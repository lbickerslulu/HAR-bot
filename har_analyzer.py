import argparse
import base64
import hashlib
import json
import logging
import re
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)


SEARCH_HINTS = (
    "search",
    "keyword",
    "query",
    "suggest",
    "product-search",
    "catalog",
    "plp",
)

SEARCH_OPERATION_HINTS = (
    "search",
    "productsearch",
)

RECOMMENDATION_OPERATION_HINTS = (
    "recommend",
    "recommendation",
    "related",
    "autocomplete",
    "suggest",
    "topquery",
    "typeahead",
    "topquery",
    "trending",
)

NON_SEARCH_GRAPHQL_HINTS = {
    "getbagcount": "bag/cart",
    "bagcount": "bag/cart",
    "cart": "bag/cart",
    "wishlist": "wishlist",
    "checkout": "checkout",
    "account": "account",
}

TELEMETRY_DOMAIN_HINTS = (
    "datadoghq.com",
    "quantummetric.com",
    "google-analytics.com",
    "googletagmanager.com",
    "doubleclick.net",
    "analytics",
)

ADS_DOMAIN_HINTS = (
    "googleadservices.com",
    "facebook.net",
    "bing.com",
    "celtra.com",
    "persa.do",
)

UTILITY_DOMAIN_HINTS = (
    "launchdarkly.com",
    "cookielaw.org",
)

TELEMETRY_PATH_HINTS = (
    "/metrics",
    "/collect",
    "/telemetry",
    "/events",
    "/rum",
    "/v1/t",
    "/horizon/",
)

ADS_PATH_HINTS = (
    "/ads",
    "/advert",
    "/pixel",
    "/campaign",
    "/marketing",
)

STATIC_EXTENSIONS = (
    ".js",
    ".css",
    ".png",
    ".jpg",
    ".jpeg",
    ".svg",
    ".gif",
    ".woff",
    ".woff2",
    ".map",
)

QUERY_FIELD_NAMES = {
    "q",
    "query",
    "search",
    "searchterm",
    "search_term",
    "keyword",
    "keywords",
    "term",
    "text",
    "phrase",
}

RESULT_KEYS = (
    "total",
    "totalcount",
    "count",
    "numfound",
    "resultcount",
    "resultscount",
    "productcount",
)

PRODUCT_LIST_KEYS = ("products", "items", "results", "hits", "records")
PRODUCT_NAME_KEYS = ("name", "productname", "displayname", "title")
COLOR_KEYS = ("color", "colour", "colorname", "colourname")


def normalize_url_for_dedup(url: str) -> str:
    """Normalize URL for deduplication by removing tracking params and fragments."""
    parsed = urlparse(url)
    query_params = parse_qs(parsed.query)
    
    # Filter out common tracking/session parameters
    exclude_keys = {'utm_source', 'utm_medium', 'utm_campaign', 'utm_content', 'utm_term',
                    'sessionid', 'session_id', 'sid', '_ga', '_gid', 'fbclid', 'gclid',
                    'msclkid', 'ref', 'referrer', 'timestamp', 'cachebuster'}
    
    filtered_params = {k: v for k, v in query_params.items() 
                      if k.lower() not in exclude_keys}
    
    # Reconstruct normalized query string
    normalized_query = '&'.join(
        f"{k}={''.join(v)}" for k, v in sorted(filtered_params.items())
    )
    
    # Return scheme + netloc + path + normalized query
    normalized_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
    if normalized_query:
        normalized_url += f"?{normalized_query}"
    
    return normalized_url


def compute_request_hash(url: str, method: str, body_text: str) -> str:
    """Compute a hash for request deduplication."""
    normalized_url = normalize_url_for_dedup(url)
    body_hash = hashlib.md5(body_text.encode()).hexdigest()[:8]
    combined = f"{method.upper()}:{normalized_url}:{body_hash}"
    return hashlib.md5(combined.encode()).hexdigest()[:12]


