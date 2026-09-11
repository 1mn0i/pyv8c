"""Offline inventory of actual registered callbacks; never part of API dispatch."""

import inspect
import json
import re
from pathlib import Path


def main():
    from pyv8c import Browser

    root = Path(__file__).resolve().parents[1]
    records = []
    browser = Browser(stub_logging="off")
    try:
        universe = browser.interface_universe
        for name in sorted(universe.registry.specs):
            cls = universe.class_for(name)
            config = universe.class_config_for(name)
            members = [("method", member.name, member.callback) for member in config.methods]
            members.extend(("setter", member.name, member.setter) for member in config.attributes if member.setter)
            if config.has_constructor and config.constructor_behavior:
                members.append(("constructor", "constructor", cls.__init__))
            if config.call:
                members.append(("callable", "call", config.call))
            for kind, member, callback in members:
                if not callable(callback) or getattr(callback, "__v8_idl_stub__", False):
                    continue
                try:
                    source = inspect.getsourcefile(callback)
                    signature = re.sub(r"<object object at 0x[0-9A-Fa-f]+>", "MISSING", str(inspect.signature(callback)))
                except (TypeError, ValueError):
                    continue
                if not source:
                    continue
                source = Path(source).resolve()
                if not source.is_relative_to(root / "pyv8c") or source.name == "stubs.py":
                    continue
                records.append({"interface": name, "member": member, "kind": kind,
                                "callback": callback.__qualname__, "signature": signature,
                                "source": source.relative_to(root).as_posix()})
        print(json.dumps({"registered_members": records}, indent=2))
    finally:
        browser.close()


if __name__ == "__main__":
    main()
