"""Extract the parent interface names from the local Chromium V8 snapshot.

This is deliberately conservative: generated Blink files are the source of
truth, while the runtime registry in ``pyv8c.interfaces`` is hand-reviewed.
"""
import argparse
import json
import re
from pathlib import Path


PARENT = re.compile(r"parent_class->GetV8ClassTemplate|wrapper_type_info_\{", re.M)
NAME = re.compile(r"V8([A-Za-z0-9_]+)::wrapper_type_info_")
PARENT_NAME = re.compile(r"V8([A-Za-z0-9_]+)::GetWrapperTypeInfo\(\)")


def extract(root: Path):
    result = {}
    for path in sorted(root.rglob("v8_*.cc")):
        text = path.read_text(encoding="utf-8", errors="ignore")
        name = NAME.search(text)
        if not name:
            continue
        match = re.search(r"wrapper_type_info_\{(?P<body>.*?)};", text, re.S)
        parent = None
        if match:
            parents = PARENT_NAME.findall(match.group("body"))
            if parents:
                parent = parents[-1]
        interface_name = name.group(1)
        result[interface_name] = {
            "parent": parent,
            "source": str(path),
            "snapshot": "local-renderer",
            "constructible": None,
            "constructor": {"exposed": None, "allow_new": None, "allow_call": None, "length": None},
            "tag": interface_name,
            "members": {"instance": [], "prototype": [], "interface": []},
            "mixins": [],
        }
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("renderer", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.write_text(json.dumps(extract(args.renderer), indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
