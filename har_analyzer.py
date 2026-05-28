import json
import sys
from collections import Counter, defaultdict

def analyze_har(file_path):
    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    entries = data.get("log", {}).get("entries", [])

    error_counts = Counter()
    domain_counts = Counter()
    slow_requests = []
    auth_failures = []
    url_hits = defaultdict(int)

    for entry in entries:
        request = entry.get("request", {})
        response = entry.get("response", {})

        url = request.get("url", "")
        status = response.get("status", 0)
        time = entry.get("time", 0)

        # Domain grouping
        domain = url.split("/")[2] if "://" in url else "unknown"
        domain_counts[domain] += 1

        # Track URL repetition
        url_hits[url] += 1

        # Errors
        if status >= 400:
            error_counts[status] += 1

        # Slow requests (>1000ms)
        if time > 1000:
            slow_requests.append((time, url))

        # Auth-related signals (SSO / session issues)
        if status in [401, 403]:
            auth_failures.append(url)

    # Detect potential refresh loops (same URL repeated a lot)
    repeated_calls = {url: count for url, count in url_hits.items() if count > 10}

    return {
        "total_requests": len(entries),
        "errors": error_counts,
        "slow_requests": sorted(slow_requests, reverse=True)[:10],
        "auth_failures": auth_failures,
        "top_domains": domain_counts.most_common(5),
        "repeated_calls": repeated_calls
    }


def print_summary(results):
    print("\n===== HAR ANALYSIS SUMMARY =====\n")

    print(f"Total Requests: {results['total_requests']}\n")

    print("Errors:")
    if results["errors"]:
        for status, count in results["errors"].items():
            print(f"  {status}: {count}")
    else:
        print("  None")

    print("\nTop Domains:")
    for domain, count in results["top_domains"]:
        print(f"  {domain}: {count} requests")

    print("\nAuth Failures (401/403):")
    if results["auth_failures"]:
        for url in results["auth_failures"][:5]:
            print(f"  {url}")
    else:
        print("  None")

    print("\nSlow Requests (>1000ms):")
    if results["slow_requests"]:
        for time, url in results["slow_requests"]:
            print(f"  {time} ms - {url}")
    else:
        print("  None")

    print("\nRepeated Calls (possible loops / refresh):")
    if results["repeated_calls"]:
        for url, count in results["repeated_calls"].items():
            print(f"  {count}x - {url}")
    else:
        print("  None")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python har_analyzer.py <file.har>")
        sys.exit(1)

    file_path = sys.argv[1]
    results = analyze_har(file_path)
    print_summary(results)
