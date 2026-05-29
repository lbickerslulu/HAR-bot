import argparse
import base64
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse


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


@dataclass
class SearchCall:
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
    keyword_signals: dict[str, dict[str, Any]]


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


def should_include_call(category: str, keywords: list[str], keyword_matches: bool) -> bool:
    if keywords:
        return keyword_matches
    return category in {"Search", "Autocomplete / Recommendations", "GraphQL (non-search)"}


def format_endpoint_label(call: SearchCall) -> str:
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


def compute_keyword_signals(keyword: str, request_text: str, response_text: str, product_names: list[str], colors: list[str]) -> dict[str, Any]:
    keyword_lower = keyword.lower()
    tokens = [token for token in re.findall(r"[a-z0-9]+", keyword_lower) if token]
    slash_variants = {
        f"{keyword_lower}/{keyword_lower}",
        f"{keyword_lower} / {keyword_lower}",
    }
    response_lower = response_text.lower()
    request_lower = request_text.lower()
    return {
        "request_contains_keyword": keyword_lower in request_lower,
        "response_contains_keyword": keyword_lower in response_lower,
        "response_contains_slash_variant": any(variant in response_lower for variant in slash_variants),
        "all_tokens_in_response": bool(tokens) and all(token in response_lower for token in tokens),
        "catalog_exact_hits": sum(keyword_lower in value.lower() for value in product_names + colors),
        "catalog_token_hits": sum(any(token in value.lower() for token in tokens) for value in product_names + colors),
    }


def is_keyword_match(keyword: str, request_terms: list[str], request_snippet: str, response_snippet: str, product_names: list[str], colors: list[str]) -> bool:
    haystacks = request_terms + [request_snippet, response_snippet] + product_names + colors
    target = keyword.lower()
    return any(target in value.lower() for value in haystacks if value)


def analyze_har(file_path: str, keywords: list[str] | None = None) -> dict[str, Any]:
    data = load_har(file_path)
    entries = data.get("log", {}).get("entries", [])

    search_calls: list[SearchCall] = []
    status_counts = Counter()
    domain_counts = Counter()
    category_counts = Counter()
    endpoint_counts_by_category: dict[str, Counter] = {}
    domain_counts_by_category: dict[str, Counter] = {}

    for entry in entries:
        request = entry.get("request", {})
        response = entry.get("response", {})
        url, request_snippet, request_terms, endpoint_score = extract_request_details(entry)
        domain = urlparse(url).netloc or "unknown"

        domain_counts[domain] += 1
        status = int(response.get("status", 0) or 0)
        status_counts[status] += 1

        response_text = decode_content_text(response.get("content", {}))
        response_json = parse_json_like(response_text)
        body_json = parse_json_like(request.get("postData", {}).get("text", ""))
        graphql_operation = parse_graphql_operation(body_json)
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

        keyword_signals = {}
        keyword_matches = False
        if keywords:
            for keyword in keywords:
                keyword_signals[keyword] = compute_keyword_signals(
                    keyword,
                    " | ".join(request_terms + [request_snippet]),
                    response_text,
                    product_names,
                    colors,
                )

            keyword_matches = any(
                is_keyword_match(keyword, request_terms, request_snippet, response_text[:500], product_names, colors)
                for keyword in keywords
            )

        endpoint = format_endpoint_label(
            SearchCall(
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
                keyword_signals={},
            )
        )
        category_counts[category] += 1
        endpoint_counts_by_category.setdefault(category, Counter())[endpoint] += 1
        domain_counts_by_category.setdefault(category, Counter())[domain] += 1

        if total_score < 3 and not request_terms and category == "Unknown":
            continue

        if not should_include_call(category, keywords or [], keyword_matches):
            continue

        search_calls.append(
            SearchCall(
                started_at=entry.get("startedDateTime", ""),
                method=request.get("method", "GET"),
                status=status,
                duration_ms=float(entry.get("time", 0) or 0),
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
                keyword_signals=keyword_signals,
            )
        )

    return {
        "file_path": file_path,
        "total_requests": len(entries),
        "top_domains": domain_counts.most_common(5),
        "status_counts": status_counts,
        "category_counts": category_counts,
        "top_endpoints_by_category": {
            category: counter.most_common(10)
            for category, counter in endpoint_counts_by_category.items()
        },
        "top_domains_by_category": {
            category: counter.most_common(5)
            for category, counter in domain_counts_by_category.items()
        },
        "search_calls": sorted(search_calls, key=lambda call: (call.started_at, call.duration_ms)),
        "keywords": keywords or [],
    }


