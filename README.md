# HAR-bot
HAR-bot analyzes HAR files linearly to explain why an app appears to hang and to surface standard failure patterns.

It now focuses on:
- Failed requests (status 0 or HTTP 4xx/5xx)
- Slow requests (total duration >= 3000ms)
- Stalled requests (high wait/TTFB or blocked time)
- Common failure signals across the full request timeline
- **NEW: Request deduplication to eliminate redundant data analysis**
- **NEW: Timeout protection and retry logic to prevent API hanging**

## Key Enhancements

### Request Deduplication
The analyzer now intelligently detects and merges duplicate requests:
- **Normalizes URLs** by removing tracking parameters (utm_*, fbclid, gclid, etc.)
- **Computes request hashes** based on method, URL, and body content
- **Aggregates timing statistics** for duplicate requests
- **Logs redundancy metrics** showing % of duplicate requests eliminated
- Reduces processing overhead for large HAR captures with many repeated requests

### Timeout & Retry Protection
- **Configurable timeouts** prevent API hanging on slow operations
- **Exponential backoff retry logic** with sensible defaults
- **Process-level timeout enforcement** in PowerShell scripts
- **Detailed logging** of collection and analysis attempts

### Caching System
- **Event cache** for hardware analysis pipeline (`collect.ps1`)
- **Optional cache usage** via `-UseCache` flag
- Reduces redundant Windows Event Log queries
- Cache auto-expires after 1 hour

## Which One Should I Run?

- Run `har_analyzer.py` when you have a browser/network `.har` capture and want to diagnose app hangs, slow requests, or failing endpoints.
- Run `netlog_viewer.py` when you have a Chrome/Edge NetLog JSON export and want to identify browser-local vs network-path failures.
- Run `compare_netlogs.ps1` when you want a single NetLog-vs-HAR comparison and one consolidated recommendation.
- Run `run_hardware_pipeline.ps1` when you suspect Windows device instability and want to analyze System Event IDs from the last N days.

## Usage

### HAR Analysis (with deduplication)
```powershell
python har_analyzer.py path\to\capture.har --limit 25

# Optional JSON summary output
python har_analyzer.py path\to\capture.har --limit 25 --json-output .\har_summary.json
```

### NetLog Viewer (Chrome/Edge)
```powershell
py -3 .\netlog_viewer.py path\to\netlog.json --limit 25

# Optional JSON summary output
py -3 .\netlog_viewer.py path\to\netlog.json --json-output .\netlog_summary.json
```

### NetLog vs HAR Comparison
```powershell
.\compare_netlogs.ps1 `
  -NetLogPath .\chrome_netlog.json `
  -HarPath .\app_capture.har `
  -NetLogLabel "Chrome NetLog" `
  -HarLabel "Checkout HAR" `
  -OutputPath .\netlog_har_compare.json
```

The comparison output shows:
- Side-by-side browser-local signal vs HAR hang/failure rates
- Delta view between top NetLog errors and HAR failure reasons
- Shared host overlap between NetLog failing hosts and HAR top domains
- One overall recommendation for triage

The output shows:
- Browser hint from NetLog metadata
- Top Chromium net errors (for example, ERR_CERT_*, ERR_SSL_*, ERR_NAME_NOT_RESOLVED)
- Event categories (TLS/Certificate, DNS, Proxy, Connection, HTTP, QUIC)
- Error timeline rows with event type, phase, source, host, and error
- A reinstall likelihood score and recommendation

The output now shows:
- **Duplicate Requests Detected**: Count of redundant requests found
- **Redundancy Eliminated**: Percentage reduction from deduplication
- **Unique Endpoints**: Count after merging duplicates
- **Occurrence counts** for repeated requests (marked with ×N)
- **Aggregate timing statistics** for duplicate requests

### Hardware Pipeline (with timeout & retry)
```powershell
# Basic run (7 days, with defaults)
.\run_hardware_pipeline.ps1

# With custom timeout and retries
.\run_hardware_pipeline.ps1 -DaysBack 7 -CollectionTimeoutSeconds 45 -MaxRetries 3

