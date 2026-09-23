import json
import zipfile
from pathlib import Path

import pytest

from pyv8c.tools import extract_interfaces
from pyv8c.tools.extract_interfaces import (
    _constant_definitions,
    _idl_handler_findings,
    _idl_property_families,
    extract_constants,
    extract_template,
    write_baseline,
)


def _bundled_baseline_document():
    path = Path(__file__).resolve().parents[2] / "pyv8c" / "baselines" / "interface-baseline.zip"
    with zipfile.ZipFile(path) as archive:
        return json.loads(archive.read("interface-baseline.json"))


def test_balanced_binding_tables_extract_all_locations_and_descriptors():
    text = r'''
const WrapperTypeInfo V8Fixture::wrapper_type_info_{
  gin::kEmbedderBlink, V8Fixture::InstallInterfaceTemplate, nullptr,
  "Fixture", V8EventTarget::GetWrapperTypeInfo(), 1, 2,
};
void GetterCallback(const v8::FunctionCallbackInfo<v8::Value>& info) {}
void SetterCallback(const v8::FunctionCallbackInfo<v8::Value>& info) {
  auto&& value = NativeValueTraits<IDLBoolean>::ArgumentValue(isolate, 0, info[0], exception_state);
}
void RunOperationCallback(const v8::FunctionCallbackInfo<v8::Value>& info) {
  if (UNLIKELY(info.Length() < 1)) { exception_state.ThrowTypeError(ExceptionMessages::NotEnoughArguments(1, info.Length())); }
  auto&& value = NativeValueTraits<IDLString>::ArgumentValue(isolate, 0, info[0], exception_state);
  if (foo({1, 2})) { bar(); }
}
static const IDLMemberInstaller::AttributeConfig kAttributeTable[] = {
  {"readonly", GetterCallback, nullptr, unsigned(v8::None), unsigned(IDLMemberInstaller::FlagLocation::kPrototype), unsigned(IDLMemberInstaller::FlagWorld::kAllWorlds), unsigned(IDLMemberInstaller::FlagReceiverCheck::kCheck), unsigned(IDLMemberInstaller::FlagCrossOriginCheck::kCheck), unsigned(v8::SideEffectType::kHasNoSideEffect), unsigned(V8PrivateProperty::CachedAccessor::kNone)},
  {"value", GetterCallback, SetterCallback, unsigned(v8::None), unsigned(IDLMemberInstaller::FlagLocation::kInstance), unsigned(IDLMemberInstaller::FlagWorld::kAllWorlds), unsigned(IDLMemberInstaller::FlagReceiverCheck::kCheck), unsigned(IDLMemberInstaller::FlagCrossOriginCheck::kDoNotCheck), unsigned(v8::SideEffectType::kHasSideEffect), unsigned(V8PrivateProperty::CachedAccessor::kNone)},
};
static const IDLMemberInstaller::OperationConfig kOperationTable[] = {
  {"run", RunOperationCallback, 1, unsigned(v8::None), unsigned(IDLMemberInstaller::FlagLocation::kInterface), unsigned(IDLMemberInstaller::FlagWorld::kAllWorlds), unsigned(IDLMemberInstaller::FlagReceiverCheck::kDoNotCheck), unsigned(IDLMemberInstaller::FlagCrossOriginCheck::kDoNotCheck), unsigned(v8::SideEffectType::kHasSideEffect)},
};
static const IDLMemberInstaller::ConstantValueConfig kConstantValueTable[] = {
  {"ANSWER", Fixture::Constant::kAnswer},
};
'''
    members = extract_interfaces.extract_members(text)
    assert [item["name"] for item in members["prototype"]] == ["readonly"]
    assert members["instance"][0]["setter"] == "SetterCallback"
    assert members["instance"][0]["setter_signature"]["parameters"][0]["idl_type"] == "IDLBoolean"
    assert members["interface"][0]["name"] == "run"
    assert members["interface"][0]["signature"]["required"] == 1
    assert members["interface"][0]["signature"]["parameters"][0]["idl_type"] == "IDLString"
    assert members["interface"][1]["kind"] == "constant"


