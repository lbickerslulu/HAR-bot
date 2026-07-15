import argparse
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


BUILTIN_NET_ERROR_NAMES: dict[int, str] = {
    -2: "ERR_FAILED",
    -3: "ERR_ABORTED",
    -7: "ERR_TIMED_OUT",
    -21: "ERR_NETWORK_CHANGED",
    -100: "ERR_CONNECTION_CLOSED",
    -101: "ERR_CONNECTION_RESET",
    -102: "ERR_CONNECTION_REFUSED",
    -103: "ERR_CONNECTION_ABORTED",
    -104: "ERR_CONNECTION_FAILED",
    -105: "ERR_NAME_NOT_RESOLVED",
    -106: "ERR_INTERNET_DISCONNECTED",
    -107: "ERR_SSL_PROTOCOL_ERROR",
    -108: "ERR_ADDRESS_INVALID",
    -109: "ERR_ADDRESS_UNREACHABLE",
    -110: "ERR_SSL_CLIENT_AUTH_CERT_NEEDED",
    -111: "ERR_TUNNEL_CONNECTION_FAILED",
    -113: "ERR_SSL_VERSION_OR_CIPHER_MISMATCH",
    -118: "ERR_CONNECTION_TIMED_OUT",
    -130: "ERR_PROXY_CONNECTION_FAILED",
    -137: "ERR_NAME_RESOLUTION_FAILED",
    -200: "ERR_CERT_COMMON_NAME_INVALID",
    -201: "ERR_CERT_DATE_INVALID",
    -202: "ERR_CERT_AUTHORITY_INVALID",
    -206: "ERR_CERT_REVOKED",
    -324: "ERR_EMPTY_RESPONSE",
    -336: "ERR_NO_SUPPORTED_PROXIES",
    -337: "ERR_SSL_PINNED_KEY_NOT_IN_CERT_CHAIN",
    -352: "ERR_INSECURE_RESPONSE",
}


ERROR_KEY_HINTS = {
    "neterror",
    "neterrorcode",
    "net_error",
    "net_error_code",
    "quicerror",
    "quicconnectionerror",
    "quic_error",
    "quic_connection_error",
}


URL_KEY_HINTS = {
    "url",
    "origin",
    "host",
    "hostname",
    "server",
    "serveraddress",
    "remoteaddress",
    "proxyserver",
    "proxy_server",
}


LOCAL_ISSUE_MARKERS = (
    "ERR_CERT",
    "ERR_SSL",
    "ERR_PROXY",
    "ERR_TUNNEL_CONNECTION_FAILED",
    "ERR_INSECURE_RESPONSE",
    "ERR_CACHE",
    "ERR_BLOCKED_BY_CLIENT",
    "ERR_BLOCKED_BY_ADMINISTRATOR",
)


NETWORK_ISSUE_MARKERS = (
    "ERR_NAME_NOT_RESOLVED",
    "ERR_DNS",
    "ERR_TIMED_OUT",
    "ERR_CONNECTION_TIMED_OUT",
    "ERR_CONNECTION_REFUSED",
    "ERR_CONNECTION_RESET",
    "ERR_ADDRESS_UNREACHABLE",
    "ERR_INTERNET_DISCONNECTED",
    "ERR_NETWORK_CHANGED",
)


@dataclass
class ErrorEvent:
    index: int
    time: str
    event_type: str
    phase: str
    source_type: str
    host: str
    error_name: str



def normalize_key(value: str) -> str:
    return value.replace("-", "").replace("_", "").strip().lower()



def try_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        if stripped.startswith("+") or stripped.startswith("-"):
            sign = stripped[0]
            body = stripped[1:]
            if body.isdigit():
                return int(sign + body)
        if stripped.isdigit():
            return int(stripped)
    return None



def load_netlog(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)

    if not isinstance(payload, dict):
        raise ValueError("Unsupported NetLog format. Expected JSON object.")

    events = payload.get("events")
    if not isinstance(events, list):
        events = payload.get("logEvents")

    if not isinstance(events, list):
        raise ValueError("Could not find NetLog events list under 'events' or 'logEvents'.")

    constants = payload.get("constants")
    if not isinstance(constants, dict):
        constants = {}

    return {
        "payload": payload,
        "constants": constants,
        "events": events,
    }



def invert_int_map(raw: Any) -> dict[int, str]:
    if not isinstance(raw, dict):
        return {}

    result: dict[int, str] = {}
    for name, code in raw.items():
        value = try_int(code)
        if value is None:
            continue
        result[value] = str(name)
    return result



def name_for_value(raw: Any, mapping: dict[int, str], fallback_prefix: str) -> str:
    value = try_int(raw)
    if value is None:
        if isinstance(raw, str) and raw:
            return raw
        return f"{fallback_prefix}_UNKNOWN"
    return mapping.get(value, f"{fallback_prefix}_{value}")



