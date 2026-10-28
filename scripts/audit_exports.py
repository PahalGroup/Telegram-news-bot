"""Read-only Telegram export audit. No network, credentials or publication.
Usage: python scripts/audit_exports.py reference.json own.json --output audit.json
A text match is not proof of a shared source, missed coverage, or ingestion latency.
"""
import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

def plaintext(value):
    if isinstance(value, str):
        return value
    return "".join(v if isinstance(v, str) else v.get("text", "") for v in (value or []))

def normalized(text):
    return re.sub(r"\s+", " ", text.lower()).strip()

def inspect(data, start="2026-10-12", end="2026-10-26"):
    messages = [m for m in data["messages"] if m.get("type") == "message"
                and start <= m["date"] < end]
    groups = defaultdict(list)
    for m in messages:
        value = normalized(plaintext(m.get("text", "")))
        if value:
            groups[value].append(m)
    duplicates = sorted((g for g in groups.values() if len(g) > 1), key=len, reverse=True)
    return {
        "channel": data["name"],
        "export_records": len(data["messages"]),
        "export_first": data["messages"][0]["date"],
        "export_last": data["messages"][-1]["date"],
        "window_messages": len(messages),
        "daily": dict(sorted(Counter(m["date"][:10] for m in messages).items())),
        "photo_references": sum(bool(m.get("photo")) for m in messages),
        "file_references": sum(bool(m.get("file")) for m in messages),
        "empty_text": sum(not plaintext(m.get("text", "")) for m in messages),
        "repeated_text_excess": sum(len(g)-1 for g in duplicates),
        "placeholder_marker_posts": sum(bool(re.search(
            r"please (?:refer|find)|self explanatory|verified from official",
            plaintext(m.get("text", "")), re.I)) for m in messages),
        "single_line_posts": sum(len(re.split(r"\n+", plaintext(m.get("text", "")).strip())) == 1
                                 for m in messages),
        "duplicate_groups": [{
            "count": len(g), "ids": [m["id"] for m in g],
            "first": g[0]["date"], "last": g[-1]["date"],
            "text": plaintext(g[0].get("text", "")),
        } for g in duplicates],
    }

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reference")
    parser.add_argument("own")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    payload = {}
    for key in ("reference", "own"):
        payload[key] = inspect(json.loads(Path(getattr(args, key)).read_text(encoding="utf-8-sig")))
    payload["method"] = {
        "window": "[2026-10-12, 2026-10-26) using exported local dates",
        "duplicates": "Case and whitespace normalized complete text; not semantic event matching",
        "limitations": "No claims of total missed coverage, first upstream availability or source attribution.",
    }
    Path(args.output).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

if __name__ == "__main__":
    main()
