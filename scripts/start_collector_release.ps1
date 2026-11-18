#Requires -Version 7.0
param(
    [Parameter(Mandatory)][string]$Python,
    [Parameter(Mandatory)][string]$Release,
    [Parameter(Mandatory)][ValidatePattern('^[a-f0-9]{64}$')][string]$ReleaseId,
    [Parameter(Mandatory)][string]$Attempt
)
$ErrorActionPreference = 'Stop'
$pythonPath = (Resolve-Path -LiteralPath $Python).Path
$releasePath = (Resolve-Path -LiteralPath $Release).Path
$attemptPath = [System.IO.Path]::GetFullPath($Attempt)
if ($attemptPath.Equals($releasePath, [System.StringComparison]::OrdinalIgnoreCase) -or
    $attemptPath.StartsWith($releasePath.TrimEnd('\') + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
    throw 'Launch artifacts must be outside the selected source snapshot.'
}
if (Test-Path -LiteralPath $attemptPath) { throw 'Attempt already exists; reconcile it, do not retry.' }
# Each wrapper invocation also gets a fresh evidence directory. Never overwrite
# old logs, even when Python did not get far enough to create its own receipt.
$logs = $attemptPath + '.launcher'
New-Item -ItemType Directory -Path $logs -ErrorAction Stop | Out-Null
$bootstrap = 'import sys;sys.path.insert(0,sys.argv.pop(1));from simcoach.cli.collector_release import main;raise SystemExit(main())'
$arguments = @('-I', '-B', '-u', '-c', $bootstrap, $releasePath, 'run',
    '--release-id', $ReleaseId, '--attempt', $attemptPath)
[ordered]@{schema_version=1;python=$pythonPath;arguments=$arguments;time=(Get-Date -Format o);
    automatic_retry=$false;actual_process_receipt=(Join-Path $attemptPath 'launch.json')
} | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $logs 'command.json')
$info = [System.Diagnostics.ProcessStartInfo]::new()
$info.FileName = $pythonPath
$info.WorkingDirectory = $releasePath
$info.UseShellExecute = $false
$info.CreateNoWindow = $true
$info.RedirectStandardInput = $true
$info.RedirectStandardOutput = $true
$info.RedirectStandardError = $true
foreach ($argument in $arguments) { $info.ArgumentList.Add($argument) }
$process = [System.Diagnostics.Process]::new()
$process.StartInfo = $info
$stdoutFile = [System.IO.File]::Open((Join-Path $logs 'stdout.log'), 'CreateNew', 'Write', 'Read')
$stderrFile = [System.IO.File]::Open((Join-Path $logs 'stderr.log'), 'CreateNew', 'Write', 'Read')
try {
    [void]$process.Start()
    $process.StandardInput.Close()
    $stdoutCopy = $process.StandardOutput.BaseStream.CopyToAsync($stdoutFile)
    $stderrCopy = $process.StandardError.BaseStream.CopyToAsync($stderrFile)
    # Wait on this handle only. An observation problem never triggers a retry.
    $process.WaitForExit()
    $stdoutCopy.GetAwaiter().GetResult()
    $stderrCopy.GetAwaiter().GetResult()
    $exitCode = $process.ExitCode
    [ordered]@{schema_version=1;wrapper_exit_code=$exitCode;time=(Get-Date -Format o);
        actual_collector_closure='inspect launch.json and exact archive artifacts';automatic_retry=$false
    } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $logs 'exit.json')
} catch {
    [ordered]@{schema_version=1;error=$_.ToString();time=(Get-Date -Format o);
        automatic_retry=$false;actual_collector_state='unknown; reconcile native identity'
    } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $logs 'failure.json')
    throw
} finally {
    $stdoutFile.Dispose()
    $stderrFile.Dispose()
    $process.Dispose()
}
exit $exitCode
