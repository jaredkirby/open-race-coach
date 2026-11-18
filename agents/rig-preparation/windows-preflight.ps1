param([Parameter(Mandatory)][ValidateSet('activity','steam','moza','ams2owner')][string]$Probe)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)

function Test-LocalSteamPath([string]$Path) {
    if ($Path -notmatch '^[a-zA-Z]:[\\/]') { return $false }
    $drive = [IO.DriveInfo]::new([IO.Path]::GetPathRoot($Path))
    return $drive.DriveType -in 'Fixed', 'Removable'
}

function Read-LimitedSteamFile([string]$Path) {
    if (-not (Test-LocalSteamPath $Path)) { throw 'Steam drive is not local' }
    $file = [IO.File]::Open($Path, 'Open', 'Read', 'Read')
    try {
        if ($file.Length -gt 1048576) { throw 'Steam file exceeds read limit' }
        $bytes = [byte[]]::new([int]$file.Length)
        $read = 0
        while ($read -lt $bytes.Length) {
            $count = $file.Read($bytes, $read, $bytes.Length - $read)
            if ($count -eq 0) { throw 'Steam file changed while reading' }
            $read += $count
        }
        return [Text.UTF8Encoding]::new($false, $true).GetString($bytes).TrimStart([char]0xFEFF)
    } finally {
        $file.Dispose()
    }
}

function Read-SteamRoot([string]$Key, [string]$Value) {
    try { Get-ItemPropertyValue -LiteralPath $Key -Name $Value } catch { }
}