@dataclass
class NetworkCall:
    started_at: str
    method: str
    status: int
    duration_ms: float
    url: str
    domain: str
    category: str
    confidence: str
    endpoint_score: int
    graphql_operation: str | None
    request_terms: list[str]
    result_count: int | None
    product_names: list[str]
    colors: list[str]
    request_snippet: str
    response_snippet: str
    wait_ms: float
    blocked_ms: float
    dns_ms: float
    connect_ms: float
    ssl_ms: float
    send_ms: float
    receive_ms: float
    size_bytes: int
    hang_reasons: list[str]
    dedup_id: str = ""  # Hash for deduplication
    occurrence_count: int = 1  # Number of duplicate requests merged
    merged_timings: dict[str, list[float]] = field(default_factory=dict)  # For aggregated timing stats


def load_har(file_path: str) -> dict[str, Any]:
    with open(file_path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def normalize_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def decode_content_text(content: dict[str, Any]) -> str:
    text = content.get("text", "")
    if not text:
        return ""

    if content.get("encoding") == "base64":
        try:
            return base64.b64decode(text).decode("utf-8", errors="replace")
        except Exception:
            return ""

    return text


def parse_json_like(text: str) -> Any:
    if not text:
        return None

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def parse_graphql_operation(body_json: Any) -> str | None:
    if not isinstance(body_json, dict):
        return None

    operation_name = body_json.get("operationName")
    if isinstance(operation_name, str) and operation_name.strip():
        return normalize_whitespace(operation_name)

    query_text = body_json.get("query")
    if isinstance(query_text, str):
        match = re.search(r"\b(query|mutation)\s+([A-Za-z0-9_]+)", query_text)
        if match:
            return match.group(2)

    return None


def flatten_values(data: Any) -> list[str]:
    values: list[str] = []

    if isinstance(data, dict):
        for key, value in data.items():
            if isinstance(value, (dict, list)):
                values.extend(flatten_values(value))
            elif value is not None:
                values.append(f"{key}={value}")
    elif isinstance(data, list):
        for item in data:
            values.extend(flatten_values(item))
    elif data is not None:
        values.append(str(data))

    return values


def extract_query_terms_from_object(data: Any) -> list[str]:
    matches: list[str] = []

    if isinstance(data, dict):
        for key, value in data.items():
            normalized_key = str(key).replace("-", "").replace("_", "").lower()
            if normalized_key in QUERY_FIELD_NAMES and value not in (None, ""):
                if isinstance(value, list):
                    matches.extend(normalize_whitespace(str(item)) for item in value if item not in (None, ""))
                elif isinstance(value, dict):
                    matches.extend(extract_query_terms_from_object(value))
                else:
                    matches.append(normalize_whitespace(str(value)))
            elif isinstance(value, (dict, list)):
                matches.extend(extract_query_terms_from_object(value))

    elif isinstance(data, list):
        for item in data:
            matches.extend(extract_query_terms_from_object(item))

    return [term for term in matches if term]


def extract_request_details(entry: dict[str, Any]) -> tuple[str, str, list[str], int]:
    request = entry.get("request", {})
    url = request.get("url", "")
    parsed_url = urlparse(url)
    query_params = parse_qs(parsed_url.query)
    raw_query_terms = []

    request_score = 0
    url_lower = unquote(url).lower()
    if any(hint in url_lower for hint in SEARCH_HINTS):
        request_score += 3

    raw_query_terms.extend(
        normalize_whitespace(value)
        for key, values in query_params.items()
        if key.replace("-", "").replace("_", "").lower() in QUERY_FIELD_NAMES
        for value in values
    )
    if raw_query_terms:
        request_score += 3

    body_text = request.get("postData", {}).get("text", "")
    body_json = parse_json_like(body_text)
    raw_query_terms.extend(extract_query_terms_from_object(body_json))
    if body_json and extract_query_terms_from_object(body_json):
        request_score += 2

    request_snippet_parts = [unquote(parsed_url.path)]
    if query_params:
        compact_params = ", ".join(
            f"{key}={';'.join(values[:2])}" for key, values in list(query_params.items())[:5]
        )
        request_snippet_parts.append(compact_params)
    elif body_text:
        request_snippet_parts.append(normalize_whitespace(body_text[:250]))

    return url, normalize_whitespace(" | ".join(part for part in request_snippet_parts if part)), dedupe(raw_query_terms), request_score


def classify_request(
    url: str,
    method: str,
    request_terms: list[str],
    request_score: int,
    response_text: str,
    result_count: int | None,
    graphql_operation: str | None,
) -> tuple[str, str, int]:
    parsed_url = urlparse(url)
    domain = (parsed_url.netloc or "").lower()
    path = unquote(parsed_url.path or "/").lower()
    operation = (graphql_operation or "").lower()
    request_text = " ".join([url.lower(), operation, " ".join(term.lower() for term in request_terms)])
    response_lower = response_text.lower()

    if path.endswith(STATIC_EXTENSIONS):
        return "Static Assets", "LOW", 0

    if any(hint in domain for hint in TELEMETRY_DOMAIN_HINTS) or any(hint in path for hint in TELEMETRY_PATH_HINTS):
        return "Telemetry / Analytics", "LOW", 1

    if any(hint in domain for hint in ADS_DOMAIN_HINTS) or any(hint in path for hint in ADS_PATH_HINTS):
        return "Ads / Marketing", "LOW", 1

    if any(hint in domain for hint in UTILITY_DOMAIN_HINTS):
        return "Unknown", "LOW", 1

    if any(hint in path for hint in ADS_PATH_HINTS):
        return "Ads / Marketing", "LOW", 1

    if graphql_operation:
        if any(hint in operation for hint in RECOMMENDATION_OPERATION_HINTS):
            return "Autocomplete / Recommendations", "HIGH", 8
        if any(hint in operation for hint in SEARCH_OPERATION_HINTS):
            return "Search", "HIGH", 9
        if any(hint in operation for hint in NON_SEARCH_GRAPHQL_HINTS):
            return "GraphQL (non-search)", "HIGH", 7
        if request_terms:
            return "Search", "MEDIUM", 6
        return "GraphQL (non-search)", "MEDIUM", 4

    search_hint_in_request = any(hint in request_text for hint in SEARCH_HINTS)
    recommendation_hint_in_request = any(
        hint in request_text for hint in ("recommend", "recommendation", "autocomplete", "suggest")
    )

    if search_hint_in_request and (request_terms or result_count is not None or "search" in path):
        confidence = "HIGH" if request_terms and result_count is not None else "MEDIUM"
        return "Search", confidence, max(request_score, 6)

    if recommendation_hint_in_request and (result_count is not None or "suggest" in path):
        confidence = "HIGH" if result_count is not None else "MEDIUM"
        return "Autocomplete / Recommendations", confidence, max(request_score, 5)

    if "/graphql" in path or method.upper() == "POST" and "graphql" in request_text:
        return "GraphQL (non-search)", "MEDIUM", max(request_score, 4)

    if result_count is not None and any(hint in response_lower for hint in ("products", "results")) and request_terms:
        return "Search", "MEDIUM", max(request_score, 5)

    return "Unknown", "LOW", request_score


def should_include_call(category: str) -> bool:
    return category in {
        "Search",
        "Autocomplete / Recommendations",
        "GraphQL (non-search)",
        "Telemetry / Analytics",
        "Unknown",
    }


def format_endpoint_label(call: NetworkCall) -> str:
    parsed_url = urlparse(call.url)
    label = f"{call.method} {parsed_url.path or '/'}"
    if call.graphql_operation:
        label = f"{label} ({call.graphql_operation})"
    return label


def dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []

    for value in values:
        normalized = value.strip()
        lowered = normalized.lower()
        if normalized and lowered not in seen:
            seen.add(lowered)
            result.append(normalized)

    return result


def extract_result_count(payload: Any) -> int | None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            normalized_key = str(key).replace("_", "").replace("-", "").lower()
            if normalized_key in RESULT_KEYS and isinstance(value, int):
                return value

        for key in PRODUCT_LIST_KEYS:
            if isinstance(payload.get(key), list):
                return len(payload[key])

        for value in payload.values():
            count = extract_result_count(value)
            if count is not None:
                return count

    elif isinstance(payload, list):
        for item in payload:
            count = extract_result_count(item)
            if count is not None:
                return count

    return None


def collect_values(payload: Any, keys: tuple[str, ...], limit: int = 5) -> list[str]:
    matches: list[str] = []
    lowered_keys = {key.lower() for key in keys}

    def walk(node: Any) -> None:
        if len(matches) >= limit:
            return
        if isinstance(node, dict):
            for key, value in node.items():
                normalized_key = str(key).replace("_", "").replace("-", "").lower()
                if normalized_key in lowered_keys and value not in (None, ""):
                    matches.append(normalize_whitespace(str(value)))
                    if len(matches) >= limit:
                        return
                if isinstance(value, (dict, list)):
                    walk(value)
                    if len(matches) >= limit:
                        return
        elif isinstance(node, list):
            for item in node:
                walk(item)
                if len(matches) >= limit:
                    return

    walk(payload)
    return dedupe(matches)


def safe_timing(timings: dict[str, Any], key: str) -> float:
    value = timings.get(key, -1)
    if isinstance(value, (int, float)) and value >= 0:
        return float(value)
    return 0.0


def describe_hang_reasons(
    status: int,
    duration_ms: float,
    wait_ms: float,
    blocked_ms: float,
    connect_ms: float,
    receive_ms: float,
) -> list[str]:
    reasons: list[str] = []
    if status == 0:
        reasons.append("request did not complete (status=0)")
    if status >= 500:
        reasons.append("server error response")
    elif status >= 400:
        reasons.append("client or auth error response")
    if duration_ms >= 8000:
        reasons.append("very slow total duration")
    elif duration_ms >= 3000:
        reasons.append("slow total duration")
    if wait_ms >= 4000:
        reasons.append("long server wait (TTFB)")
    if blocked_ms >= 1000:
        reasons.append("high queue/blocked time")
    if connect_ms >= 1500:
        reasons.append("slow network connect")
    if receive_ms >= 2000:
        reasons.append("slow response download")
    return reasons


def hang_severity(call: NetworkCall) -> float:
    score = 0.0
    score += min(call.duration_ms / 1000.0, 20.0)
    score += min(call.wait_ms / 800.0, 20.0)
    score += min(call.blocked_ms / 400.0, 10.0)
    score += min(call.connect_ms / 600.0, 10.0)
    if call.status == 0:
        score += 12.0
    elif call.status >= 500:
        score += 8.0
    elif call.status >= 400:
        score += 4.0
    return round(score, 1)


def build_hang_core_findings(results: dict[str, Any], network_calls: list[NetworkCall]) -> list[str]:
    findings: list[str] = []
    total = max(results["total_requests"], 1)
    failed = results["failed_call_count"]
    slow = results["slow_call_count"]
    stalled = results["stalled_call_count"]

    findings.append(
        f"Hanging Profile: failed={failed}/{total} ({(failed/total)*100:.1f}%), "
        f"slow={slow}/{total} ({(slow/total)*100:.1f}%), stalled={stalled}/{total} ({(stalled/total)*100:.1f}%)."
    )

    if results["failure_reason_counts"]:
        top_reason, top_count = results["failure_reason_counts"].most_common(1)[0]
        findings.append(f"Primary Hang Signal: {top_reason} ({top_count} request(s)).")

    hang_candidates = [call for call in network_calls if call.hang_reasons]
    if hang_candidates:
        worst = max(hang_candidates, key=hang_severity)
        findings.append(
            "Top Hang Suspect: "
            f"{worst.method} {worst.status} {worst.duration_ms:.0f}ms {worst.url} "
            f"(severity={hang_severity(worst):.1f}; reasons: {', '.join(worst.hang_reasons)})."
        )

    return findings


def analyze_har(file_path: str) -> dict[str, Any]:
    data = load_har(file_path)
    entries = data.get("log", {}).get("entries", [])
    
    logging.info(f"Processing HAR file with {len(entries)} total entries")

    # Phase 1: Initial processing with dedup tracking
    request_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    status_counts = Counter()
    domain_counts = Counter()
    category_counts = Counter()
    failure_reason_counts = Counter()
    endpoint_counts_by_category: dict[str, Counter] = {}
    domain_counts_by_category: dict[str, Counter] = {}
    
    # Track duplicates for logging
    dedup_map: dict[str, str] = {}  # Maps dedup_id to first occurrence URL
    duplicate_count = 0

    for entry_idx, entry in enumerate(entries):
        request = entry.get("request", {})
        response = entry.get("response", {})
        url, _, _, _ = extract_request_details(entry)
        
        # Compute dedup ID
        body_text = request.get("postData", {}).get("text", "")
        method = request.get("method", "GET")
        dedup_id = compute_request_hash(url, method, body_text)
        
        # Track duplicates
        if dedup_id not in dedup_map:
            dedup_map[dedup_id] = url
        else:
            duplicate_count += 1
        
        # Group by dedup ID for later merging
        request_groups[dedup_id].append({
            "entry_index": entry_idx,
            "entry": entry,
            "dedup_id": dedup_id,
            "url": url,
            "method": method
        })
    
    logging.info(f"Found {duplicate_count} duplicate requests ({len(request_groups)} unique endpoints)")

    # Phase 2: Process unique requests, merging duplicates
    network_calls: list[NetworkCall] = []
    
    for dedup_id, request_entries in request_groups.items():
        # Use first occurrence for analysis
        first_entry = request_entries[0]["entry"]
        request = first_entry.get("request", {})
        response = first_entry.get("response", {})
        url, request_snippet, request_terms, endpoint_score = extract_request_details(first_entry)
        domain = urlparse(url).netloc or "unknown"

        domain_counts[domain] += 1
        status = int(response.get("status", 0) or 0)
        status_counts[status] += 1

        response_text = decode_content_text(response.get("content", {}))
        response_json = parse_json_like(response_text)
        body_json = parse_json_like(request.get("postData", {}).get("text", ""))
        graphql_operation = parse_graphql_operation(body_json)
        timings = first_entry.get("timings", {}) if isinstance(first_entry.get("timings", {}), dict) else {}
        wait_ms = safe_timing(timings, "wait")
        blocked_ms = safe_timing(timings, "blocked")
        dns_ms = safe_timing(timings, "dns")
        connect_ms = safe_timing(timings, "connect")
        ssl_ms = safe_timing(timings, "ssl")
        send_ms = safe_timing(timings, "send")
        receive_ms = safe_timing(timings, "receive")
        size_bytes = int(response.get("bodySize", 0) or 0)
        response_score = 1 if extract_result_count(response_json) is not None else 0
        response_score += 1 if any(hint in response_text.lower() for hint in ("products", "results", "search")) else 0
        total_score = endpoint_score + response_score

        result_count = extract_result_count(response_json)
        product_names = collect_values(response_json, PRODUCT_NAME_KEYS)
        colors = collect_values(response_json, COLOR_KEYS)
        category, confidence, classified_score = classify_request(
            url,
            request.get("method", "GET"),
            request_terms,
            total_score,
            response_text,
            result_count,
            graphql_operation,
        )
        total_score = max(total_score, classified_score)

        hang_reasons = describe_hang_reasons(
            status,
            float(first_entry.get("time", 0) or 0),
            wait_ms,
            blocked_ms,
            connect_ms,
            receive_ms,
        )
        for reason in hang_reasons:
            failure_reason_counts[reason] += 1

        endpoint = format_endpoint_label(
            NetworkCall(
                started_at="",
                method=request.get("method", "GET"),
                status=status,
                duration_ms=0.0,
                url=url,
                domain=domain,
                category=category,
                confidence=confidence,
                endpoint_score=total_score,
                graphql_operation=graphql_operation,
                request_terms=[],
                result_count=None,
                product_names=[],
                colors=[],
                request_snippet="",
                response_snippet="",
                wait_ms=0.0,
                blocked_ms=0.0,
                dns_ms=0.0,
                connect_ms=0.0,
                ssl_ms=0.0,
                send_ms=0.0,
                receive_ms=0.0,
                size_bytes=0,
                hang_reasons=[],
                dedup_id=dedup_id,
            )
        )
        category_counts[category] += 1
        endpoint_counts_by_category.setdefault(category, Counter())[endpoint] += 1
        domain_counts_by_category.setdefault(category, Counter())[domain] += 1

        if total_score < 3 and not request_terms and category == "Unknown":
            continue

        if not should_include_call(category):
            continue

        # Aggregate timing data from all duplicate requests
        merged_timings = defaultdict(list)
        for req_entry in request_entries:
            entry_timings = req_entry["entry"].get("timings", {})
            if isinstance(entry_timings, dict):
                merged_timings["wait"].append(safe_timing(entry_timings, "wait"))
                merged_timings["blocked"].append(safe_timing(entry_timings, "blocked"))
                merged_timings["dns"].append(safe_timing(entry_timings, "dns"))
                merged_timings["connect"].append(safe_timing(entry_timings, "connect"))
                merged_timings["ssl"].append(safe_timing(entry_timings, "ssl"))
                merged_timings["send"].append(safe_timing(entry_timings, "send"))
                merged_timings["receive"].append(safe_timing(entry_timings, "receive"))

        network_calls.append(
            NetworkCall(
                started_at=first_entry.get("startedDateTime", ""),
                method=request.get("method", "GET"),
                status=status,
                duration_ms=float(first_entry.get("time", 0) or 0),
                url=url,
                domain=domain,
                category=category,
                confidence=confidence,
                endpoint_score=total_score,
                graphql_operation=graphql_operation,
                request_terms=request_terms,
                result_count=result_count,
                product_names=product_names,
                colors=colors,
                request_snippet=request_snippet,
                response_snippet=normalize_whitespace(response_text[:260]),
                wait_ms=wait_ms,
                blocked_ms=blocked_ms,
                dns_ms=dns_ms,
                connect_ms=connect_ms,
                ssl_ms=ssl_ms,
                send_ms=send_ms,
                receive_ms=receive_ms,
                size_bytes=size_bytes,
                hang_reasons=hang_reasons,
                dedup_id=dedup_id,
                occurrence_count=len(request_entries),
                merged_timings=dict(merged_timings),
            )
        )

    failed_calls = [call for call in network_calls if call.status == 0 or call.status >= 400]
    slow_calls = [call for call in network_calls if call.duration_ms >= 3000]
    stalled_calls = [call for call in network_calls if call.wait_ms >= 4000 or call.blocked_ms >= 1000]

    return {
        "file_path": file_path,
        "total_requests": len(entries),
        "unique_endpoints": len(network_calls),
        "duplicate_requests": duplicate_count,
        "top_domains": domain_counts.most_common(5),
        "status_counts": status_counts,
        "category_counts": category_counts,
        "failure_reason_counts": failure_reason_counts,
        "failed_call_count": len(failed_calls),
        "slow_call_count": len(slow_calls),
        "stalled_call_count": len(stalled_calls),
        "top_endpoints_by_category": {
            category: counter.most_common(10)
            for category, counter in endpoint_counts_by_category.items()
        },
        "top_domains_by_category": {
            category: counter.most_common(5)
            for category, counter in domain_counts_by_category.items()
        },
        "network_calls": sorted(network_calls, key=lambda call: (call.started_at, call.duration_ms)),
    }


def print_summary(results: dict[str, Any], limit: int) -> None:
    network_calls: list[NetworkCall] = results["network_calls"]
    hang_candidates = [call for call in network_calls if call.hang_reasons]
    ranked_hang_calls = sorted(hang_candidates, key=hang_severity, reverse=True)

    print("\n===== HAR HANG DIAGNOSTIC =====\n")
    print(f"HAR File: {results['file_path']}")
    print(f"Total Requests in HAR: {results['total_requests']}")
    print(f"Unique Endpoints (after dedup): {results['unique_endpoints']}")
    print(f"Duplicate Requests Detected: {results['duplicate_requests']}")
    dedup_reduction = (results['duplicate_requests'] / max(results['total_requests'], 1)) * 100
    print(f"Redundancy Eliminated: {dedup_reduction:.1f}% reduction")
    print(f"Classified Calls: {len(network_calls)}")
    print(f"Failed Calls (status 0 or >=400): {results['failed_call_count']}")
    print(f"Slow Calls (>=3000ms): {results['slow_call_count']}")
    print(f"Stalled Calls (wait>=4000ms or blocked>=1000ms): {results['stalled_call_count']}")

    print("\nCore Hang Findings:")
    for finding in build_hang_core_findings(results, network_calls):
        print(f"  - {finding}")

    print("\nTop Hanging Requests:")
    if ranked_hang_calls:
        for call in ranked_hang_calls[:5]:
            occ_str = f" (×{call.occurrence_count})" if call.occurrence_count > 1 else ""
            print(
                "  "
                f"severity={hang_severity(call):.1f} | {call.method} {call.status} {call.duration_ms:.0f}ms | "
                f"{urlparse(call.url).path or '/'}{occ_str}"
            )
            print(f"     reasons: {', '.join(call.hang_reasons)}")
    else:
        print("  None")

    print("\nTop Domains:")
    if results["top_domains"]:
        for domain, count in results["top_domains"]:
            print(f"  {domain}: {count}")
    else:
        print("  None")

    print("\nCategory Summary:")
    if results["category_counts"]:
        for category, count in results["category_counts"].most_common():
            print(f"  {category}: {count}")
    else:
        print("  None")

    print("\nStandard Failure Signals:")
    if results["failure_reason_counts"]:
        for reason, count in results["failure_reason_counts"].most_common(8):
            print(f"  {count}x  {reason}")
    else:
        print("  None")

    for category in (
        "Search",
        "Autocomplete / Recommendations",
        "GraphQL (non-search)",
        "Telemetry / Analytics",
        "Ads / Marketing",
        "Static Assets",
        "Unknown",
    ):
        endpoints = results["top_endpoints_by_category"].get(category, [])
        if not endpoints:
            continue
        print(f"\nTop {category} Endpoints:")
        for endpoint, count in endpoints[:5]:
            print(f"  {count}x  {endpoint}")

    print("\nLinear Request Timeline:")
    if not network_calls:
        print("  None")
        return

    for index, call in enumerate(network_calls[:limit], start=1):
        occ_str = f" (×{call.occurrence_count})" if call.occurrence_count > 1 else ""
        print(
            f"\n  [{index}] {call.method} {call.status} {call.duration_ms:.0f}ms  {call.url}{occ_str}"
        )
        print(
            f"      Classification: {call.category} | {call.confidence} confidence | score={call.endpoint_score}"
        )
        if call.graphql_operation:
            print(f"      GraphQL Operation: {call.graphql_operation}")
        if call.request_terms:
            print(f"      Request Terms: {', '.join(call.request_terms)}")
        print(f"      Request Snippet: {call.request_snippet}")
        if call.result_count is not None:
            print(f"      Result Count: {call.result_count}")
        
        # Display aggregate timing data if duplicates exist
        if call.occurrence_count > 1 and call.merged_timings:
            print(
                "      Timings(ms) - avg: "
                f"blocked={sum(call.merged_timings.get('blocked', [0]))/max(call.occurrence_count, 1):.0f}, "
                f"dns={sum(call.merged_timings.get('dns', [0]))/max(call.occurrence_count, 1):.0f}, "
                f"connect={sum(call.merged_timings.get('connect', [0]))/max(call.occurrence_count, 1):.0f}, "
                f"ssl={sum(call.merged_timings.get('ssl', [0]))/max(call.occurrence_count, 1):.0f}, "
                f"send={sum(call.merged_timings.get('send', [0]))/max(call.occurrence_count, 1):.0f}, "
                f"wait={sum(call.merged_timings.get('wait', [0]))/max(call.occurrence_count, 1):.0f}, "
                f"receive={sum(call.merged_timings.get('receive', [0]))/max(call.occurrence_count, 1):.0f}"
            )
        else:
            print(
                "      Timings(ms): "
                f"blocked={call.blocked_ms:.0f}, dns={call.dns_ms:.0f}, connect={call.connect_ms:.0f}, "
                f"ssl={call.ssl_ms:.0f}, send={call.send_ms:.0f}, wait={call.wait_ms:.0f}, receive={call.receive_ms:.0f}"
            )
        
        if call.size_bytes > 0:
            print(f"      Response Size: {call.size_bytes} bytes")
        if call.product_names:
            print(f"      Product Names: {', '.join(call.product_names[:4])}")
        if call.colors:
            print(f"      Colors: {', '.join(call.colors[:4])}")
        if call.response_snippet:
            print(f"      Response Snippet: {call.response_snippet}")
        if call.hang_reasons:
            print(f"      Hang Indicators: {', '.join(call.hang_reasons)}")
        if call.occurrence_count > 1:
            print(f"      ⚠️  DUPLICATE: This request appeared {call.occurrence_count} times in the capture")


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Analyze HAR files linearly for app hang indicators and standard failure data.",
    )
    parser.add_argument("har_file", help="Path to the HAR file to inspect")
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum number of timeline calls to print.",
    )
    return parser


def main(argv: list[str]) -> int:
    parser = build_argument_parser()
    args = parser.parse_args(argv)

    try:
        results = analyze_har(args.har_file)
    except FileNotFoundError:
        print(f"File not found: {args.har_file}", file=sys.stderr)
        return 1
    except json.JSONDecodeError as exc:
        print(f"Invalid HAR JSON: {exc}", file=sys.stderr)
        return 1

    print_summary(results, max(args.limit, 1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
