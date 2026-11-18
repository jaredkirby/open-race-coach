[CmdletBinding(SupportsShouldProcess)]
param(
    [string] $NodePath,
    [string] $DataDirectory,
    [string] $PreviousRelease,
    [string] $TargetRelease
)

$ErrorActionPreference = 'Stop'
$taskName = 'Open Race Coach Idle Rig Preflight'
if ($TargetRelease -and -not $PreviousRelease) {
    throw 'TargetRelease requires PreviousRelease.'
}
if ($PreviousRelease -and -not $DataDirectory) {
    throw 'An update requires the existing explicit DataDirectory.'
}

function Get-Release([string] $Path) {
    $release = Get-Item -LiteralPath $Path
    if (-not $release.PSIsContainer) { throw 'The release must be a directory.' }
    foreach ($file in @('invoke-preflight.ps1', 'preflight.mjs')) {
        if (-not (Test-Path -LiteralPath (Join-Path $release.FullName $file) -PathType Leaf)) {
            throw 'The release must contain invoke-preflight.ps1 and preflight.mjs.'
        }
    }
    return $release.FullName
}

$requestedRelease = $PSScriptRoot
if ($TargetRelease) { $requestedRelease = $TargetRelease }
$release = Get-Release $requestedRelease
$launcher = Join-Path $release 'invoke-preflight.ps1'
if (-not $NodePath) {
    $NodePath = (Get-Command node.exe -CommandType Application | Select-Object -First 1).Source
}
$node = Get-Item -LiteralPath $NodePath
if ($node.PSIsContainer -or $node.Name -ine 'node.exe') {
    throw 'NodePath must point to node.exe.'
}

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$sid = $identity.User.Value
$system = [Environment]::GetFolderPath('System')
$powershell = Join-Path $system 'WindowsPowerShell\v1.0\powershell.exe'

# These are resolved Windows file paths. Double quotes are not valid path characters.
$dataArgument = ''
if ($DataDirectory) {
    $data = Get-Item -LiteralPath $DataDirectory
    if (-not $data.PSIsContainer) { throw 'DataDirectory must be a directory.' }
    $dataArgument = ' -DataDirectory "{0}"' -f (Join-Path $data.FullName '.')
}

function Get-LauncherArguments([string] $Path) {
    return ('-WindowStyle Hidden -NoProfile -NonInteractive -File "{0}" -NodePath "{1}"' -f $Path, $node.FullName) + $dataArgument
}

function Test-ChildNames($Element, [string[]] $Allowed) {
    foreach ($child in $Element.ChildNodes) {
        if ($child.NodeType -ne 'Element' -or $child.NamespaceURI -ne $Element.NamespaceURI -or $child.LocalName -notin $Allowed) {
            return $false
        }
    }
    return $true
}

function Test-CurrentUser([string] $UserId) {
    if ($UserId -eq $sid) { return $true }
    if ($UserId -ine $identity.Name) { return $false }
    try {
        $account = [Security.Principal.NTAccount]::new($UserId)
        return $account.Translate([Security.Principal.SecurityIdentifier]).Value -eq $sid
    } catch {
        return $false
    }
}

function Assert-ExpectedTask([xml] $Xml, [string] $ReleaseDirectory) {
    $task = $Xml.Task
    $actions = @($task.Actions.ChildNodes)
    $principals = @($task.Principals.ChildNodes)
    if ($actions.Count -ne 1 -or $actions[0].LocalName -ne 'Exec' -or
        -not (Test-ChildNames $actions[0] @('Command', 'Arguments', 'WorkingDirectory')) -or
        $actions[0].Command -cne $powershell -or
        $actions[0].Arguments -cne (Get-LauncherArguments (Join-Path $ReleaseDirectory 'invoke-preflight.ps1')) -or
        $actions[0].WorkingDirectory -cne $ReleaseDirectory) {
        throw 'The existing task does not match the supplied release, node.exe and data directory.'
    }
    if ($principals.Count -ne 1 -or $principals[0].LocalName -ne 'Principal' -or
        -not (Test-ChildNames $principals[0] @('UserId', 'LogonType', 'RunLevel')) -or
        $principals[0].UserId -ne $sid -or $principals[0].LogonType -ne 'InteractiveToken' -or
        $principals[0].RunLevel -notin @($null, 'LeastPrivilege') -or
        $principals[0].id -ne 'CurrentUser' -or $task.Actions.Context -ne 'CurrentUser') {
        throw 'The existing task does not have the expected current-user interactive principal.'
    }
    $triggers = @($task.Triggers.ChildNodes)
    $logon = $task.Triggers.LogonTrigger
    $time = $task.Triggers.TimeTrigger
    if ($triggers.Count -ne 2 -or -not $logon -or -not $time -or
        -not (Test-ChildNames $logon @('UserId', 'Enabled')) -or -not (Test-CurrentUser $logon.UserId) -or
        -not (Test-ChildNames $time @('StartBoundary', 'Enabled', 'Repetition')) -or
        -not $time.StartBoundary -or -not $time.Repetition -or
        -not (Test-ChildNames $time.Repetition @('Interval', 'StopAtDurationEnd')) -or
        $time.Repetition.Interval -ne 'PT15M' -or $time.Repetition.StopAtDurationEnd -notin @($null, 'false')) {
        throw 'The existing task does not have the expected logon and 15-minute triggers.'
    }
}

