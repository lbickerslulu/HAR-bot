param(
    [int]$DaysBack = 7,
    [string]$EventsOutputPath = ".\hardware_events.json",
    [string]$SummaryOutputPath = ".\summary.txt",
    [string]$ResultOutputPath = ".\result.json"
)

$ErrorActionPreference = "Stop"

$scriptRoot = Split-Path -Path $MyInvocation.MyCommand.Path -Parent
$collectorPath = Join-Path -Path $scriptRoot -ChildPath "collect.ps1"
$analyzerPath = Join-Path -Path $scriptRoot -ChildPath "analyze.py"

if (-not (Test-Path -Path $collectorPath)) {
    throw "Collector script not found: $collectorPath"
}

if (-not (Test-Path -Path $analyzerPath)) {
    throw "Analyzer script not found: $analyzerPath"
}

Write-Host "Step 1/3: Collecting hardware events..."
& $collectorPath -DaysBack $DaysBack -OutputPath $EventsOutputPath
if (-not $?) {
    throw "Collection step failed."
}

Write-Host "Step 2/3: Running analyzer..."
$pyLauncher = Get-Command py -ErrorAction SilentlyContinue
if ($pyLauncher) {
    & py -3 $analyzerPath --input $EventsOutputPath --summary $SummaryOutputPath --result $ResultOutputPath
} else {
    & python $analyzerPath --input $EventsOutputPath --summary $SummaryOutputPath --result $ResultOutputPath
}
if (-not $?) {
    throw "Analyzer step failed."
}

Write-Host "Step 3/3: Printing summary..."
if (Test-Path -Path $SummaryOutputPath) {
    Get-Content -Path $SummaryOutputPath
} else {
    Write-Warning "Summary file not found: $SummaryOutputPath"
}

Write-Host "Pipeline complete."
