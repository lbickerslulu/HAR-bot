import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


# Weighted severity model for hardware-related Event IDs.
WEIGHTS: dict[int, int] = {
    41: 10,
    6008: 9,
    1001: 9,
    18: 10,
    17: 7,
    129: 8,
    153: 6,
    7: 10,
    219: 4,
    225: 5,
    37: 5,
    55: 5,
}



def load_events(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as f:
        payload = json.load(f)

    if isinstance(payload, list):
        return [e for e in payload if isinstance(e, dict)]

    if isinstance(payload, dict):
        # Some exporters may wrap records in a top-level property.
        for key in ("events", "data", "records"):
            value = payload.get(key)
            if isinstance(value, list):
                return [e for e in value if isinstance(e, dict)]

    raise ValueError("Unsupported JSON format. Expected a list of event objects.")



def safe_event_id(event: dict[str, Any]) -> int | None:
    value = event.get("EventID")
    if value is None:
        return None

    try:
        return int(value)
    except (TypeError, ValueError):
        return None



def count_groups(counts: Counter[int]) -> dict[str, int]:
    def count(*ids: int) -> int:
        return sum(counts.get(i, 0) for i in ids)

    return {
        "crashes": count(41, 6008, 1001),
        "whea": count(17, 18, 19),
        "disk": count(7, 51, 129, 153),
        "pnp": count(219, 225, 410, 411),
    }



def verdict_from_score(score: int, groups: dict[str, int]) -> tuple[str, str]:
    if score > 80 or groups["crashes"] >= 3 or groups["whea"] >= 2:
        return (
            "HIGH likelihood of hardware instability",
            "Recommend device swap",
        )

    if score > 40:
        return (
            "Moderate instability detected",
            "Investigate specific component (disk / drivers)",
        )

    return (
        "Low hardware risk",
        "Monitor / software remediation",
    )



def build_summary(
    total_events: int,
    score: int,
    counts: Counter[int],
    groups: dict[str, int],
    verdict: str,
    recommendation: str,
) -> str:
    lines = [
        "===== Hardware Analysis Summary =====",
        "",
        f"Total Events: {total_events}",
        f"Score: {score}",
        "",
        "--- Breakdown ---",
        f"Crashes (41/6008/1001): {groups['crashes']}",
        f"WHEA Errors: {groups['whea']}",
        f"Disk Issues: {groups['disk']}",
        f"PnP Issues: {groups['pnp']}",
        "",
        "--- Top Events ---",
    ]

    top_events = counts.most_common(5)
    if top_events:
        for event_id, qty in top_events:
            lines.append(f"Event {event_id}: {qty}")
    else:
        lines.append("No events found")

    lines.extend(
        [
            "",
            "--- Assessment ---",
            verdict,
            "",
            "--- Recommendation ---",
            recommendation,
            "",
        ]
    )

    return "\n".join(lines)



def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Score Windows hardware events and generate a summary."
    )
    parser.add_argument(
        "--input",
        default="hardware_events.json",
        help="Path to collector JSON output (default: hardware_events.json)",
    )
    parser.add_argument(
        "--summary",
        default="summary.txt",
        help="Path to summary output file (default: summary.txt)",
    )
    parser.add_argument(
        "--result",
        default="result.json",
        help="Path to structured JSON result (default: result.json)",
    )
    return parser.parse_args()



def main() -> int:
    args = parse_args()

    input_path = Path(args.input)
    summary_path = Path(args.summary)
    result_path = Path(args.result)

    try:
        events = load_events(input_path)
    except FileNotFoundError:
        print(f"Input file not found: {input_path}")
        return 1
    except json.JSONDecodeError as exc:
        print(f"Invalid JSON in {input_path}: {exc}")
        return 1
    except ValueError as exc:
        print(str(exc))
        return 1

    event_ids = [event_id for event in (safe_event_id(e) for e in events) if event_id is not None]
    counts = Counter(event_ids)

    score = sum(WEIGHTS.get(event_id, 1) for event_id in event_ids)
    groups = count_groups(counts)
    verdict, recommendation = verdict_from_score(score, groups)

    summary = build_summary(
        total_events=len(events),
        score=score,
        counts=counts,
        groups=groups,
        verdict=verdict,
        recommendation=recommendation,
    )

    print(summary)
    summary_path.write_text(summary, encoding="utf-8")

    result_payload = {
        "total_events": len(events),
        "score": score,
        "verdict": verdict,
        "recommendation": recommendation,
        "counts": dict(counts),
        "groups": groups,
    }
    result_path.write_text(json.dumps(result_payload, indent=2), encoding="utf-8")

    print(f"Saved summary to: {summary_path}")
    print(f"Saved structured result to: {result_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
