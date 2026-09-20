"""What the package promises has to be what the extension publishes.

The stubs, the package's ``__all__`` and the module itself are three descriptions
of one surface, kept in three places.  These tests are what says whether they
agree: a stub that declares a method the type does not have is worse than no stub,
because a type checker believes it -- ``Context.__getitem__`` was exactly that
until it was removed.
"""

import ast
import pathlib

import pyv8
import pyv8._pyv8 as native
import pyv8.flag

PACKAGE = pathlib.Path(pyv8.__file__).parent
NATIVE_STUB = PACKAGE / "_pyv8.pyi"
PACKAGE_STUB = PACKAGE / "__init__.pyi"

# init_v8 is deliberately private: this package calls it at import time, and hands
# out nothing that would have a caller point it at a different ICU data file.
INTERNAL_NATIVE = {"init_v8"}

# These two are how a Python class on this type hooks attribute access; the type
# implements the slots (__getattribute__ and __setattr__) rather than members with
# those names, so the stub is naming behaviour, not a member to find.
BEHAVIOUR_ONLY = {"__getattr__", "__setattr__"}


def members_of(node):
    """The member names a class body declares."""
    for member in node.body:
        if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield member.name
        elif isinstance(member, ast.AnnAssign) and isinstance(member.target, ast.Name):
            yield member.target.id


def test_every_native_name_is_exported_or_deliberately_private():
    published = {name for name in dir(native) if not name.startswith("_")}
    assert published - INTERNAL_NATIVE <= set(pyv8.__all__)


def test_every_name_the_package_promises_exists():
    for name in pyv8.__all__:
        assert hasattr(pyv8, name), name


def test_the_flag_names_the_package_promises_are_the_ones_flag_py_declares():
    assert set(pyv8.flag.__all__) <= set(pyv8.__all__)


def test_every_stub_declaration_exists_in_the_extension():
    tree = ast.parse(NATIVE_STUB.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef)):
            assert hasattr(native, node.name), node.name


def test_every_native_stub_member_exists_on_its_type():
    tree = ast.parse(NATIVE_STUB.read_text(encoding="utf-8"))
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        declared = getattr(native, node.name)
        for name in members_of(node):
            if name in BEHAVIOUR_ONLY:
                continue
            assert hasattr(declared, name), "%s.%s" % (node.name, name)


def test_every_package_stub_member_exists_on_the_package():
    tree = ast.parse(PACKAGE_STUB.read_text(encoding="utf-8"))
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        declared = getattr(pyv8, node.name)
        for name in members_of(node):
            assert hasattr(declared, name), "%s.%s" % (node.name, name)
