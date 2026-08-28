param(
    [Parameter(Mandatory = $true)]
    [string]$NetLogPath,

    [Parameter(Mandatory = $true)]
    [string]$HarPath,

    [string]$NetLogLabel = "NetLog",
    [string]$HarLabel = "HAR",
    [int]$Limit = 25,
    [string]$OutputPath = ".\netlog_har_compare.json"
)

$ErrorActionPreference = "Stop"

$scriptRoot = Split-Path -Path $MyInvocation.MyCommand.Path -Parent
$viewerPath = Join-Path -Path $scriptRoot -ChildPath "netlog_viewer.py"
$harAnalyzerPath = Join-Path -Path $scriptRoot -ChildPath "har_analyzer.py"

if (-not (Test-Path -Path $viewerPath)) {
    throw "NetLog viewer not found: $viewerPath"
}

if (-not (Test-Path -Path $harAnalyzerPath)) {
    throw "HAR analyzer not found: $harAnalyzerPath"
}

if (-not (Test-Path -Path $NetLogPath)) {
    throw "NetLog file not found: $NetLogPath"
}

if (-not (Test-Path -Path $HarPath)) {
    throw "HAR file not found: $HarPath"
}

$tempNetLog = Join-Path -Path $env:TEMP -ChildPath ("netlog_summary_" + [guid]::NewGuid().ToString("N") + ".json")
$tempHar = Join-Path -Path $env:TEMP -ChildPath ("har_summary_" + [guid]::NewGuid().ToString("N") + ".json")

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

