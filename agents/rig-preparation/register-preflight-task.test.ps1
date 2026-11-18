$ErrorActionPreference = 'Stop'
$global:OrcTaskSchedulerMock = $mock = [pscustomobject]@{
    registrationCalls = 0
    taskXml = $null
    getTaskCount = 0
    changePermissionsOnReread = $false
    taskPermissions = 'mock owner, group and permissions'
}
$register = Join-Path $PSScriptRoot 'register-preflight-task.ps1'
$node = (Get-Command node.exe -CommandType Application | Select-Object -First 1).Source
$fixture = Join-Path ([IO.Path]::GetTempPath()) "orc-task-test-$([guid]::NewGuid())"
$null = New-Item -Path $fixture -ItemType Directory
# Match the canonical spelling used by registration's Get-Release. Windows
# temporary-directory providers can return different casing for the same path.
$fixture = (Get-Item -LiteralPath $fixture).FullName

function New-Object([string] $ComObject) {
    if ($ComObject -ne 'Schedule.Service') { throw "Unexpected COM object: $ComObject" }
    $service = [pscustomobject]@{ Mock = $global:OrcTaskSchedulerMock }
    $service | Add-Member ScriptMethod Connect {}
    $service | Add-Member ScriptMethod GetFolder {
        param($path)
        if ($path -ne '\') { throw 'Unexpected task folder.' }
        $folder = [pscustomobject]@{ Mock = $this.Mock }
        $folder | Add-Member ScriptMethod GetTask {
            param($name)
            if (-not $this.Mock.taskXml) { throw 'Task is missing.' }
            $this.Mock.getTaskCount++
            if ($this.Mock.changePermissionsOnReread -and $this.Mock.getTaskCount -eq 2) {
                $this.Mock.taskPermissions = 'changed ACL'
            }
            $task = [pscustomobject]@{ Xml = $this.Mock.taskXml; Permissions = $this.Mock.taskPermissions }
            $task | Add-Member ScriptMethod GetSecurityDescriptor {
                param($information)
                if ($information -ne 7) { throw 'Unexpected security information request.' }
                return $this.Permissions
            }
            return $task
        }
        $folder | Add-Member ScriptMethod RegisterTask {
            param($name, $xml, $flags, $sid, $password, $logonType, $sddl)
            $expectedFlags = 2
            if ($this.Mock.taskXml) { $expectedFlags = 52 }
            if ($flags -ne $expectedFlags -or $logonType -ne 3 -or $password -or $sddl) {
                throw 'Unexpected registration or permission flags.'
            }
            $this.Mock.taskXml = $xml
            $this.Mock.registrationCalls++
        }
        return $folder
    }
    return $service
}

function Assert([bool] $Condition, [string] $Message) {
    if (-not $Condition) { throw $Message }
}

function Assert-Refused([hashtable] $Arguments, [string] $Message) {
    try {
        $null = & $register @Arguments
    } catch {
        if ($_.Exception.Message -like "*$Message*") { return }
        throw
    }
    throw "The update should have refused: $Message"
}

try {
    $old = Join-Path $fixture 'previous'
    $data = Join-Path $fixture 'private data'
    $other = Join-Path $fixture 'another data directory'
    $null = New-Item -Path $old, $data, $other -ItemType Directory
    foreach ($file in @('invoke-preflight.ps1', 'preflight.mjs')) {
        $null = New-Item -Path (Join-Path $old $file) -ItemType File
    }
    $null = & $register -NodePath $node -DataDirectory $data
    [xml] $oldTask = $mock.taskXml
    $oldTask.Task.Actions.Exec['Arguments'].InnerText = $oldTask.Task.Actions.Exec.Arguments.Replace(
        (Join-Path $PSScriptRoot 'invoke-preflight.ps1'), (Join-Path $old 'invoke-preflight.ps1'))
    $oldTask.Task.Actions.Exec['WorkingDirectory'].InnerText = $old
    $oldTask.Task.RegistrationInfo['Description'].InnerText = 'Run the read-only Open Race Coach idle rig preflight.'
    $oldTask.Task.Settings.AppendChild($oldTask.CreateElement('Enabled', $oldTask.Task.NamespaceURI)).InnerText = 'false'
    $oldTask.Task.Triggers.TimeTrigger.StartBoundary = '2025-01-01T14:30:00'
    $mock.taskXml = $oldTask.OuterXml
    $before = $mock.taskXml

    $parameters = @{ PreviousRelease = $old; NodePath = $node; DataDirectory = $data }
    $null = & $register @parameters -WhatIf
    Assert ($mock.registrationCalls -eq 1 -and $mock.taskXml -ceq $before) 'WhatIf changed the task.'
    Assert-Refused (@{ PreviousRelease = $old; NodePath = $node; DataDirectory = $other }) 'does not match'

    $oldTask.Task.Triggers.TimeTrigger.Repetition.Interval = 'PT2M'
    $mock.taskXml = $oldTask.OuterXml
    Assert-Refused $parameters 'expected logon and 15-minute triggers'
    $oldTask.Task.Triggers.TimeTrigger.Repetition.Interval = 'PT15M'
    $oldTask.Task.Principals.Principal.RunLevel = 'HighestAvailable'
    $mock.taskXml = $oldTask.OuterXml
    Assert-Refused $parameters 'expected current-user interactive principal'
    $mock.taskXml = $before

    [xml] $normalized = $before
    $normalizedPrincipal = $normalized.Task.Principals.Principal
    $null = $normalizedPrincipal.RemoveChild($normalizedPrincipal['RunLevel'])
    $normalized.Task.Triggers.LogonTrigger['UserId'].InnerText = 'S-1-5-18'
    $mock.taskXml = $normalized.OuterXml
    Assert-Refused $parameters 'expected logon and 15-minute triggers'
    $normalized.Task.Triggers.LogonTrigger['UserId'].InnerText = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    $mock.taskXml = $normalized.OuterXml
    $null = & $register @parameters -WhatIf
    Assert ($mock.registrationCalls -eq 1) 'The normalized WhatIf changed the task.'
    $mock.taskXml = $before

    $mock.getTaskCount = 0
    $mock.changePermissionsOnReread = $true
    Assert-Refused $parameters 'changed during the update'
    $mock.changePermissionsOnReread = $false
    $mock.taskPermissions = 'mock owner, group and permissions'
    Assert ($mock.registrationCalls -eq 1) 'A refused update changed the task.'

    $result = & $register @parameters
    [xml] $newTask = $mock.taskXml
    [xml] $originalTask = $before
    foreach ($section in @('Triggers', 'Principals', 'Settings')) {
        Assert ($originalTask.Task[$section].OuterXml -ceq $newTask.Task[$section].OuterXml) "$section was changed."
    }
    Assert ($result.PreviousRelease -ceq $old -and $result.TargetRelease -ceq $PSScriptRoot) 'Rollback paths are wrong.'
    Assert ($newTask.Task.RegistrationInfo.Description -like '*repairs*') 'New release description was not updated.'

    $null = & $register -PreviousRelease $PSScriptRoot -TargetRelease $old -NodePath $node -DataDirectory $data
    Assert ($mock.taskXml -ceq $before) 'Rollback did not restore the original task.'
    Assert ($mock.registrationCalls -eq 3) 'Unexpected registration count.'
    Write-Output 'Scheduled task creation, guarded update, WhatIf, refusal and rollback passed with Task Scheduler mocked.'
} finally {
    Remove-Variable OrcTaskSchedulerMock -Scope Global
    $resolved = (Resolve-Path -LiteralPath $fixture).ProviderPath
    if ($resolved -ieq $fixture) { Remove-Item -LiteralPath $resolved -Recurse -Force }
}
