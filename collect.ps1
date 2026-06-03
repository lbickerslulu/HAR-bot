param(
    [int]$DaysBack = 7,
    [string]$OutputPath = ".\hardware_events.json"
)

$ErrorActionPreference = "Stop"

$eventIDs = @(
    41, 6008, 1001,
    219, 225, 410, 411,
    17, 18, 19,
    7, 51, 129, 153,
    37, 55, 13,
    10110, 10111, 7026
)

$startTime = (Get-Date).AddDays(-$DaysBack)

$events = Get-WinEvent -FilterHashtable @{
    LogName   = "System"
    ID        = $eventIDs
    StartTime = $startTime
} -ErrorAction Stop

$structured = $events | ForEach-Object {
    $firstLine = ""
    if ($_.Message) {
        $firstLine = ($_.Message -split "`r?`n")[0]
    }

    [PSCustomObject]@{
        TimeCreated = $_.TimeCreated
        EventID     = $_.Id
        Source      = $_.ProviderName
        Level       = $_.LevelDisplayName
        Message     = $firstLine
    }
}

$targetDirectory = Split-Path -Path $OutputPath -Parent
if ($targetDirectory -and -not (Test-Path -Path $targetDirectory)) {
    New-Item -ItemType Directory -Path $targetDirectory -Force | Out-Null
}

$structured | ConvertTo-Json -Depth 3 | Out-File -FilePath $OutputPath -Encoding utf8

Write-Host "Events exported to $OutputPath"
Write-Host "Collected $($structured.Count) matching events from the last $DaysBack day(s)."