function Read-SteamManifest {
    $name = 'appmanifest_1066890.acf'
    $configured = $env:ORC_PREFLIGHT_STEAM_MANIFEST
    if ($configured) {
        if ([IO.Path]::GetFileName($configured) -ne $name -or -not (Test-LocalSteamPath $configured) -or
            -not (Test-Path -LiteralPath $configured -PathType Leaf)) { return @{ manifest = $null } }
        return @{ manifest = Read-LimitedSteamFile $configured }
    }

    $roots = @(
        (Read-SteamRoot 'HKCU:\Software\Valve\Steam' SteamPath)
        (Read-SteamRoot 'HKLM:\Software\WOW6432Node\Valve\Steam' InstallPath)
        (Read-SteamRoot 'HKLM:\Software\Valve\Steam' InstallPath)
        "${env:ProgramFiles(x86)}\Steam"
        "$env:ProgramFiles\Steam"
    ) | Where-Object { Test-LocalSteamPath $_ } | ForEach-Object { [IO.Path]::GetFullPath($_.TrimEnd('\', '/') + '\') }
    $libraries = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    foreach ($root in $roots) {
        [void]$libraries.Add($root)
        $folders = Join-Path $root 'steamapps\libraryfolders.vdf'
        if (Test-Path -LiteralPath $folders -PathType Leaf) {
            # Match modern Steam's quoted path entries. An explicit path also supports legacy libraries.
            $content = Read-LimitedSteamFile $folders
            foreach ($match in [regex]::Matches($content, '(?m)^\s*"path"[ \t]+"([^"\r\n]+)"[ \t]*\r?$')) {
                $path = $match.Groups[1].Value.Replace('\\', '\')
                if (Test-LocalSteamPath $path) {
                    [void]$libraries.Add([IO.Path]::GetFullPath($path.TrimEnd('\', '/') + '\'))
                }
            }
        }
    }
    $manifests = @(foreach ($library in $libraries) {
        $path = Join-Path $library "steamapps\$name"
        if (Test-Path -LiteralPath $path -PathType Leaf) { $path }
    })
    if ($manifests.Count -ne 1) { return @{ manifest = $null } }
    return @{ manifest = Read-LimitedSteamFile $manifests[0] }
}

function Read-MozaPresence {
    # Windows PowerShell returns a JSON array as a single pipeline object.
    $ids = ConvertFrom-Json -InputObject $env:ORC_PREFLIGHT_MOZA_IDS
    if ($ids.Count -eq 0) { throw 'Expected PnP devices are missing' }
    # One enumeration yields a coherent Windows-layer snapshot. Do not return instance identifiers.
    $devices = @(Get-PnpDevice -PresentOnly -ErrorAction Stop)
    $states = @(foreach ($id in $ids) {
        $matches = @($devices | Where-Object InstanceId -EQ $id)
        if ($matches.Count -eq 0) { 'missing' }
        elseif ($matches.Count -ne 1) { 'unknown' }
        else {
            # Read the CIM value before the module's enum projection, which can coerce null to zero.
            $errorCode = $matches[0].PSBase.CimInstanceProperties['ConfigManagerErrorCode'].Value
            if ($matches[0].Status -eq 'OK' -and $null -ne $errorCode -and $errorCode -eq 0) { 'present' }
            elseif ($matches[0].Status -in 'Error', 'Degraded' -or $errorCode -gt 0) { 'problem' }
            else { 'unknown' }
        }
    })
    return @{ deviceStates = $states }
}

function Get-RacingSimulators {
    $names = @(
        'AMS2', 'AMS2AVX', 'acs', 'acs_x86', 'AC2-Win64-Shipping', 'ACC',
        'rFactor2', 'iRacingSim64DX11', 'Le Mans Ultimate', 'RRRE', 'RRRE64',
        'pCARS', 'pCARS64', 'pCARS2', 'pCARS2AVX', 'pCARS3', 'pCARS3AVX'
    )
    Get-Process | Where-Object { $_.ProcessName -in $names }
}

function Read-Ams2Owner {
    $games = @(Get-RacingSimulators)
    if ($games.Count -ne 1 -or $games[0].ProcessName -notin 'AMS2','AMS2AVX') {
        return @{ owner = $null }
    }
    $ticks = $games[0].StartTime.ToUniversalTime().Ticks
    return @{ owner = ('{0}-{1}' -f $games[0].Id, $ticks) }
}

function Test-RacingSimulatorRunning {
    return @(Get-RacingSimulators).Count -gt 0
}

function Read-Activity {
    if (Test-RacingSimulatorRunning) {
        return @{ simulatorRunning = $true; ams2Running = [bool](Get-Process -Name 'AMS2','AMS2AVX' -ErrorAction SilentlyContinue) }
    }

    Add-Type -TypeDefinition @'
using System;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text;

public static class OrcRigActivity
{
    [StructLayout(LayoutKind.Sequential)]
    private struct LastInput { public uint Size; public uint Time; }

    [DllImport("user32.dll")] private static extern bool GetLastInputInfo(ref LastInput info);
    [DllImport("user32.dll")] private static extern IntPtr OpenInputDesktop(uint flags, bool inherit, uint access);
    [DllImport("user32.dll")] private static extern bool CloseDesktop(IntPtr desktop);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern bool GetUserObjectInformation(IntPtr handle, int index, StringBuilder data,
        uint length, out uint needed);
    [DllImport("kernel32.dll")] private static extern uint WTSGetActiveConsoleSessionId();
    [DllImport("kernel32.dll")] private static extern ulong GetTickCount64();
    [DllImport("wtsapi32.dll", CharSet = CharSet.Unicode)]
    private static extern bool WTSQuerySessionInformation(IntPtr server, uint session, int informationClass,
        out IntPtr buffer, out uint size);
    [DllImport("wtsapi32.dll")] private static extern void WTSFreeMemory(IntPtr buffer);

    // WTSINFOEX starts with a DWORD and an 8-byte-aligned union. Only the level-1 prefix is needed.
    [StructLayout(LayoutKind.Explicit, Size = 20)]
    private struct SessionInfoPrefix {
        [FieldOffset(0)] public uint Level;
        [FieldOffset(8)] public uint SessionId;
        [FieldOffset(12)] public int State;
        [FieldOffset(16)] public int Flags;
    }

    private static bool ConsoleIsLocked(uint session)
    {
        // Windows 7 / Server 2008 R2 reverse the meanings of the lock flags.
        if (Environment.OSVersion.Version < new Version(6, 2)) return false;
        IntPtr buffer;
        uint size;
        if (!WTSQuerySessionInformation(IntPtr.Zero, session, 25, out buffer, out size) || buffer == IntPtr.Zero)
            return false;
        try {
            if (size < Marshal.SizeOf(typeof(SessionInfoPrefix))) return false;
            var info = (SessionInfoPrefix)Marshal.PtrToStructure(buffer, typeof(SessionInfoPrefix));
            return info.Level == 1 && info.SessionId == session && info.State == 0 && info.Flags == 0;
        } finally { WTSFreeMemory(buffer); }
    }

    private static bool DefaultDesktopIsActive()
    {
        IntPtr desktop = OpenInputDesktop(0, false, 1); // DESKTOP_READOBJECTS
        if (desktop == IntPtr.Zero) return false;
        try {
            var name = new StringBuilder(64);
            uint needed;
            return GetUserObjectInformation(desktop, 2, name, (uint)(name.Capacity * 2), out needed)
                && String.Equals(name.ToString(), "Default", StringComparison.OrdinalIgnoreCase);
        } finally { CloseDesktop(desktop); }
    }

    public static long IdleMilliseconds()
    {
        uint session = (uint)Process.GetCurrentProcess().SessionId;
        if (session == 0 || session != WTSGetActiveConsoleSessionId()) return -1;
        bool defaultDesktop = DefaultDesktopIsActive();
        if (!defaultDesktop && !ConsoleIsLocked(session)) return -1;
        var input = new LastInput { Size = (uint)Marshal.SizeOf(typeof(LastInput)) };
        if (!GetLastInputInfo(ref input) || session != WTSGetActiveConsoleSessionId()) return -1;
        if (defaultDesktop ? !DefaultDesktopIsActive() : !ConsoleIsLocked(session)) return -1;
        ulong uptime = GetTickCount64();
        uint elapsed = unchecked((uint)uptime - input.Time);
        // Treat out-of-order timestamps as unknown. Modulo arithmetic can conservatively undercount old input.
        return elapsed <= uptime && elapsed <= Int32.MaxValue ? (long)elapsed : -1;
    }
}
'@
    $idle = [OrcRigActivity]::IdleMilliseconds()
    if (Test-RacingSimulatorRunning) { return @{ simulatorRunning = $true } }
    return @{ simulatorRunning = $false; idleMilliseconds = $idle }
}

try {
    $result = switch ($Probe) {
        'activity' { Read-Activity }
        'ams2owner' { Read-Ams2Owner }
        'steam' { Read-SteamManifest }
        'moza' { Read-MozaPresence }
    }
    ConvertTo-Json -InputObject $result -Compress -Depth 3
} catch {
    # Native errors can contain local paths or device identifiers.
    exit 1
}