def test_generated_constant_expression_resolves_from_header(tmp_path):
    header = tmp_path / "v8_gpu_buffer_usage.h"
    header.write_text(
        "class V8GPUBufferUsage { public: class Constant { public: "
        "static constexpr uint32_t kMapRead = 1; "
        "static constexpr uint32_t kCopySrc = 1 << 2; }; };",
        encoding="utf-8",
    )
    members = {"instance": [], "prototype": [], "interface": [{
        "name": "COPY_SRC", "kind": "constant",
        "value": "V8GPUBufferUsage::Constant::kCopySrc",
    }]}
    definitions = _constant_definitions(tmp_path)
    constants = extract_constants(members, definitions)
    assert constants[0]["resolved"] is True
    assert constants[0]["value"] == 4


def test_constant_expression_is_audit_only():
    extracted = {
        "Fixture": {
            "sources": [], "constructor": {}, "template": {},
            "members": {"instance": [], "prototype": [], "interface": [{
                "name": "COPY_SRC", "kind": "constant", "value": "1 << 2",
            }]},
            "constants": [{"name": "COPY_SRC", "expression": "1 << 2", "resolved": True, "value": 4}],
        },
    }
    compact = extract_interfaces.schema5_baseline(extracted)["Fixture"]
    assert compact["constants"] == [{"name": "COPY_SRC", "resolved": True, "value": 4, "location": "interface"}]
    audit = extract_interfaces.schema5_baseline(extracted, include_audit=True)["Fixture"]
    assert audit["constants"][0]["expression"] == "1 << 2"


def test_template_handler_comments_are_not_callback_names():
    text = r'''
void NamedGetterCallback() {}
void NamedSetterCallback() {}
void NamedQueryCallback() {}
void NamedDeleterCallback() {}
void NamedEnumeratorCallback() {}
void NamedDefinerCallback() {}
void IndexedGetterCallback() {}
void IndexedSetterCallback() {}
void IndexedQueryCallback() {}
void IndexedDeleterCallback() {}
void IndexedEnumeratorCallback() {}
void IndexedDefinerCallback() {}
void Install() {
  NamedPropertyHandlerConfiguration(
      NamedGetterCallback,
      NamedSetterCallback,
      // query
      NamedQueryCallback,
      // deleter
      NamedDeleterCallback,
      // enumerator
      NamedEnumeratorCallback,
      // definer
      NamedDefinerCallback);
  IndexedPropertyHandlerConfiguration(
      IndexedGetterCallback, IndexedSetterCallback, /* query */ IndexedQueryCallback,
      IndexedDeleterCallback, IndexedEnumeratorCallback, IndexedDefinerCallback);
}
'''
    handlers = extract_template(text)["handlers"]
    assert handlers["named"] == {
        "get": "NamedGetterCallback", "set": "NamedSetterCallback",
        "query": "NamedQueryCallback", "del": "NamedDeleterCallback",
        "enum": "NamedEnumeratorCallback", "definer": "NamedDefinerCallback",
    }
    assert handlers["indexed"] == {
        "get": "IndexedGetterCallback", "set": "IndexedSetterCallback",
        "query": "IndexedQueryCallback", "del": "IndexedDeleterCallback",
        "enum": "IndexedEnumeratorCallback", "definer": "IndexedDefinerCallback",
    }


