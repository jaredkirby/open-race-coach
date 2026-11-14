param()
$ErrorActionPreference = 'Stop'
$repo = Split-Path $PSScriptRoot -Parent
$archive = Join-Path $repo '.scratch/telemetry'
$launchLogs = Join-Path $archive 'launcher-logs'
New-Item -ItemType Directory -Path $launchLogs -Force | Out-Null
$mutex = [System.Threading.Mutex]::new($false, 'Local\OpenRaceCoachContinuousTelemetry')
$owned = $false
try {
    try { $owned = $mutex.WaitOne(0) }
    catch [System.Threading.AbandonedMutexException] { $owned = $true }
    if (-not $owned) { exit 0 }
    Set-Location -LiteralPath $repo
    $env:PYTHONUTF8 = '1'
    $env:PYTHONUNBUFFERED = '1'
    $python = Join-Path $repo '.venv/Scripts/python.exe'
    while (-not (Test-Path -LiteralPath (Join-Path $archive 'disabled'))) {
        $stamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
        $stdout = Join-Path $launchLogs "$stamp.stdout.log"
        $stderr = Join-Path $launchLogs "$stamp.stderr.log"
        $arguments = @('-m', 'simcoach.cli.telemetry_watch', '--out', ('"' + $archive + '"'), '--hz', '60')
        $process = Start-Process -FilePath $python -ArgumentList $arguments -WorkingDirectory $repo `
            -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr `
            -Wait -PassThru
        $code = $process.ExitCode
        [ordered]@{
            time = (Get-Date -Format o)
            exit_code = $code
            stdout = $stdout
            stderr = $stderr
        } | ConvertTo-Json -Compress | Add-Content -LiteralPath (Join-Path $launchLogs 'exits.jsonl')
        if ($code -eq 0) { break }
        Start-Sleep -Seconds 5
    }
}
finally {
    if ($owned) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}
