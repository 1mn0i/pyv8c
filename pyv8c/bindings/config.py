"""Pure helpers for translating baseline template metadata to native flags."""

from __future__ import annotations


TEMPLATE_FLAGS = (
    ("immutable_proto", "__v8_immutable__"),
    ("undetectable", "__v8_undetectable__"),
    ("global_this", "__v8_global_this__"),
    ("as_object", "__v8_as_obj__"),
    ("unconstructable", "__v8_unconstructable__"),
)


def apply_template_flags(config, template):
    for source, target in TEMPLATE_FLAGS:
        if template.get(source):
            config[target] = 1
    return config

