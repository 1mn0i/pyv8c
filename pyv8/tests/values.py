"""Explicit data conversion for tests asserting Python snapshots."""
from pyv8c import pyv8


def snapshot(value):
    return value.to_python() if isinstance(value, pyv8.JSObject) else value
