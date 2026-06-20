param(
    [int]$DaysBack = 7,
    [string]$EventsOutputPath = ".\hardware_events.json",
    [string]$SummaryOutputPath = ".\summary.txt",
    [string]$ResultOutputPath = ".\result.json",
    [int]$CollectionTimeoutSeconds = 30,
    [int]$AnalysisTimeoutSeconds = 60,
    [int]$MaxRetries = 2,
    [switch]$SkipCollection
)

$ErrorActionPreference = "Stop"
$VerbosePreference = "Continue"

$scriptRoot = Split-Path -Path $MyInvocation.MyCommand.Path -Parent
$collectorPath = Join-Path -Path $scriptRoot -ChildPath "collect.ps1"
$analyzerPath = Join-Path -Path $scriptRoot -ChildPath "analyze.py"

function Write-StepLog {
    param(
        [string]$Message,
        [int]$Step,
        [int]$Total = 3
    )
    Write-Host "`n>>> Step $Step/$Total: $Message" -ForegroundColor Cyan
}

function Invoke-ProcessWithTimeout {
    param(
        [string]$FilePath,
        [string[]]$ArgumentList,
        [int]$TimeoutSeconds,
        [string]$ProcessName
    )
    
    $attempt = 0
    $backoffMs = 1000
    
    while ($attempt -lt $MaxRetries) {
        try {
            Write-Verbose "Executing: $FilePath $(($ArgumentList -join ' '))" 
            Write-Verbose "Timeout: $TimeoutSeconds seconds, Attempt: $($attempt + 1)/$MaxRetries"
            
            $process = Start-Process -FilePath $FilePath `
                                     -ArgumentList $ArgumentList `
                                     -NoNewWindow `
                                     -PassThru `
                                     -RedirectStandardOutput "$env:TEMP\${ProcessName}_stdout.txt" `
                                     -RedirectStandardError "$env:TEMP\${ProcessName}_stderr.txt"
            
            $completed = $process | Wait-Process -Timeout $TimeoutSeconds -ErrorAction SilentlyContinue
            
            if ($completed -or $process.HasExited) {
                if ($process.ExitCode -ne 0) {
                    $stderr = Get-Content "$env:TEMP\${ProcessName}_stderr.txt" -Raw -ErrorAction SilentlyContinue
                    throw "Process exited with code $($process.ExitCode): $stderr"
                }
                
                $stdout = Get-Content "$env:TEMP\${ProcessName}_stdout.txt" -Raw -ErrorAction SilentlyContinue
                Write-Verbose "Process output: $stdout"
                return $true
            } else {
                # Process timed out
                Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
                throw "Process timeout after $TimeoutSeconds seconds"
            }
        } catch {
            $attempt++
            $lastError = $_
            
            if ($attempt -lt $MaxRetries) {
                Write-Warning "Attempt $attempt failed: $($_.Exception.Message). Retrying in $($backoffMs)ms..."
                Start-Sleep -Milliseconds $backoffMs
                $backoffMs = [Math]::Min($backoffMs * 2, 10000)
            } else {
                throw "Process failed after $MaxRetries attempts: $lastError"
            }
        } finally {
            # Cleanup temp files
            Remove-Item "$env:TEMP\${ProcessName}_stdout.txt" -ErrorAction SilentlyContinue
            Remove-Item "$env:TEMP\${ProcessName}_stderr.txt" -ErrorAction SilentlyContinue
        }
    }
}

try {
    Write-Host "`n===== HAR-Bot Hardware Pipeline =====" -ForegroundColor Green
    Write-Host "Configuration:" -ForegroundColor Cyan
    Write-Host "  Days Back: $DaysBack"
    Write-Host "  Collection Timeout: $CollectionTimeoutSeconds seconds"
    Write-Host "  Analysis Timeout: $AnalysisTimeoutSeconds seconds"
    Write-Host "  Max Retries: $MaxRetries"
    Write-Host "  Skip Collection: $SkipCollection"
    
    # Validate script paths
    if (-not (Test-Path -Path $collectorPath)) {
        throw "Collector script not found: $collectorPath"
    }
    
    if (-not (Test-Path -Path $analyzerPath)) {
        throw "Analyzer script not found: $analyzerPath"
    }
    
    # Step 1: Collection
    if (-not $SkipCollection) {
        Write-StepLog "Collecting hardware events from Windows System log" 1 3
        
        Invoke-ProcessWithTimeout -FilePath "powershell.exe" `
                                  -ArgumentList @(
                                      "-NoProfile",
                                      "-ExecutionPolicy", "Bypass",
                                      "-File", $collectorPath,
                                      "-DaysBack", $DaysBack,
                                      "-OutputPath", $EventsOutputPath,
                                      "-TimeoutSeconds", $CollectionTimeoutSeconds,
                                      "-MaxRetries", $MaxRetries,
                                      "-UseCache"
                                  ) `
                                  -TimeoutSeconds ($CollectionTimeoutSeconds + 15) `
                                  -ProcessName "collector"
        
        Write-Host "✓ Collection complete: $EventsOutputPath" -ForegroundColor Green
    } else {
        if (-not (Test-Path -Path $EventsOutputPath)) {
            throw "Collection skipped but events file not found: $EventsOutputPath"
        }
        Write-Host "✓ Using existing events file: $EventsOutputPath" -ForegroundColor Green
    }
    
    # Step 2: Analysis
    Write-StepLog "Analyzing collected events" 2 3
    
    $pyExecutable = Get-Command python -ErrorAction SilentlyContinue
    if ($pyExecutable) {
        $pyPath = $pyExecutable.Source
    } else {
        $pyPath = "python"
    }
    
    Invoke-ProcessWithTimeout -FilePath $pyPath `
                              -ArgumentList @(
                                  $analyzerPath,
                                  "--input", $EventsOutputPath,
                                  "--summary", $SummaryOutputPath,
                                  "--result", $ResultOutputPath
                              ) `
                              -TimeoutSeconds $AnalysisTimeoutSeconds `
                              -ProcessName "analyzer"
    
    Write-Host "✓ Analysis complete" -ForegroundColor Green
    
    # Step 3: Report
    Write-StepLog "Displaying summary report" 3 3
    
    if (Test-Path -Path $SummaryOutputPath) {
        Write-Host "`n--- Summary Report ---`n" -ForegroundColor Cyan
        Get-Content -Path $SummaryOutputPath
    } else {
        Write-Warning "Summary file not found: $SummaryOutputPath"
    }
    
    Write-Host "`n===== Pipeline Complete =====" -ForegroundColor Green
    Write-Host "Results saved to:"
    Write-Host "  Events: $EventsOutputPath"
    Write-Host "  Summary: $SummaryOutputPath"
    Write-Host "  JSON Result: $ResultOutputPath"
    
} catch {
    Write-Host "`n✗ Pipeline failed: $_" -ForegroundColor Red
    Write-Host "Stack trace:" -ForegroundColor Red
    Write-Host $_.ScriptStackTrace
    exit 1
}
