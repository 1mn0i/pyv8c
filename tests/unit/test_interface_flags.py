"""Member property flags arrive as the bitmask the binding uses, not only as text."""
import pytest

pytest.importorskip("pyv8", reason="native pyv8 extension is not importable")

from pyv8.flag import V8Attribute  # noqa: E402
from pyv8c.engine.assembly import property_flags  # noqa: E402
from pyv8c.engine.baseline import BaselineSnapshot, InterfaceTable  # noqa: E402
from pyv8c.tools.extract_interfaces import _property_mask  # noqa: E402


def test_the_extractor_reads_a_table_flags_field_as_a_mask():
    assert _property_mask("unsigned(v8::None)") == V8Attribute.kNone
    assert _property_mask("unsigned(v8::ReadOnly) | unsigned(v8::DontEnum)") == (
        V8Attribute.ReadOnly | V8Attribute.DontEnum
    )
    assert _property_mask("somethingUnreadable") is None


def _member_entry(**fields):
    """A one-member baseline the table decodes, so the flags come from a real Member."""
    member = {"name": "value", "kind": "attribute", **fields}
    entry = {"members": {"instance": [member]}}
    baseline = BaselineSnapshot(path="test", digest="", metadata={}, interfaces={"Widget": entry})
    return InterfaceTable(baseline).member("Widget", "value", "attribute")


def test_the_table_prefers_a_stated_mask_and_still_reads_the_older_text():
    assert property_flags(_member_entry(property_flags=6)) == 6
    assert property_flags(_member_entry(flags="v8::DontEnum | v8::DontDelete")) == (
        V8Attribute.DontEnum | V8Attribute.DontDelete
    )


def test_the_compact_snapshot_drops_the_flag_text_only_when_the_mask_is_there():
    from pyv8c.tools.extract_interfaces import _compact_member_callback_metadata

    read = {"name": "value", "property_flags": 2, "flags": "v8::DontEnum"}
    unread = {"name": "value", "property_flags": None, "flags": "v8::DontEnum"}
    _compact_member_callback_metadata(read, include_audit=False)
    _compact_member_callback_metadata(unread, include_audit=False)
    assert read == {"name": "value", "property_flags": 2, "main_world": True}
    assert unread["flags"] == "v8::DontEnum"
    kept = {"name": "value", "property_flags": 2, "flags": "v8::DontEnum", "world": "IsolatedWorld"}
    _compact_member_callback_metadata(kept, include_audit=True)
    assert kept["flags"] == "v8::DontEnum"
    assert kept["world"] == "IsolatedWorld"
