param(
    [int]$DaysBack = 7,
    [string]$OutputPath = ".\hardware_events.json",
    [int]$TimeoutSeconds = 30,
    [int]$MaxRetries = 2,
    [switch]$UseCache
)

$ErrorActionPreference = "Stop"
$WarningPreference = "Continue"

# Configuration
$eventIDs = @(
    41, 6008, 1001,      # Critical crashes
    219, 225, 410, 411,  # PnP/device issues
    17, 18, 19,          # WHEA errors
    7, 51, 129, 153,     # Disk errors
    37, 55, 13,          # System errors
    10110, 10111, 7026   # Service issues
)

$cacheDir = Join-Path -Path $PSScriptRoot -ChildPath ".cache"
$cacheFile = Join-Path -Path $cacheDir -ChildPath "events_cache.json"

# Ensure cache directory exists
if (-not (Test-Path -Path $cacheDir)) {
    New-Item -ItemType Directory -Path $cacheDir -Force | Out-Null
}

function Write-Log {
    param(
        [string]$Message,
        [ValidateSet("Info", "Warning", "Error")]
        [string]$Level = "Info"
    )
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Write-Host "[$timestamp] [$Level] $Message"
}

function Get-CachedEvents {
    param(
        [string]$CachePath,
        [int]$DaysBack
    )
    
    if (-not (Test-Path -Path $CachePath)) {
        return $null
    }
    
    try {
        $cache = Get-Content -Path $CachePath -Raw | ConvertFrom-Json
        if ($cache.CachedAt) {
            $cacheAge = (Get-Date) - [datetime]$cache.CachedAt
            if ($cacheAge.TotalHours -lt 1) {
                Write-Log "Using cached events (cached $(($cacheAge.TotalMinutes) -as [int]) minutes ago)" "Info"
                return $cache.Events
            } else {
                Write-Log "Cache expired, fetching fresh data" "Info"
                Remove-Item -Path $CachePath -Force -ErrorAction SilentlyContinue
                return $null
            }
        }
    } catch {
        Write-Log "Failed to read cache: $_" "Warning"
        Remove-Item -Path $CachePath -Force -ErrorAction SilentlyContinue
        return $null
    }
    return $null
}

function Save-EventCache {
    param(
        [string]$CachePath,
        [object]$Events
    )
    
    try {
        $cacheObject = @{
            CachedAt = (Get-Date -AsUTC).ToString("o")
            Events   = $Events
        }
        $cacheObject | ConvertTo-Json -Depth 10 | Out-File -FilePath $CachePath -Encoding utf8 -Force
        Write-Log "Events cached for future use" "Info"
    } catch {
        Write-Log "Failed to save cache: $_" "Warning"
    }
}

function Get-WinEventWithRetry {
    param(
        [hashtable]$FilterHashtable,
        [int]$MaxRetries,
        [int]$TimeoutSeconds
    )
    
    $attempt = 0
    $backoffMs = 500
    
    while ($attempt -lt $MaxRetries) {
        try {
            Write-Log "Fetching events (attempt $($attempt + 1)/$MaxRetries, timeout: ${TimeoutSeconds}s)" "Info"
            
            # Create a job to enforce timeout
            $job = Start-Job -ScriptBlock {
                param($filter)
                Get-WinEvent -FilterHashtable $filter -ErrorAction Stop
            } -ArgumentList $FilterHashtable
            
            # Wait with timeout
            $result = $job | Wait-Job -Timeout $TimeoutSeconds -ErrorAction SilentlyContinue
            
            if ($result) {
                # Job completed within timeout
                $events = Receive-Job -Job $job -ErrorAction SilentlyContinue
                Remove-Job -Job $job -Force
                Write-Log "Successfully fetched $($events.Count) events" "Info"
                return $events
            } else {
                # Job timed out
                Remove-Job -Job $job -Force -ErrorAction SilentlyContinue
                throw "Event query timed out after $TimeoutSeconds seconds"
            }
        } catch {
            $attempt++
            $lastError = $_
            
            if ($attempt -lt $MaxRetries) {
                Write-Log "Fetch failed: $($_.Exception.Message). Retrying in $($backoffMs)ms..." "Warning"
                Start-Sleep -Milliseconds $backoffMs
                $backoffMs = [Math]::Min($backoffMs * 2, 5000)
            } else {
                Write-Log "Event fetch failed after $MaxRetries attempts: $lastError" "Error"
                throw $lastError
            }
        }
    }
}

function Format-Events {
    param(
        [object[]]$Events
    )
    
    if (-not $Events) {
        return @()
    }
    
    $formatted = $Events | ForEach-Object {
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
    
    return $formatted
}

# Main execution
try {
    Write-Log "HAR-Bot Hardware Event Collector" "Info"
    Write-Log "Configuration: DaysBack=$DaysBack, TimeoutSeconds=$TimeoutSeconds, MaxRetries=$MaxRetries, UseCache=$UseCache" "Info"
    
    # Check cache first if enabled
    $events = $null
    if ($UseCache) {
        $events = Get-CachedEvents -CachePath $cacheFile -DaysBack $DaysBack
    }
    
    # Fetch fresh events if cache miss or cache disabled
    if (-not $events) {
        $startTime = (Get-Date).AddDays(-$DaysBack)
        
        $filter = @{
            LogName   = "System"
            ID        = $eventIDs
            StartTime = $startTime
        }
        
        $events = Get-WinEventWithRetry -FilterHashtable $filter -MaxRetries $MaxRetries -TimeoutSeconds $TimeoutSeconds
        
        # Save to cache for future use
        Save-EventCache -CachePath $cacheFile -Events $events
    }
    
    # Format and export
    $formatted = Format-Events -Events $events
    
    $targetDirectory = Split-Path -Path $OutputPath -Parent
    if ($targetDirectory -and -not (Test-Path -Path $targetDirectory)) {
        New-Item -ItemType Directory -Path $targetDirectory -Force | Out-Null
    }
    
    $formatted | ConvertTo-Json -Depth 3 | Out-File -FilePath $OutputPath -Encoding utf8
    
    Write-Log "Successfully exported $($formatted.Count) events to $OutputPath" "Info"
    
} catch {
    Write-Log "Fatal error: $_" "Error"
    exit 1
}
