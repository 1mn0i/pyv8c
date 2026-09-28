"""Pure helpers for translating baseline template metadata to native flags."""

from __future__ import annotations

TEMPLATE_FLAGS = (
    ("immutable_proto", "immutable"),
    ("undetectable", "undetectable"),
    ("global_this", "global_this"),
    ("as_object", "as_object"),
    ("unconstructable", "unconstructable"),
)


def apply_template_flags(config, template):
    for source, target in TEMPLATE_FLAGS:
        if template.get(source):
            config[target] = 1 if target == "immutable" else True
    # ``array`` marks a Web IDL indexed collection; the bridge shares FlagArrayProto's bit layout.
    if template.get("array") and not config.get("array"):
        config["array"] = 1
    return config