def test_cross_origin_configurations_are_not_the_interfaces_own_handlers():
    """Cross-origin setups build their own configurations, inside the access check.

    Reading those as the interface's own handlers is what gave a global named
    handlers its IDL never declares.
    """
    text = r'''
void Install(InstanceTemplate* instance_template) {
  v8::NamedPropertyHandlerConfiguration(
      V8Widget::NamedPropertyGetterCallback,
      V8Widget::NamedPropertySetterCallback,
      nullptr,
      nullptr,
      nullptr,
      nullptr,
      v8::Local<v8::Value>(),
      v8::PropertyHandlerFlags::kNone);
  instance_template->SetAccessCheckCallbackAndHandler(
      CrossOriginAccessCheckCallback,
      v8::NamedPropertyHandlerConfiguration(
          CrossOriginNamedGetterCallback,
          CrossOriginNamedSetterCallback,
          CrossOriginNamedQueryCallback,
          CrossOriginNamedDeleterCallback,
          CrossOriginNamedEnumeratorCallback,
          CrossOriginNamedDefinerCallback,
          v8::Local<v8::Value>(),
          v8::PropertyHandlerFlags::kNone),
      v8::IndexedPropertyHandlerConfiguration(
          CrossOriginIndexedGetterCallback,
          nullptr,
          nullptr,
          nullptr,
          nullptr,
          nullptr,
          v8::Local<v8::Value>(),
          v8::PropertyHandlerFlags::kNone));
}
'''
    handlers = extract_template(text)["handlers"]
    assert handlers["named"] == {
        "get": "V8Widget::NamedPropertyGetterCallback",
        "set": "V8Widget::NamedPropertySetterCallback",
    }
    assert handlers["indexed"] == {}
    assert handlers["cross_origin"]["named"] == {
        "get": "CrossOriginNamedGetterCallback", "set": "CrossOriginNamedSetterCallback",
        "query": "CrossOriginNamedQueryCallback", "del": "CrossOriginNamedDeleterCallback",
        "enum": "CrossOriginNamedEnumeratorCallback", "definer": "CrossOriginNamedDefinerCallback",
    }
    assert handlers["cross_origin"]["indexed"] == {"get": "CrossOriginIndexedGetterCallback"}


def test_a_named_handler_needs_a_named_getter_in_the_idl():
    """The IDL decides which families a set of generated handlers can belong to."""
    named = _idl_property_families("  [NotEnumerable, CrossOrigin] getter object (DOMString name);\n")
    indexed = _idl_property_families("  getter Widget (unsigned long index);\n")
    assert named == {"named": {"get": True}, "indexed": {}}
    assert indexed == {"named": {}, "indexed": {"get": True}}

    metadata = {"Window": {"properties": indexed}, "Storage": {"properties": named}}
    extracted = {
        "Window": {"template": {"handlers": {"named": {"get": "callback"}}}},
        "Storage": {"template": {"handlers": {"named": {"get": "callback"}}}},
    }
    assert _idl_handler_findings(extracted, metadata) == [{
        "interface": "Window",
        "family": "named",
        "handlers": ["get"],
        "reason": "no named getter or setter in the interface's IDL",
    }]


def test_real_renderer_snapshot_has_members_for_core_interfaces():
    root = Path(__file__).resolve().parents[3] / "_pyv8env" / "renderer"
    if not root.is_dir():
        pytest.skip(f"no generated Chromium sources at {root}")
    snapshot = extract_interfaces.extract(root)
    for name in ("Event", "EventTarget", "Node", "Document", "Element", "Window", "Headers", "Blob", "Request", "Response", "URL"):
        entry = snapshot[name]
        assert any(entry["members"].values()), name
    assert len(snapshot["URL"]["sources"]) >= 2
    assert snapshot["_report"]["interfaces"] > 1000
    assert snapshot["_report"]["unresolved_signatures"] > 0


