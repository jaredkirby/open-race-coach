$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

try {
    # Use the existing Windows PowerShell Start menu registration. Do not create a shortcut or change notification settings.
    $appId = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
    if (-not (Get-StartApps | Where-Object AppID -EQ $appId)) { 'unavailable'; exit }
    $null = [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime]
    $null = [Windows.UI.Notifications.ToastNotifier, Windows.UI.Notifications, ContentType = WindowsRuntime]
    $null = [Windows.UI.Notifications.NotificationSetting, Windows.UI.Notifications, ContentType = WindowsRuntime]
    $null = [Windows.UI.Notifications.ToastNotification, Windows.UI.Notifications, ContentType = WindowsRuntime]
    $null = [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime]
    $notifier = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($appId)
    try {
        # The property adapter can silently return null when the underlying getter fails.
        $setting = [Windows.UI.Notifications.ToastNotifier].GetProperty('Setting').GetValue($notifier)
        if ($setting.ToString() -ne 'Enabled') { 'unavailable'; exit }
    } catch {
        # A desktop shortcut without a per-app setting reports ERROR_NOT_FOUND. Windows still applies its own settings in Show().
        if ($_.Exception.GetBaseException().HResult -ne -2147023728) { throw } # 0x80070490
    }

    $body = [Security.SecurityElement]::Escape($env:ORC_PREFLIGHT_NOTICE_BODY)
    if ([string]::IsNullOrEmpty($body)) { 'unavailable'; exit }
    $xml = [Windows.Data.Xml.Dom.XmlDocument]::new()
    $xml.LoadXml("<toast><visual><binding template=`"ToastGeneric`"><text>Open Race Coach preflight</text><text>$body</text></binding></visual><audio silent=`"true`"/></toast>")
    $toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
    $toast.SuppressPopup = $true
    if (-not $toast.SuppressPopup) { 'unavailable'; exit }
    $toast.ExpirationTime = [DateTimeOffset]::Now.AddHours(24)
} catch {
    'unavailable'
    exit
}

try {
    $notifier.Show($toast)
    'submitted'
} catch {
    'ambiguous'
}