# Skip collection if using existing events
.\run_hardware_pipeline.ps1 -SkipCollection
```

**Parameters:**
- `-DaysBack`: Number of days to analyze (default: 7)
- `-CollectionTimeoutSeconds`: Timeout for event collection (default: 30)
- `-AnalysisTimeoutSeconds`: Timeout for analysis (default: 60)
- `-MaxRetries`: Number of retry attempts (default: 2)
- `-SkipCollection`: Use existing events file without re-collecting

### Collector Script (with caching)
```powershell
.\collect.ps1 -DaysBack 7 -UseCache
```

**Parameters:**
- `-DaysBack`: Number of days to look back (default: 7)
- `-TimeoutSeconds`: Query timeout in seconds (default: 30)
- `-MaxRetries`: Retry attempts (default: 2)
- `-UseCache`: Enable event caching (default: $false)

## Output Highlights

### HAR Analysis
- **Deduplication Summary**: Shows how many redundant requests were detected
- Category summary (Search, GraphQL, Telemetry, Unknown)
- Standard failure signals and frequency
- Linear request timeline with per-request timing breakdown
- Hang indicators per request
- **Duplicate request flags** indicating repeated API calls

### NetLog Viewer
- Browser metadata hint from NetLog constants/client info
- Top negative net errors and counts
- Failure-prone event types and categories
- Host footprint of failing requests
- Reinstall signal score to guide reset/reinstall triage

### NetLog vs HAR Comparison
- Side-by-side comparison of one NetLog capture and one HAR capture
- NetLog local reinstall signal shown next to HAR failure/stall rates
- Shared host analysis (NetLog failing hosts vs HAR top domains)
- Consolidated recommendation across both data sources

### Hardware Pipeline
- Category summary (Search, GraphQL, Telemetry, Unknown)
- Standard failure signals and frequency
- Linear request timeline with per-request timing breakdown:
	blocked, dns, connect, ssl, send, wait, receive
- Hang indicators per request (for example: long server wait, slow connect, request did not complete)

## Project Layout

| Workflow | Goal | Main Script | Supporting Scripts | Input | Output |
| --- | --- | --- | --- | --- | --- |
| HAR analysis | Diagnose app hangs and request-level failure patterns from browser/network captures with deduplication | `har_analyzer.py` | None | `.har` file | Console report with hang findings, category summary, deduplication stats, and timeline |
| NetLog viewing | Triage Chrome/Edge network failures and estimate browser-local reinstall likelihood | `netlog_viewer.py` | None | NetLog `.json` export | Console report with net error summary, category breakdown, timeline, and reinstall signal |
| NetLog vs HAR comparison | Compare one NetLog capture with one HAR capture and output a single triage recommendation | `compare_netlogs.ps1` | `netlog_viewer.py`, `har_analyzer.py` | NetLog `.json` + HAR `.har` | Console comparison + `netlog_har_compare.json` with score deltas and host overlap |
| Hardware pipeline | Flag possible Windows device instability from System Event IDs with timeout protection | `run_hardware_pipeline.ps1` | `collect.ps1`, `analyze.py` | Windows System log events (last N days) | `hardware_events.json`, `summary.txt`, `result.json` |

### Scripts

- `har_analyzer.py`: Analyzes HAR files with intelligent request deduplication
  - Normalizes URLs for accurate duplicate detection
  - Merges duplicate requests with aggregated timing stats
  - Logs redundancy information

- `netlog_viewer.py`: Parses Chrome/Edge NetLog JSON and surfaces browser-network failure signatures
  - Resolves numeric NetLog constants into readable event/phase/source names
  - Extracts and ranks Chromium net errors (ERR_*)
  - Scores reinstall likelihood based on local-vs-network error signatures

- `compare_netlogs.ps1`: Runs side-by-side NetLog-vs-HAR comparison
  - Calls `netlog_viewer.py` and `har_analyzer.py` and reads JSON summaries
  - Highlights the largest NetLog error vs HAR failure-signal deltas
  - Produces a consolidated recommendation and writes `netlog_har_compare.json`
  
- `collect.ps1`: Pulls selected Event IDs from the Windows System log for the last N days
  - Timeout-protected event queries
  - Automatic retry with exponential backoff
  - Event caching to reduce redundant queries
  - Comprehensive logging
  
- `analyze.py`: Scores collected events, groups signals (crash/WHEA/disk/PnP)
  - Timeout protection on file operations
  - Comprehensive error handling

- `run_hardware_pipeline.ps1`: Orchestrates the full hardware analysis pipeline
  - Process-level timeout enforcement
  - Automatic retries on failure
  - Detailed step logging
  - Better error reporting

### Run End-to-End

```powershell
# Basic run
.\run_hardware_pipeline.ps1 -DaysBack 7

# With protection against hanging and caching
.\run_hardware_pipeline.ps1 -DaysBack 7 -CollectionTimeoutSeconds 45 -MaxRetries 3

# Run only analysis (skip collection)
.\run_hardware_pipeline.ps1 -SkipCollection
```

## Troubleshooting

### Collection Timeout
If `collect.ps1` times out:
```powershell
# Increase timeout
.\run_hardware_pipeline.ps1 -CollectionTimeoutSeconds 60 -MaxRetries 3

# Or run collection directly with higher timeout
.\collect.ps1 -TimeoutSeconds 60 -MaxRetries 3
```

### Analysis Hanging
If analysis appears to hang:
```powershell
# Increase analysis timeout
.\run_hardware_pipeline.ps1 -AnalysisTimeoutSeconds 120
```

### Cache Issues
Clear the event cache if needed:
```powershell
Remove-Item ".\.cache\events_cache.json" -Force -ErrorAction SilentlyContinue
```

## Performance Notes

- **HAR Deduplication**: Typically reduces processing by 10-40% for captures with repeated requests
- **Event Caching**: Subsequent runs complete in <100ms if cache is valid
- **Timeout Protection**: Prevents infinite hangs; processes will fail gracefully after timeout + retries

Optional outputs:

```powershell
.\run_hardware_pipeline.ps1 -DaysBack 7 -EventsOutputPath .\hardware_events.json -SummaryOutputPath .\summary.txt -ResultOutputPath .\result.json
```

### Run Steps Individually

```powershell
.\collect.ps1 -DaysBack 7 -OutputPath .\hardware_events.json
py -3 .\analyze.py --input .\hardware_events.json --summary .\summary.txt --result .\result.json
```