def iter_kv(node: Any):
    if isinstance(node, dict):
        for key, value in node.items():
            yield str(key), value
            yield from iter_kv(value)
    elif isinstance(node, list):
        for item in node:
            yield from iter_kv(item)



def host_from_value(value: str) -> str | None:
    text = value.strip()
    if not text:
        return None

    parsed = urlparse(text)
    if parsed.hostname:
        return parsed.hostname.lower()

    # Host[:port] values are common in NetLog params.
    if ":" in text and "/" not in text:
        return text.split(":", 1)[0].strip().lower() or None

    if "/" not in text and " " not in text and "." in text:
        return text.lower()

    return None



def extract_host(params: Any) -> str:
    if not isinstance(params, (dict, list)):
        return "-"

    for key, value in iter_kv(params):
        nk = normalize_key(key)
        if nk not in URL_KEY_HINTS and "url" not in nk and "host" not in nk:
            continue

        if isinstance(value, str):
            host = host_from_value(value)
            if host:
                return host

    return "-"



def net_error_name(value: Any, net_error_map: dict[int, str]) -> str | None:
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("ERR_"):
            return text

    code = try_int(value)
    if code is None:
        return None
    if code >= 0:
        return None

    if code in net_error_map:
        return net_error_map[code]

    if code in BUILTIN_NET_ERROR_NAMES:
        return BUILTIN_NET_ERROR_NAMES[code]

    return f"NET_ERROR_{code}"



def extract_error_names(params: Any, net_error_map: dict[int, str]) -> list[str]:
    if not isinstance(params, (dict, list)):
        return []

    found: list[str] = []
    seen = set()

    for key, value in iter_kv(params):
        nk = normalize_key(key)
        if nk in ERROR_KEY_HINTS or "neterror" in nk:
            name = net_error_name(value, net_error_map)
            if name and name not in seen:
                seen.add(name)
                found.append(name)
            continue

        if isinstance(value, str) and value.startswith("ERR_"):
            if value not in seen:
                seen.add(value)
                found.append(value)

    return found



def categorize_event(event_type: str) -> str:
    value = event_type.upper()
    if "CERT" in value or "SSL" in value:
        return "TLS/Certificate"
    if "PROXY" in value or "PAC" in value:
        return "Proxy"
    if "DNS" in value or "HOST_RESOLVER" in value:
        return "DNS"
    if "SOCKET" in value or "CONNECT" in value or "TCP" in value:
        return "Connection"
    if "QUIC" in value:
        return "QUIC"
    if "HTTP" in value or "URL_REQUEST" in value:
        return "HTTP"
    return "Other"



def reinstall_assessment(error_counts: Counter[str], affected_hosts: set[str]) -> dict[str, Any]:
    total_failures = sum(error_counts.values())
    local_count = sum(
        qty
        for name, qty in error_counts.items()
        if any(marker in name for marker in LOCAL_ISSUE_MARKERS)
    )
    network_count = sum(
        qty
        for name, qty in error_counts.items()
        if any(marker in name for marker in NETWORK_ISSUE_MARKERS)
    )

    score = 0
    score += min(total_failures * 2, 40)
    score += min(local_count * 3, 35)
    score -= min(network_count * 2, 25)

    host_footprint = len(affected_hosts)
    if host_footprint >= 3 and local_count >= 5:
        score += 15
    elif host_footprint >= 2 and local_count >= 3:
        score += 8

    score = max(0, min(score, 100))

    if score >= 65:
        likelihood = "Likely browser-local issue"
        recommendation = "Reinstall/reset is a reasonable next step after one clean-profile reproduction test."
    elif score >= 40:
        likelihood = "Possible browser-local issue"
        recommendation = "Run a clean-profile + extensions-disabled test before reinstall."
    else:
        likelihood = "Reinstall unlikely to help"
        recommendation = "Prioritize endpoint, DNS, proxy, or network path troubleshooting."

    return {
        "score": score,
        "likelihood": likelihood,
        "recommendation": recommendation,
        "total_failures": total_failures,
        "local_signature_count": local_count,
        "network_signature_count": network_count,
        "affected_host_count": host_footprint,
    }



def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="View and summarize Chrome/Edge NetLog JSON to triage browser-local issues."
    )
    parser.add_argument("netlog_file", help="Path to a NetLog JSON export file")
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="How many error timeline rows to print (default: 20)",
    )
    parser.add_argument(
        "--json-output",
        help="Optional path to write a structured JSON summary",
    )
    return parser.parse_args()



