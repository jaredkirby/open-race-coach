param([Parameter(Mandatory)][string] $NodePath, [string] $DataDirectory)

$ErrorActionPreference = 'Stop'
$entry = Join-Path $PSScriptRoot 'preflight.mjs'
$arguments = @($entry, 'once')
if ($DataDirectory) { $arguments += @('--data-dir', $DataDirectory) }
& $NodePath @arguments
exit $LASTEXITCODE
