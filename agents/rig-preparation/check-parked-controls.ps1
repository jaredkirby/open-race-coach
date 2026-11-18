param([string] $DataDirectory, [string] $NodePath)

$ErrorActionPreference = 'Stop'
if (-not $NodePath) { $NodePath = (Get-Command node.exe -CommandType Application).Source }
$entry = Join-Path $PSScriptRoot 'preflight.mjs'
$options = @()
if ($DataDirectory) { $options = @('--data-dir', $DataDirectory) }
Write-Host 'Park safely in neutral in your own AMS2 practice or test cockpit, with AI driving off.'
Write-Host 'The check takes about 20 seconds in the cockpit. One beep means wheel, two mean throttle, three mean brake.'
Write-Host 'A successful check can save your bindings for automatic recovery if the file goes missing.'
$answer = Read-Host 'Run the parked check and enable recovery on success? [y/N]'
if ($answer -notin 'y','yes') { return }
$devices = & $NodePath $entry list-controllers @options | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw 'Unable to start controller discovery.' }
$selection = @()
if ($devices.controllers.Count -gt 0) {
    $devices.controllers | Select-Object id, name | Format-Table -AutoSize | Out-Host
    $wheel = Read-Host 'Wheel device number (Enter for the configured wheel)'
    $pedals = Read-Host 'Separate USB pedal device number (Enter for pedals on the wheel)'
    foreach ($value in @($wheel,$pedals)) {
        if ($value -and ($value -notmatch '^(?:[0-9]|1[0-5])$' -or [int]$value -notin $devices.controllers.id)) {
            throw 'Select a listed device number, or leave it blank.'
        }
    }
    if ($wheel) { $selection += @('--controller-id',$wheel) }
    if ($pedals) { $selection += @('--pedal-controller-id',$pedals) }
}
& $NodePath $entry check-inputs --parked-driver --enable-repair @options @selection
if ($LASTEXITCODE -ne 0) { throw 'The parked check could not finish.' }
[void](Read-Host 'Press Enter to close')