def summarize_keyword_diagnostics(search_calls: list[SearchCall], keywords: list[str]) -> list[str]:
    diagnostics: list[str] = []

    for keyword in keywords:
        matched_calls = [call for call in search_calls if keyword in call.keyword_signals]
        if not matched_calls:
            diagnostics.append(f"- {keyword}: no matching search calls were found in the HAR.")
            continue

        zero_result_calls = [call for call in matched_calls if call.result_count == 0]
        slash_variant_hits = sum(
            call.keyword_signals[keyword]["response_contains_slash_variant"] for call in matched_calls
        )
        exact_hits = sum(call.keyword_signals[keyword]["response_contains_keyword"] for call in matched_calls)
        broad_calls = [call for call in matched_calls if call.result_count is not None and call.result_count >= 50]
        weak_catalog_hits = [call for call in broad_calls if call.keyword_signals[keyword]["catalog_exact_hits"] == 0]
        multi_term_failure = len(keyword.split()) > 1 and zero_result_calls

        parts = [f"- {keyword}: {len(matched_calls)} matching search call(s)"]
        if zero_result_calls:
            parts.append(f"{len(zero_result_calls)} returned zero results")
        if slash_variant_hits > exact_hits:
            parts.append("response favored slash-form canonical values over the plain keyword")
        if weak_catalog_hits:
            parts.append("broad result sets showed weak exact catalog-name hits")
        if multi_term_failure:
            parts.append("multi-term query behavior looks stricter than single-token matching")

        diagnostics.append("; ".join(parts) + ".")

    return diagnostics


def print_summary(results: dict[str, Any], limit: int) -> None:
    search_calls: list[SearchCall] = results["search_calls"]

    print("\n===== SEARCH KEYWORD DIAGNOSTIC =====\n")
    print(f"HAR File: {results['file_path']}")
    print(f"Total Requests: {results['total_requests']}")
    print(f"Classified Calls: {len(search_calls)}")

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

    if results["keywords"]:
        print("\nKeyword Diagnostics:")
        for line in summarize_keyword_diagnostics(search_calls, results["keywords"]):
            print(f"  {line}")

    print("\nDetailed Calls:")
    if not search_calls:
        print("  None")
        return

    for index, call in enumerate(search_calls[:limit], start=1):
        print(
            f"\n  [{index}] {call.method} {call.status} {call.duration_ms:.0f}ms  {call.url}"
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
        if call.product_names:
            print(f"      Product Names: {', '.join(call.product_names[:4])}")
        if call.colors:
            print(f"      Colors: {', '.join(call.colors[:4])}")
        if call.response_snippet:
            print(f"      Response Snippet: {call.response_snippet}")

        for keyword, signals in call.keyword_signals.items():
            signal_parts = []
            if signals["request_contains_keyword"]:
                signal_parts.append("keyword in request")
            if signals["response_contains_keyword"]:
                signal_parts.append("keyword in response")
            if signals["response_contains_slash_variant"]:
                signal_parts.append("slash variant in response")
            if signals["all_tokens_in_response"]:
                signal_parts.append("all tokens present")
            if signals["catalog_exact_hits"]:
                signal_parts.append(f"catalog exact hits={signals['catalog_exact_hits']}")
            if signals["catalog_token_hits"]:
                signal_parts.append(f"catalog token hits={signals['catalog_token_hits']}")

            if signal_parts:
                print(f"      Signals for '{keyword}': {', '.join(signal_parts)}")


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Analyze HAR files for keyword and search endpoint diagnostics.",
    )
    parser.add_argument("har_file", help="Path to the HAR file to inspect")
    parser.add_argument(
        "-k",
        "--keyword",
        action="append",
        default=[],
        help="Keyword to trace through search requests and responses. Repeat for multiple values.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum number of detailed search calls to print.",
    )
    return parser


def main(argv: list[str]) -> int:
    parser = build_argument_parser()
    args = parser.parse_args(argv)

    try:
        results = analyze_har(args.har_file, dedupe(args.keyword))
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
