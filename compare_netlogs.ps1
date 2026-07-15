param(
    [Parameter(Mandatory = $true)]
    [string]$FirstNetLogPath,

    [Parameter(Mandatory = $true)]
    [string]$SecondNetLogPath,

    [string]$FirstLabel = "Capture A",
    [string]$SecondLabel = "Capture B",
    [int]$Limit = 25,
    [string]$OutputPath = ".\netlog_compare.json"
)

$ErrorActionPreference = "Stop"

$scriptRoot = Split-Path -Path $MyInvocation.MyCommand.Path -Parent
$viewerPath = Join-Path -Path $scriptRoot -ChildPath "netlog_viewer.py"

if (-not (Test-Path -Path $viewerPath)) {
    throw "NetLog viewer not found: $viewerPath"
}

if (-not (Test-Path -Path $FirstNetLogPath)) {
    throw "First NetLog file not found: $FirstNetLogPath"
}

if (-not (Test-Path -Path $SecondNetLogPath)) {
    throw "Second NetLog file not found: $SecondNetLogPath"
}

$tempFirst = Join-Path -Path $env:TEMP -ChildPath ("netlog_first_" + [guid]::NewGuid().ToString("N") + ".json")
$tempSecond = Join-Path -Path $env:TEMP -ChildPath ("netlog_second_" + [guid]::NewGuid().ToString("N") + ".json")

function Invoke-NetLogViewer {
    param(
        [string]$InputPath,
        [string]$SummaryPath
    )

    $arguments = @(
        ".\netlog_viewer.py",
        $InputPath,
        "--limit", $Limit,
        "--json-output", $SummaryPath
    )

    Push-Location $scriptRoot
    try {
        py -3 @arguments
        if ($LASTEXITCODE -ne 0) {
            throw "netlog_viewer.py failed for: $InputPath"
        }
    }
    finally {
        Pop-Location
    }

    return (Get-Content -Path $SummaryPath -Raw | ConvertFrom-Json)
}

function Get-CounterFromObject {
    param([object]$ObjectValue)

    $counter = @{}
    if ($null -eq $ObjectValue) {
        return $counter
    }

    foreach ($prop in $ObjectValue.PSObject.Properties) {
        $counter[$prop.Name] = [int]$prop.Value
    }

    return $counter
}

function Get-TopDifferenceRows {
    param(
        [hashtable]$FirstCounts,
        [hashtable]$SecondCounts,
        [int]$Top = 8
    )

    $allKeys = New-Object System.Collections.Generic.HashSet[string]
    foreach ($key in $FirstCounts.Keys) { [void]$allKeys.Add($key) }
    foreach ($key in $SecondCounts.Keys) { [void]$allKeys.Add($key) }

    $rows = foreach ($key in $allKeys) {
        $firstValue = 0
        $secondValue = 0

        if ($FirstCounts.ContainsKey($key)) { $firstValue = [int]$FirstCounts[$key] }
        if ($SecondCounts.ContainsKey($key)) { $secondValue = [int]$SecondCounts[$key] }

        [pscustomobject]@{
            Error = $key
            First = $firstValue
            Second = $secondValue
            Delta = ($firstValue - $secondValue)
            AbsDelta = [Math]::Abs($firstValue - $secondValue)
        }
    }

    return $rows |
        Sort-Object -Property @{Expression = "AbsDelta"; Descending = $true}, @{Expression = "Error"; Descending = $false} |
        Select-Object -First $Top
}

function Resolve-OverallRecommendation {
    param(
        [int]$FirstScore,
        [int]$SecondScore,
        [string]$FirstLikelihood,
        [string]$SecondLikelihood,
        [string]$FirstLabel,
        [string]$SecondLabel
    )

    $scoreGap = [Math]::Abs($FirstScore - $SecondScore)

    if ($FirstScore -ge 65 -and $SecondScore -lt 40 -and $scoreGap -ge 20) {
        return "$FirstLabel looks browser-local compared to $SecondLabel; prioritize reset/reinstall for $FirstLabel environment."
    }

    if ($SecondScore -ge 65 -and $FirstScore -lt 40 -and $scoreGap -ge 20) {
        return "$SecondLabel looks browser-local compared to $FirstLabel; prioritize reset/reinstall for $SecondLabel environment."
    }

    if ($FirstScore -ge 40 -and $SecondScore -ge 40) {
        return "Both captures show meaningful local signatures. Run clean-profile tests before reinstalling either browser."
    }

    if ($FirstScore -lt 40 -and $SecondScore -lt 40) {
        return "Neither capture strongly suggests browser reinstall. Focus on shared network, DNS, proxy, or upstream service factors."
    }

    return "Mixed signal. Validate with one controlled repro per browser using clean profiles and no extensions."
}

