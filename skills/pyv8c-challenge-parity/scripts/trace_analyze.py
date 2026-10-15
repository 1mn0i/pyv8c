"""Group trace candidates by Realm; no return value proves a defect on its own."""

import argparse
import collections
import json
from pathlib import Path
import re

LINE = re.compile(r"\[([^\]]+)\]\s+(getter|method|setter):\s+(.*)")


def analyze(path):
    groups = collections.defaultdict(lambda: {"count": 0, "samples": []})
    unmatched = 0
    for number, raw in enumerate(
        Path(path).read_text(encoding="utf-8", errors="replace").splitlines(), 1
    ):
        text = raw
        try:
            event = json.loads(raw)
        except ValueError:
            event = None
        if isinstance(event, dict):
            if event.get("kind") != "trace":
                continue
            if all(
                key in event for key in ("realm", "access", "member", "value", "error")
            ):
                realm, kind, name = event["realm"], event["access"], event["member"]
                value, error = event["value"], event["error"]
                signal = (
                    "exception"
                    if error != {"type": "undefined"}
                    else "undefined"
                    if value == {"type": "undefined"}
                    else "access"
                )
                item = groups[(realm, kind, name, signal)]
                item["count"] += 1
                if len(item["samples"]) < 3:
                    item["samples"].append({"line": number, "raw": raw})
                continue
            # Keep native structured payloads intact when format cannot be parsed.
            text = json.dumps(event, ensure_ascii=False)
        match = LINE.search(text)
        if match:
            realm, kind, rest = match.groups()
            name, arrow, value = rest.partition(" -> ")
            if kind == "method":
                name = name.split("(")[0].strip()
            signal = (
                "exception"
                if ", error:" in rest
                else (
                    "undefined" if arrow and value.startswith("undefined") else "access"
                )
            )
        elif isinstance(event, dict):
            realm, kind, name, signal = (
                "unparsed",
                "trace",
                "structured",
                "unclassified",
            )
            unmatched += 1
        elif any(token in text for token in ("Error", "Exception", "dispatch error")):
            realm, kind, name, signal = (
                "unknown",
                "log",
                "error-looking line",
                "unconfirmed_exception",
            )
        else:
            continue
        item = groups[(realm, kind, name, signal)]
        item["count"] += 1
        if len(item["samples"]) < 3:
            item["samples"].append({"line": number, "raw": raw})
    return {
        "source": str(Path(path).resolve()),
        "meaning": "candidates, not confirmed Chrome differences",
        "unparsed_trace_events": unmatched,
        "groups": [
            dict(realm=k[0], kind=k[1], name=k[2], signal=k[3], **v)
            for k, v in sorted(groups.items())
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path")
    parser.add_argument("--out")
    args = parser.parse_args()
    result = analyze(args.path)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    else:
        print(text)


if __name__ == "__main__":
    main()
