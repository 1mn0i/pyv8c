import json
import sys
from pathlib import Path


TOOLS = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import extract_interfaces


def test_balanced_binding_tables_extract_all_locations_and_descriptors():
    text = r'''
const WrapperTypeInfo V8Fixture::wrapper_type_info_{
  gin::kEmbedderBlink, V8Fixture::InstallInterfaceTemplate, nullptr,
  "Fixture", V8EventTarget::GetWrapperTypeInfo(), 1, 2,
};
void GetterCallback(const v8::FunctionCallbackInfo<v8::Value>& info) {}
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
    assert members["interface"][0]["name"] == "run"
    assert members["interface"][0]["signature"]["required"] == 1
    assert members["interface"][0]["signature"]["parameters"][0]["idl_type"] == "IDLString"
    assert members["interface"][1]["kind"] == "constant"


def test_real_renderer_snapshot_has_members_for_core_interfaces():
    root = Path(__file__).resolve().parents[2] / "_pyv8env" / "renderer"
    snapshot = extract_interfaces.extract(root)
    for name in ("Event", "EventTarget", "Node", "Document", "Element", "Window", "Headers", "Blob", "Request", "Response", "URL"):
        entry = snapshot[name]
        assert any(entry["members"].values()), name
    assert len(snapshot["URL"]["sources"]) >= 2
    assert snapshot["_report"]["interfaces"] > 1000
    assert snapshot["_report"]["manual_review_required"] > 0


def test_generated_baseline_keeps_full_snapshot_and_report():
    baseline = json.loads((Path(__file__).resolve().parents[1] / "interface-baseline.json").read_text(encoding="utf-8"))
    assert baseline["_webidl"]["schema"] == 2
    assert baseline["_webidl"]["report"]["interfaces"] > 1000
    assert len(baseline["Event"]["members"]["prototype"]) > 5
    assert len(baseline["Node"]["members"]["prototype"]) > 10
    assert len(baseline["URL"]["members"]["interface"]) >= 2