try {
    Write-Host "`n===== NetLog Comparison =====" -ForegroundColor Cyan
    Write-Host "First:  $FirstLabel -> $FirstNetLogPath"
    Write-Host "Second: $SecondLabel -> $SecondNetLogPath"

    $firstSummary = Invoke-NetLogViewer -InputPath $FirstNetLogPath -SummaryPath $tempFirst
    $secondSummary = Invoke-NetLogViewer -InputPath $SecondNetLogPath -SummaryPath $tempSecond

    $firstAssessment = $firstSummary.assessment
    $secondAssessment = $secondSummary.assessment

    $firstErrors = Get-CounterFromObject -ObjectValue $firstSummary.error_counts
    $secondErrors = Get-CounterFromObject -ObjectValue $secondSummary.error_counts

    $topDiff = Get-TopDifferenceRows -FirstCounts $firstErrors -SecondCounts $secondErrors -Top 10

    $firstAffected = @($firstSummary.affected_hosts)
    $secondAffected = @($secondSummary.affected_hosts)

    $commonFailingTargets = @($firstAffected | Where-Object { $secondAffected -contains $_ })

    $overall = Resolve-OverallRecommendation `
        -FirstScore ([int]$firstAssessment.score) `
        -SecondScore ([int]$secondAssessment.score) `
        -FirstLikelihood ([string]$firstAssessment.likelihood) `
        -SecondLikelihood ([string]$secondAssessment.likelihood) `
        -FirstLabel $FirstLabel `
        -SecondLabel $SecondLabel

    Write-Host ""
    Write-Host "--- Side-by-Side ---" -ForegroundColor Yellow
    Write-Host "$FirstLabel"
    Write-Host "  Browser Hint: $($firstSummary.browser_hint)"
    Write-Host "  Score: $($firstAssessment.score)/100"
    Write-Host "  Likelihood: $($firstAssessment.likelihood)"
    Write-Host "  Failures: $($firstAssessment.total_failures)"
    Write-Host "  Affected Hosts: $($firstAssessment.affected_host_count)"

    Write-Host ""
    Write-Host "$SecondLabel"
    Write-Host "  Browser Hint: $($secondSummary.browser_hint)"
    Write-Host "  Score: $($secondAssessment.score)/100"
    Write-Host "  Likelihood: $($secondAssessment.likelihood)"
    Write-Host "  Failures: $($secondAssessment.total_failures)"
    Write-Host "  Affected Hosts: $($secondAssessment.affected_host_count)"

    Write-Host ""
    Write-Host "--- Error Delta (Top 10 by absolute difference) ---" -ForegroundColor Yellow
    if ($topDiff.Count -eq 0) {
        Write-Host "  No error differences found"
    }
    else {
        foreach ($row in $topDiff) {
            Write-Host "  $($row.Error): $FirstLabel=$($row.First), $SecondLabel=$($row.Second), delta=$($row.Delta)"
        }
    }

    Write-Host ""
    Write-Host "--- Host Overlap ---" -ForegroundColor Yellow
    Write-Host "  Shared failing hosts: $($commonFailingTargets.Count)"
    if ($commonFailingTargets.Count -gt 0) {
        foreach ($targetName in ($commonFailingTargets | Sort-Object | Select-Object -First 15)) {
            Write-Host "  - $targetName"
        }
    }

    Write-Host ""
    Write-Host "--- Overall Recommendation ---" -ForegroundColor Green
    Write-Host "  $overall"

    $result = [pscustomobject]@{
        generated_at = (Get-Date).ToString("o")
        first_label = $FirstLabel
        second_label = $SecondLabel
        first_file = (Resolve-Path -Path $FirstNetLogPath).Path
        second_file = (Resolve-Path -Path $SecondNetLogPath).Path
        first = $firstSummary
        second = $secondSummary
        shared_failing_hosts = ($commonFailingTargets | Sort-Object -Unique)
        overall_recommendation = $overall
        error_delta = @($topDiff | ForEach-Object {
            [pscustomobject]@{
                error = $_.Error
                first = $_.First
                second = $_.Second
                delta = $_.Delta
            }
        })
    }

    $result | ConvertTo-Json -Depth 8 | Set-Content -Path $OutputPath -Encoding UTF8
    Write-Host ""
    Write-Host "Comparison JSON written to: $OutputPath" -ForegroundColor Green
}
finally {
    Remove-Item -Path $tempFirst -ErrorAction SilentlyContinue
    Remove-Item -Path $tempSecond -ErrorAction SilentlyContinue
}