$arguments = Get-LauncherArguments $launcher
$description = 'Run the read-only Open Race Coach idle rig preflight.'
if (Test-Path -LiteralPath (Join-Path $release 'profile-repair.mjs') -PathType Leaf) {
    $description = 'Run the Open Race Coach idle rig preflight and verify safe missing-profile repairs.'
}

if ($PreviousRelease) {
    $previous = Get-Release $PreviousRelease
    if ($previous -ieq $release) { throw 'The previous and target releases must be different.' }
    $scheduler = New-Object -ComObject 'Schedule.Service'
    $scheduler.Connect()
    $folder = $scheduler.GetFolder('\')
    $existing = $folder.GetTask($taskName)
    $original = $existing.Xml
    $securityInformation = 7 # OWNER_SECURITY_INFORMATION | GROUP_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION
    $permissions = $existing.GetSecurityDescriptor($securityInformation)
    [xml] $updated = $original
    Assert-ExpectedTask $updated $previous

    $updated.Task.RegistrationInfo['Description'].InnerText = $description
    $updated.Task.Actions.Exec['Arguments'].InnerText = $arguments
    $updated.Task.Actions.Exec['WorkingDirectory'].InnerText = $release
    Assert-ExpectedTask $updated $release

    if ($PSCmdlet.ShouldProcess($taskName, "Update idle preflight release from '$previous' to '$release'")) {
        $latest = $folder.GetTask($taskName)
        if ($latest.Xml -cne $original -or $latest.GetSecurityDescriptor($securityInformation) -cne $permissions) {
            throw 'The existing task changed during the update. Review it before retrying.'
        }
        # Retain the task ACL and suppress registration-trigger side effects on update.
        $updateOnly = 4 -bor 16 -bor 32 # TASK_UPDATE | TASK_DONT_ADD_PRINCIPAL_ACE | TASK_IGNORE_REGISTRATION_TRIGGERS
        $interactiveToken = 3
        $null = $folder.RegisterTask($taskName, $updated.OuterXml, $updateOnly, $sid, $null, $interactiveToken, $null)
        Write-Output ([pscustomobject]@{
            TaskName = $taskName
            PreviousRelease = $previous
            TargetRelease = $release
            NodePath = $node.FullName
            DataDirectory = $data.FullName
        })
    }
    return
}

$start = (Get-Date).AddMinutes(15).ToString('yyyy-MM-ddTHH:mm:ss', [Globalization.CultureInfo]::InvariantCulture)
$xmlArguments = [Security.SecurityElement]::Escape($arguments)
$command = [Security.SecurityElement]::Escape($powershell)
$directory = [Security.SecurityElement]::Escape($release)
$taskXml = @"
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>$description</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger><UserId>$sid</UserId></LogonTrigger>
    <TimeTrigger>
      <Repetition><Interval>PT15M</Interval></Repetition>
      <StartBoundary>$start</StartBoundary>
    </TimeTrigger>
  </Triggers>
  <Principals>
    <Principal id="CurrentUser">
      <UserId>$sid</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>PT3M</ExecutionTimeLimit>
    <WakeToRun>false</WakeToRun>
  </Settings>
  <Actions Context="CurrentUser">
    <Exec>
      <Command>$command</Command>
      <Arguments>$xmlArguments</Arguments>
      <WorkingDirectory>$directory</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"@

if ($PSCmdlet.ShouldProcess($taskName, 'Register current-user idle preflight task')) {
    $scheduler = New-Object -ComObject 'Schedule.Service'
    $scheduler.Connect()
    $folder = $scheduler.GetFolder('\')
    $createOnly = 2 # TASK_CREATE
    $interactiveToken = 3 # TASK_LOGON_INTERACTIVE_TOKEN
    $null = $folder.RegisterTask($taskName, $taskXml, $createOnly, $sid, $null, $interactiveToken, $null)
    Write-Output "Registered $taskName"
}