def test_generated_baseline_keeps_full_runtime_snapshot_and_report():
    baseline = _bundled_baseline_document()
    assert baseline["_webidl"]["schema"] == 5
    assert baseline["_webidl"]["compact"] is True
    assert baseline["_webidl"]["report"]["interfaces"] > 1000
    assert baseline["_webidl"]["report"]["unresolved_signatures"] > 0
    assert all("callbacks" not in value for key, value in baseline.items() if not key.startswith("_"))
    assert len(baseline["Event"]["members"]["prototype"]) >= 5
    assert len(baseline["Node"]["members"]["prototype"]) > 10
    assert len(baseline["URL"]["members"]["interface"]) >= 2
    assert baseline["HTMLAllCollection"]["template"]["undetectable"] is True
    handlers = baseline["WindowProperties"]["template"]["handlers"]
    assert handlers["named"]["del"] == 1
    assert handlers["named"]["definer"] == 1
    assert handlers["indexed"]["del"] == 1
    assert handlers["indexed"]["definer"] == 1
    entry = baseline["URL"]
    assert set(entry) == {"constants", "constructor", "exposed_in", "members", "parent", "tag", "template"}
    assert "provenance" not in entry
    assert "handlers" in entry["template"]
    assert all(member["kind"] != "constant" for values in entry["members"].values() for member in values)


def test_baseline_zip_is_optional_and_has_a_stable_json_member(tmp_path):
    document = {"_webidl": {"schema": 5, "compact": True}, "Fixture": {"parent": None}}
    json_path = write_baseline(tmp_path / "baseline.json", document)
    assert json_path.name == "baseline.json"
    assert json.loads(json_path.read_text(encoding="utf-8")) == document

    zip_path = write_baseline(tmp_path / "baseline.json", document, compress=True)
    assert zip_path.name == "baseline.zip"
    with zipfile.ZipFile(zip_path) as archive:
        assert archive.namelist() == ["baseline.json"]
        assert json.loads(archive.read("baseline.json")) == document


def test_schema5_audit_metadata_is_opt_in():
    extracted = {
        "Fixture": {
            "parent": None,
            "tag": "Fixture",
            "sources": ["renderer/core/v8_fixture.cc"],
            "constructor": {},
            "template": {},
            "members": {"instance": [], "prototype": [], "interface": []},
            "constants": [],
        },
    }
    assert "provenance" not in extract_interfaces.schema5_baseline(extracted)["Fixture"]
    assert extract_interfaces.schema5_baseline(extracted, include_audit=True)["Fixture"]["provenance"] == {
        "snapshot": "chromium-generated-bindings",
        "sources": ["renderer/core/v8_fixture.cc"],
    }


def test_schema5_audit_switch_controls_callback_and_world_fields():
    extracted = {
        "Fixture": {
            "parent": None,
            "sources": [],
            "constructor": {"callback": "ConstructorCallback"},
            "template": {
                "call_handler": "CallHandler",
                "access_check": {"callback": "AccessCheck", "with_handler": True},
                "handlers": {"named": {"get": "NamedGetterCallback"}, "indexed": {},
                             "cross_origin": {"named": {}, "indexed": {}}},
            },
            "members": {"instance": [], "prototype": [{
                "name": "run", "kind": "operation", "callback": "RunCallback",
                "world": "AllWorlds", "length": 0, "signature": {"parameters": []},
            }, {
                "name": "value", "kind": "attribute", "getter": "GetCallback",
                "setter": "SetCallback", "world": "AllWorlds",
            }], "interface": []},
            "constants": [],
        },
    }
    compact = extract_interfaces.schema5_baseline(extracted)["Fixture"]
    assert "callback" not in compact["constructor"]
    assert compact["template"]["call_handler"] == 1
    assert compact["template"]["access_check"] == {"with_handler": True, "enabled": 1}
    assert compact["template"]["handlers"]["named"]["get"] == 1
    operation, attribute = compact["members"]["prototype"]
    assert "callback" not in operation and "world" not in operation
    assert "getter" not in attribute and "readonly" not in attribute and attribute["setter"] == 1

    audit = extract_interfaces.schema5_baseline(extracted, include_audit=True)["Fixture"]
    assert audit["constructor"]["callback"] == "ConstructorCallback"
    assert audit["template"]["handlers"]["named"]["get"] == "NamedGetterCallback"
    assert audit["members"]["prototype"][0]["callback"] == "RunCallback"
    assert audit["members"]["prototype"][0]["world"] == "AllWorlds"