function Invoke-HarAnalyzer {
    param(
        [string]$InputPath,
        [string]$SummaryPath
    )

    $arguments = @(
        ".\har_analyzer.py",
        $InputPath,
        "--limit", $Limit,
        "--json-output", $SummaryPath
    )

    Push-Location $scriptRoot
    try {
        py -3 @arguments
        if ($LASTEXITCODE -ne 0) {
            throw "har_analyzer.py failed for: $InputPath"
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
        [hashtable]$NetLogCounts,
        [hashtable]$HarCounts,
        [int]$Top = 8
    )

    $allKeys = New-Object System.Collections.Generic.HashSet[string]
    foreach ($key in $NetLogCounts.Keys) { [void]$allKeys.Add($key) }
    foreach ($key in $HarCounts.Keys) { [void]$allKeys.Add($key) }

    $rows = foreach ($key in $allKeys) {
        $netlogValue = 0
        $harValue = 0

        if ($NetLogCounts.ContainsKey($key)) { $netlogValue = [int]$NetLogCounts[$key] }
        if ($HarCounts.ContainsKey($key)) { $harValue = [int]$HarCounts[$key] }

        [pscustomobject]@{
            Error = $key
            NetLog = $netlogValue
            HAR = $harValue
            Delta = ($netlogValue - $harValue)
            AbsDelta = [Math]::Abs($netlogValue - $harValue)
        }
    }

    return $rows |
        Sort-Object -Property @{Expression = "AbsDelta"; Descending = $true}, @{Expression = "Error"; Descending = $false} |
        Select-Object -First $Top
}

function Normalize-Host {
    param([string]$Value)

    if ([string]::IsNullOrWhiteSpace($Value)) {
        return ""
    }

    $text = $Value.Trim().ToLowerInvariant()
    if ($text.Contains(":")) {
        return $text.Split(":")[0]
    }
    return $text
}

function Resolve-OverallRecommendation {
    param(
        [int]$NetLogScore,
        [double]$HarFailureRate,
        [double]$HarStallRate,
        [int]$SharedHostCount
    )

    if ($NetLogScore -ge 65 -and $HarFailureRate -lt 0.10 -and $HarStallRate -lt 0.10) {
        return "Strong browser-local signal: NetLog has high local signature while HAR has low failure/stall rates. Prioritize browser reset/profile cleanup/reinstall testing."
    }

    if ($NetLogScore -lt 40 -and ($HarFailureRate -ge 0.20 -or $HarStallRate -ge 0.20)) {
        return "Strong service/network path signal: HAR shows meaningful failures/stalls while NetLog local signatures are weak. Prioritize upstream service/network triage."
    }

    if ($NetLogScore -ge 50 -and ($HarFailureRate -ge 0.15 -or $HarStallRate -ge 0.15)) {
        return "Mixed signal: both local browser signatures and HAR-side request failures are present. Run clean-profile repro and backend latency/error checks in parallel."
    }

    if ($SharedHostCount -ge 3) {
        return "Cross-source overlap found on multiple hosts. Focus first on shared host/service health and edge/network path consistency."
    }

    return "Moderate signal. Gather one more synchronized NetLog+HAR repro and compare host-level overlap plus request timing outliers."
}

function Resolve-ScoreColor {
    param([int]$Score)

    if ($Score -ge 80) { return "Red" }
    if ($Score -ge 65) { return "Magenta" }
    if ($Score -ge 50) { return "Yellow" }
    if ($Score -ge 35) { return "Cyan" }
    if ($Score -ge 20) { return "Green" }
    return "White"
}

function Resolve-CountColor {
    param([int]$Count)

    if ($Count -gt 0) { return "Yellow" }
    return "Green"
}

function Resolve-DeltaColor {
    param([int]$Delta)

    if ($Delta -gt 0) { return "Yellow" }
    if ($Delta -lt 0) { return "Cyan" }
    return "Green"
}

function Try-GetEpochMs {
    param([object]$Value)

    if ($null -eq $Value) {
        return $null
    }

    $text = [string]$Value
    if ([string]::IsNullOrWhiteSpace($text)) {
        return $null
    }

    [double]$num = 0
    if ([double]::TryParse($text, [ref]$num)) {
        if ($num -ge 1000000000000) {
            return [double]$num
        }
        if ($num -ge 1000000000) {
            return [double]($num * 1000)
        }
        return $null
    }

    [datetimeoffset]$dto = [datetimeoffset]::MinValue
    if ([datetimeoffset]::TryParse($text, [ref]$dto)) {
        return [double]$dto.ToUnixTimeMilliseconds()
    }

    return $null
}

function Get-NetLogHostCounts {
    param([object[]]$Timeline)

    $counts = @{}
    foreach ($row in @($Timeline)) {
        $hostName = Normalize-Host -Value ([string]$row.host)
        if (-not $hostName -or $hostName -eq "-") {
            continue
        }
        if (-not $counts.ContainsKey($hostName)) {
            $counts[$hostName] = 0
        }
        $counts[$hostName] = [int]$counts[$hostName] + 1
    }
    return $counts
}

function Get-TopHarFailedOrStalledRows {
    param([object]$HarSummary, [int]$Top = 5)

    $rows = @()
    if ($null -ne $HarSummary.top_failed_or_stalled) {
        $rows = @($HarSummary.top_failed_or_stalled)
    }

    if ($rows.Count -eq 0) {
        $rows = @($HarSummary.timeline | Where-Object {
            ([int]$_.status -eq 0) -or
            ([int]$_.status -ge 400) -or
            ([double]$_.wait_ms -ge 4000) -or
            ([double]$_.blocked_ms -ge 1000)
        })
    }

    return @($rows | Select-Object -First $Top)
}

function Resolve-LayerVerdict {
    param(
        [object]$NetLogAssessment,
        [double]$HarFailureRate,
        [double]$HarStallRate,
        [int]$SharedHostCount
    )

    $local = [int]$NetLogAssessment.local_signature_count
    $network = [int]$NetLogAssessment.network_signature_count
    $harHot = ($HarFailureRate -ge 0.20 -or $HarStallRate -ge 0.20)
    $netlogLocalHot = ($local -gt $network -and [int]$NetLogAssessment.score -ge 55)
    $netlogNetworkHot = ($network -ge $local -and $network -ge 3)

    if ($harHot -and $netlogNetworkHot) {
        return "Network-layer"
    }

    if ((-not $harHot) -and $netlogLocalHot) {
        return "App-layer"
    }

    if ($harHot -and $netlogLocalHot) {
        return "Mixed"
    }

    if ($SharedHostCount -ge 3) {
        return "Network-layer"
    }

    return "Mixed"
}

function Get-ProtocolEvidence {
    param([object]$NetLogSummary)

    $eventTypes = Get-CounterFromObject -ObjectValue $NetLogSummary.error_event_type_counts
    $quic = $false
    $http2 = $false
    $tcp = $false

    foreach ($name in $eventTypes.Keys) {
        $upper = ([string]$name).ToUpperInvariant()
        if ($upper.Contains("QUIC")) { $quic = $true }
        if ($upper.Contains("HTTP2") -or $upper.Contains("HTTP_STREAM")) { $http2 = $true }
        if ($upper.Contains("TCP") -or $upper.Contains("SOCKET") -or $upper.Contains("CONNECT")) { $tcp = $true }
    }

    if (-not $quic -and $null -ne $NetLogSummary.event_categories) {
        $categoryMap = Get-CounterFromObject -ObjectValue $NetLogSummary.event_categories
        if ($categoryMap.ContainsKey("QUIC") -and [int]$categoryMap["QUIC"] -gt 0) {
            $quic = $true
        }
    }

    return [pscustomobject]@{
        quic = $quic
        http2 = $http2
        tcp = $tcp
    }
}

try {
    Write-Host "`n===== NetLog vs HAR Comparison =====" -ForegroundColor Cyan
    Write-Host "NetLog: $NetLogLabel -> $NetLogPath"
    Write-Host "HAR:    $HarLabel -> $HarPath"

    $netlogSummary = Invoke-NetLogViewer -InputPath $NetLogPath -SummaryPath $tempNetLog
    $harSummary = Invoke-HarAnalyzer -InputPath $HarPath -SummaryPath $tempHar

    $netlogAssessment = $netlogSummary.assessment

    $netlogErrors = Get-CounterFromObject -ObjectValue $netlogSummary.error_counts
    $harFailureReasons = Get-CounterFromObject -ObjectValue $harSummary.failure_reason_counts
    $topHarFailedOrStalled = @(Get-TopHarFailedOrStalledRows -HarSummary $harSummary -Top 8)
    $netlogHostCounts = Get-NetLogHostCounts -Timeline @($netlogSummary.timeline)
    $topNetLogHosts = @(
        $netlogHostCounts.GetEnumerator() |
            Sort-Object -Property @{Expression = "Value"; Descending = $true}, @{Expression = "Name"; Descending = $false} |
            Select-Object -First 8
    )

    $topDiff = Get-TopDifferenceRows -NetLogCounts $netlogErrors -HarCounts $harFailureReasons -Top 10

    $netlogAffected = @($netlogSummary.affected_hosts)
    $harTopDomains = @($harSummary.top_domains | ForEach-Object { [string]$_.domain })
    $harFailedDomains = @($topHarFailedOrStalled | ForEach-Object { [string]$_.domain })
    if ($harFailedDomains.Count -gt 0) {
        $harTopDomains = @($harTopDomains + $harFailedDomains)
    }

    $harDomainSet = New-Object System.Collections.Generic.HashSet[string]
    foreach ($domain in $harTopDomains) {
        $normalized = Normalize-Host -Value $domain
        if ($normalized) { [void]$harDomainSet.Add($normalized) }
    }

    $commonFailingTargets = @(
        $netlogAffected |
            ForEach-Object { Normalize-Host -Value ([string]$_) } |
            Where-Object { $_ -and $harDomainSet.Contains($_) } |
            Sort-Object -Unique
    )

    $harTotalRequests = [Math]::Max([int]$harSummary.total_requests, 1)
    $harFailureRate = [double]$harSummary.failed_call_count / $harTotalRequests
    $harStallRate = [double]$harSummary.stalled_call_count / $harTotalRequests
    $scoreColor = Resolve-ScoreColor -Score ([int]$netlogAssessment.score)
    $verdict = Resolve-LayerVerdict `
        -NetLogAssessment $netlogAssessment `
        -HarFailureRate $harFailureRate `
        -HarStallRate $harStallRate `
        -SharedHostCount $commonFailingTargets.Count
    $protocolEvidence = Get-ProtocolEvidence -NetLogSummary $netlogSummary

    $primaryStalledHar = $null
    if ($topHarFailedOrStalled.Count -gt 0) {
        $primaryStalledHar = @($topHarFailedOrStalled | Select-Object -First 1)[0]
    }

    $nearestNetlogErrors = @()
    if ($null -ne $primaryStalledHar) {
        $harEpoch = Try-GetEpochMs -Value $primaryStalledHar.started_at
        if ($null -ne $harEpoch) {
            $nearestNetlogErrors = @(
                $netlogSummary.timeline |
                    ForEach-Object {
                        $netEpoch = Try-GetEpochMs -Value $_.time
                        if ($null -eq $netEpoch) {
                            return $null
                        }
                        [pscustomobject]@{
                            host = $_.host
                            error_name = $_.error_name
                            event_type = $_.event_type
                            phase = $_.phase
                            time = $_.time
                            delta_ms = [Math]::Abs([double]$netEpoch - [double]$harEpoch)
                        }
                    } |
                    Where-Object { $null -ne $_ } |
                    Sort-Object -Property delta_ms, host |
                    Select-Object -First 5
            )
        }
    }

    $overall = Resolve-OverallRecommendation `
        -NetLogScore ([int]$netlogAssessment.score) `
        -HarFailureRate $harFailureRate `
        -HarStallRate $harStallRate `
        -SharedHostCount $commonFailingTargets.Count

    Write-Host ""
    Write-Host "--- Side-by-Side ---" -ForegroundColor Yellow
    Write-Host "$NetLogLabel"
    Write-Host "  Browser Hint: $($netlogSummary.browser_hint)"
    Write-Host "  Local Reinstall Signal Score: $($netlogAssessment.score)/100" -ForegroundColor $scoreColor
    Write-Host "  Likelihood: $($netlogAssessment.likelihood)"
    Write-Host "  NetLog Failure Events: $($netlogAssessment.total_failures)" -ForegroundColor (Resolve-CountColor -Count ([int]$netlogAssessment.total_failures))
    Write-Host "  Affected Hosts: $($netlogAssessment.affected_host_count)" -ForegroundColor (Resolve-CountColor -Count ([int]$netlogAssessment.affected_host_count))

    Write-Host ""
    Write-Host "$HarLabel"
    Write-Host "  Total Requests: $($harSummary.total_requests)"
    Write-Host "  Unique Endpoints: $($harSummary.unique_endpoints)"
    Write-Host "  Failed Calls: $($harSummary.failed_call_count)" -ForegroundColor (Resolve-CountColor -Count ([int]$harSummary.failed_call_count))
    Write-Host "  Slow Calls: $($harSummary.slow_call_count)" -ForegroundColor (Resolve-CountColor -Count ([int]$harSummary.slow_call_count))
    Write-Host "  Stalled Calls: $($harSummary.stalled_call_count)" -ForegroundColor (Resolve-CountColor -Count ([int]$harSummary.stalled_call_count))
    Write-Host ("  Failure Rate: {0:P1}" -f $harFailureRate) -ForegroundColor (Resolve-CountColor -Count ([int]$harSummary.failed_call_count))
    Write-Host ("  Stall Rate: {0:P1}" -f $harStallRate) -ForegroundColor (Resolve-CountColor -Count ([int]$harSummary.stalled_call_count))

    Write-Host ""
    Write-Host "--- Top HAR Failed/Stalled URLs ---" -ForegroundColor Yellow
    if ($topHarFailedOrStalled.Count -eq 0) {
        Write-Host "  None"
    }
    else {
        foreach ($row in $topHarFailedOrStalled) {
            Write-Host "  $($row.method) $($row.status) $([Math]::Round([double]$row.duration_ms, 0))ms | $($row.url)"
        }
    }

    Write-Host ""
    Write-Host "--- Top NetLog Failed Hosts ---" -ForegroundColor Yellow
    if ($topNetLogHosts.Count -eq 0) {
        Write-Host "  None"
    }
    else {
        foreach ($hostRow in $topNetLogHosts) {
            Write-Host "  $($hostRow.Name): $($hostRow.Value)"
        }
    }

    Write-Host ""
    Write-Host "--- NetLog Error vs HAR Failure-Signal Delta (Top 10) ---" -ForegroundColor Yellow
    if ($topDiff.Count -eq 0) {
        Write-Host "  No error differences found"
    }
    else {
        foreach ($row in $topDiff) {
            Write-Host "  $($row.Error): NetLog=$($row.NetLog), HAR=$($row.HAR), delta=$($row.Delta)" -ForegroundColor (Resolve-DeltaColor -Delta ([int]$row.Delta))
        }
    }

    Write-Host ""
    Write-Host "--- Host Overlap ---" -ForegroundColor Yellow
    Write-Host "  Shared failing hosts: $($commonFailingTargets.Count)" -ForegroundColor (Resolve-CountColor -Count $commonFailingTargets.Count)
    if ($commonFailingTargets.Count -gt 0) {
        foreach ($targetName in ($commonFailingTargets | Sort-Object | Select-Object -First 15)) {
            Write-Host "  - $targetName"
        }
    }

    Write-Host ""
    Write-Host "--- HAR Timing of Stalled Call ---" -ForegroundColor Yellow
    if ($null -eq $primaryStalledHar) {
        Write-Host "  No failed/stalled HAR call found."
    }
    else {
        Write-Host "  URL: $($primaryStalledHar.url)"
        Write-Host "  Status: $($primaryStalledHar.status)"
        Write-Host "  Duration: $([Math]::Round([double]$primaryStalledHar.duration_ms, 0))ms"
        Write-Host "  Wait: $([Math]::Round([double]$primaryStalledHar.wait_ms, 0))ms"
        Write-Host "  Blocked: $([Math]::Round([double]$primaryStalledHar.blocked_ms, 0))ms"
        Write-Host "  StartedAt: $($primaryStalledHar.started_at)"
    }

    Write-Host ""
    Write-Host "--- Nearest NetLog Errors by Timestamp ---" -ForegroundColor Yellow
    if ($nearestNetlogErrors.Count -eq 0) {
        Write-Host "  Unable to compute nearest-by-time matches (timestamp bases may differ or be relative)."
    }
    else {
        foreach ($near in $nearestNetlogErrors) {
            Write-Host "  host=$($near.host) error=$($near.error_name) type=$($near.event_type) phase=$($near.phase) time=$($near.time) delta_ms=$([Math]::Round([double]$near.delta_ms, 0))"
        }
    }

    Write-Host ""
    Write-Host "--- Protocol Evidence: QUIC / HTTP2 / TCP ---" -ForegroundColor Yellow
    Write-Host "  QUIC:  $($protocolEvidence.quic)"
    Write-Host "  HTTP2: $($protocolEvidence.http2)"
    Write-Host "  TCP:   $($protocolEvidence.tcp)"

    Write-Host ""
    Write-Host "--- Verdict: App-layer vs Network-layer vs Mixed ---" -ForegroundColor Yellow
    Write-Host "  $verdict" -ForegroundColor $(if ($verdict -eq "Network-layer") { "Yellow" } elseif ($verdict -eq "App-layer") { "Cyan" } else { "Magenta" })

    Write-Host ""
    Write-Host "--- Overall Recommendation ---" -ForegroundColor Green
    Write-Host "  $overall" -ForegroundColor Green

    $result = [pscustomobject]@{
        generated_at = (Get-Date).ToString("o")
        netlog_label = $NetLogLabel
        har_label = $HarLabel
        netlog_file = (Resolve-Path -Path $NetLogPath).Path
        har_file = (Resolve-Path -Path $HarPath).Path
        netlog = $netlogSummary
        har = $harSummary
        har_failure_rate = $harFailureRate
        har_stall_rate = $harStallRate
        verdict = $verdict
        protocol_evidence = $protocolEvidence
        top_har_failed_or_stalled = @($topHarFailedOrStalled)
        top_netlog_failed_hosts = @($topNetLogHosts | ForEach-Object {
            [pscustomobject]@{
                host = $_.Name
                count = $_.Value
            }
        })
        primary_har_stalled_call = $primaryStalledHar
        nearest_netlog_errors = @($nearestNetlogErrors)
        shared_failing_hosts = ($commonFailingTargets | Sort-Object -Unique)
        overall_recommendation = $overall
        error_delta = @($topDiff | ForEach-Object {
            [pscustomobject]@{
                error = $_.Error
                netlog = $_.NetLog
                har = $_.HAR
                delta = $_.Delta
            }
        })
    }

    $result | ConvertTo-Json -Depth 8 | Set-Content -Path $OutputPath -Encoding UTF8
    Write-Host ""
    Write-Host "Comparison JSON written to: $OutputPath" -ForegroundColor Green
}
finally {
    Remove-Item -Path $tempNetLog -ErrorAction SilentlyContinue
    Remove-Item -Path $tempHar -ErrorAction SilentlyContinue
}