def main() -> int:
    args = parse_args()
    netlog_path = Path(args.netlog_file)

    try:
        loaded = load_netlog(netlog_path)
    except FileNotFoundError:
        print(f"NetLog file not found: {netlog_path}")
        return 1
    except json.JSONDecodeError as exc:
        print(f"Invalid JSON in {netlog_path}: {exc}")
        return 1
    except ValueError as exc:
        print(str(exc))
        return 1

    constants = loaded["constants"]
    events = loaded["events"]
    payload = loaded["payload"]

    event_type_map = invert_int_map(constants.get("logEventTypes"))
    phase_map = invert_int_map(constants.get("logEventPhase"))
    source_type_map = invert_int_map(constants.get("logSourceType"))
    net_error_map = invert_int_map(constants.get("netError"))

    event_category_counts: Counter[str] = Counter()
    error_name_counts: Counter[str] = Counter()
    error_type_counts: Counter[str] = Counter()
    affected_hosts: set[str] = set()
    error_events: list[ErrorEvent] = []

    for index, event in enumerate(events, start=1):
        if not isinstance(event, dict):
            continue

        event_type = name_for_value(event.get("type"), event_type_map, "EVENT")
        phase = name_for_value(event.get("phase"), phase_map, "PHASE")

        source = event.get("source")
        source_type = "SOURCE_UNKNOWN"
        if isinstance(source, dict):
            source_type = name_for_value(source.get("type"), source_type_map, "SOURCE")

        params = event.get("params", {})
        host = extract_host(params)
        event_category_counts[categorize_event(event_type)] += 1

        error_names = extract_error_names(params, net_error_map)
        if not error_names:
            continue

        if host != "-":
            affected_hosts.add(host)

        for error_name in error_names:
            error_name_counts[error_name] += 1
            error_type_counts[event_type] += 1
            error_events.append(
                ErrorEvent(
                    index=index,
                    time=str(event.get("time", "")),
                    event_type=event_type,
                    phase=phase,
                    source_type=source_type,
                    host=host,
                    error_name=error_name,
                )
            )

    browser_hint = "unknown"
    client_info = constants.get("clientInfo")
    if isinstance(client_info, dict):
        name = str(client_info.get("name", "")).strip()
        version = str(client_info.get("version", "")).strip()
        if name and version:
            browser_hint = f"{name} {version}"
        elif name:
            browser_hint = name
    elif isinstance(payload.get("clientInfo"), dict):
        raw = payload["clientInfo"]
        name = str(raw.get("name", "")).strip()
        version = str(raw.get("version", "")).strip()
        if name and version:
            browser_hint = f"{name} {version}"
        elif name:
            browser_hint = name

    assessment = reinstall_assessment(error_name_counts, affected_hosts)

    print("===== NetLog Viewer =====")
    print("")
    print(f"File: {netlog_path}")
    print(f"Browser Hint: {browser_hint}")
    print(f"Total NetLog Events: {len(events)}")
    print(f"Events With Failures: {assessment['total_failures']}")
    print(f"Unique Error Types: {len(error_name_counts)}")
    print(f"Affected Hosts: {assessment['affected_host_count']}")

    print("\nTop Net Errors:")
    if error_name_counts:
        for error_name, qty in error_name_counts.most_common(10):
            print(f"  {qty}x  {error_name}")
    else:
        print("  None")

    print("\nTop Error Event Types:")
    if error_type_counts:
        for event_name, qty in error_type_counts.most_common(10):
            print(f"  {qty}x  {event_name}")
    else:
        print("  None")

    print("\nEvent Categories:")
    for category, qty in event_category_counts.most_common():
        print(f"  {category}: {qty}")

    print("\nReinstall Signal:")
    print(f"  Score: {assessment['score']}/100")
    print(f"  Likelihood: {assessment['likelihood']}")
    print(f"  Recommendation: {assessment['recommendation']}")
    print(f"  Local Signature Hits: {assessment['local_signature_count']}")
    print(f"  Network Signature Hits: {assessment['network_signature_count']}")

    print("\nError Timeline:")
    if error_events:
        for item in error_events[: max(args.limit, 0)]:
            print(
                "  "
                f"[{item.index}] time={item.time} "
                f"type={item.event_type} phase={item.phase} "
                f"source={item.source_type} host={item.host} error={item.error_name}"
            )
    else:
        print("  None")

    if args.json_output:
        output_payload = {
            "file": str(netlog_path),
            "browser_hint": browser_hint,
            "total_events": len(events),
            "event_categories": dict(event_category_counts),
            "error_counts": dict(error_name_counts),
            "error_event_type_counts": dict(error_type_counts),
            "affected_hosts": sorted(affected_hosts),
            "assessment": assessment,
            "timeline": [
                {
                    "index": e.index,
                    "time": e.time,
                    "event_type": e.event_type,
                    "phase": e.phase,
                    "source_type": e.source_type,
                    "host": e.host,
                    "error_name": e.error_name,
                }
                for e in error_events[: max(args.limit, 0)]
            ],
        }
        output_path = Path(args.json_output)
        output_path.write_text(json.dumps(output_payload, indent=2), encoding="utf-8")
        print(f"\nJSON summary written to: {output_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
