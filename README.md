# HAR-bot
HAR-bot analyzes HAR files linearly to explain why an app appears to hang and to surface standard failure patterns.

It now focuses on:
- Failed requests (status 0 or HTTP 4xx/5xx)
- Slow requests (total duration >= 3000ms)
- Stalled requests (high wait/TTFB or blocked time)
- Common failure signals across the full request timeline

## Usage

```powershell
python har_analyzer.py path\to\capture.har --limit 25
```

## Output Highlights

- Category summary (Search, GraphQL, Telemetry, Unknown)
- Standard failure signals and frequency
- Linear request timeline with per-request timing breakdown:
	blocked, dns, connect, ssl, send, wait, receive
- Hang indicators per request (for example: long server wait, slow connect, request did not complete)