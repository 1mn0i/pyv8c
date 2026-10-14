"""Behavioral contracts and adversarial build/checker inputs, not source spelling."""
import ast
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest
import pyv8
from tests.test_architecture_contracts import ROOT, setup_configuration


def checker():
    spec = importlib.util.spec_from_file_location('maintenance_layout', ROOT / 'tools/check_layout.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('literal', ["['src', 'src/isolate']", '["src", "src/isolate"]', '("src", "src/isolate")'])
def test_build_roots_ignore_quote_and_sequence_style(tmp_path, monkeypatch, literal):
    (tmp_path / 'setup.py').write_text('Extension("fixture", include_dirs=' + literal + ')')
    module = checker()
    monkeypatch.setattr(module, 'PROJECT_ROOT', tmp_path)
    assert module.include_roots_the_build_declares() == ['src', 'src/isolate']


@pytest.mark.parametrize('include,expected', [
    ('directory_that_does_not_exist/v8_compat.h', 1),
    ('handles/jsobject.h', 0),
    ('handles/v8_compat.h', 1),
])
def test_include_checks_real_paths(tmp_path, monkeypatch, include, expected):
    module = checker()
    copied = tmp_path / 'src'
    shutil.copytree(ROOT / 'src', copied)
    source = copied / 'pyv8.cpp'
    source.write_bytes(source.read_bytes() + f'\n#include "{include}"\n'.encode())
    monkeypatch.setattr(module, 'SOURCE_ROOT', copied)
    monkeypatch.setattr(sys, 'argv', ['check_layout.py', '--enforce'])
    with contextlib.redirect_stdout(io.StringIO()):
        assert module.main() == expected


def test_selected_compiler_and_binary_changes_force_rebuild(tmp_path, monkeypatch):
    namespace, _ = setup_configuration()
    build = namespace['PyV8BuildExt']
    (tmp_path / 'build').mkdir()
    monkeypatch.setitem(build._require_rebuild_for_changed_v8.__globals__, 'PROJECT_ROOT', tmp_path)
    executable = tmp_path / 'compiler.exe'
    executable.write_bytes(b'version one')
    compiler = SimpleNamespace(compiler_type='fixture', cc=str(executable))
    owner = SimpleNamespace(compiler=compiler, force=False, _build_fingerprint=lambda config: {'fixture': 1})
    owner._toolchain_identity = lambda: build._toolchain_identity(owner)
    file, original = build._require_rebuild_for_changed_v8(owner, None)
    file.write_text(json.dumps({'inputs': original}))
    owner.force = False
    build._require_rebuild_for_changed_v8(owner, None)
    assert owner.force is False
    executable.write_bytes(b'version two')
    build._require_rebuild_for_changed_v8(owner, None)
    assert owner.force is True
    owner.force = False
    compiler.cc = 'another-selected-compiler'
    build._require_rebuild_for_changed_v8(owner, None)
    assert owner.force is True


HANDLERS = re.findall(r'^PYV8_HANDLER\([^,]+,[^,]+,\s*(\w+)\)',
                      (ROOT / 'src/interfaces/handler_table.def').read_text(), re.M)


@pytest.mark.parametrize('name', HANDLERS)
def test_handler_constructor_default_replace_and_immutability(name):
    spec = pyv8.IdlSpec()
    assert getattr(spec, name) is None
    callback = lambda *args: None
    changed = spec.replace(**{name: callback})
    assert getattr(changed, name) is callback
    assert getattr(pyv8.IdlSpec(**{name: callback}), name) is callback
    assert getattr(spec, name) is None
    with pytest.raises(AttributeError):
        setattr(changed, name, callback)
    with pytest.raises(TypeError):
        pyv8.IdlSpec(**{name: 123})


def test_handler_keywords_are_declared_in_stub():
    tree = ast.parse((ROOT / 'pyv8/_pyv8.pyi').read_text(encoding='utf-8'))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'IdlSpec')
    init = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == '__init__')
    keywords = {arg.arg for arg in [*init.args.args, *init.args.kwonlyargs]}
    assert set(HANDLERS) <= keywords


class Window:
    pass


def child(program):
    result = subprocess.run([sys.executable, '-c', 'import pyv8,time\nclass Window: pass\n' + program],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize('setup,callback', [
    ('', 'c.clear_deadline'),
    ('c.set_deadline(.3)', 'c.clear_deadline'),
    ('c.set_deadline(.05)', 'other.clear_deadline'),
    ('other.set_deadline(.05);c.set_deadline(.3)', 'c.clear_deadline'),
])
def test_manual_deadlines_cannot_pop_other_owners_or_crossings(setup, callback):
    child(f'''
isolate=pyv8.Isolate();c=isolate.context(Window);other=isolate.context(Window)
{setup}
c.expose(clear={callback})
try:
    try: c.exec_js('clear();var t=Date.now();while(Date.now()-t<200){{}}',timeout=.05)
    except pyv8.JavaScriptTerminated: pass
    else: raise AssertionError('deadline ownership lost')
    c.clear_deadline();other.clear_deadline()
    assert c.exec_js('42')==42
finally: c.close();other.close();isolate.close()
''')


def test_closed_context_releases_its_manual_deadline():
    child('''
isolate=pyv8.Isolate();c=isolate.context(Window);other=isolate.context(Window)
c.set_deadline(.05);c.close()
try: assert other.exec_js('var t=Date.now();while(Date.now()-t<150){};42')==42
finally: other.close();isolate.close()
''')


@pytest.mark.parametrize('out_of_order', [False, True])
def test_context_clear_only_removes_its_own_manual_deadline(out_of_order):
    child(f'''
isolate=pyv8.Isolate();a=isolate.context(Window);b=isolate.context(Window)
if {out_of_order!r}:
    a.set_deadline(.3);b.set_deadline(.04);a.clear_deadline()
else:
    b.set_deadline(.04);a.clear_deadline()
try:
    try:b.exec_js('var t=Date.now();while(Date.now()-t<150){{}}',timeout=0)
    except pyv8.JavaScriptTerminated:pass
    else:raise AssertionError('another context cleared the deadline')
    b.clear_deadline()
    assert b.exec_js('42')==42
finally:a.close();b.close();isolate.close()
''')


def test_idle_expired_deadline_is_delivered_once():
    child('''
c=pyv8.Context(Window)
try:
    c.set_deadline(.03);time.sleep(.10)
    try:c.exec_js('42')
    except pyv8.JavaScriptTerminated:pass
    else:raise AssertionError('idle abort discarded')
    c.clear_deadline()
    assert c.exec_js('42')==42
finally:c.close()
''')


@pytest.mark.parametrize('cleanup', ['a.clear_deadline()', 'a.close()'])
def test_cleanup_releases_undelivered_abort_of_that_deadline(cleanup):
    child(f'''
isolate=pyv8.Isolate();a=isolate.context(Window);b=isolate.context(Window)
a.set_deadline(.02);time.sleep(.08)
{cleanup}
try:assert b.exec_js('42')==42
finally:a.close();b.close();isolate.close()
''')


def test_deadline_cleanup_does_not_cancel_explicit_termination():
    child('''
c=pyv8.Context(Window)
try:
    c.set_deadline(1);c.terminate();c.clear_deadline()
    try:c.exec_js('42')
    except pyv8.JavaScriptTerminated:pass
    else:raise AssertionError('explicit abort cancelled by deadline cleanup')
    assert c.exec_js('42')==42
finally:c.close()
''')


def test_deferred_context_close_cancels_deadline_at_logical_close():
    child('''
isolate=pyv8.Isolate();a=isolate.context(Window);b=isolate.context(Window)
a.set_deadline(.04)
b.expose(close=a.close)
try:
    assert b.exec_js('close();var t=Date.now();while(Date.now()-t<150){};42')==42
finally:a.close();b.close();isolate.close()
''')


def test_context_and_execution_keyword_contracts_and_invalid_calls():
    # Invalid parsing used to proceed with an uninitialized program pointer.
    child('''
c=pyv8.Context(global_this=Window,timeout=None)
try:
    assert c.timeout==0
    assert c.exec_js(program='6*7',filename=None,timeout=None)==42
    for kwargs in ({},{'program':'42','unknown':1},{'program':object()},{'program':'42','timeout':'bad'}):
        try:c.exec_js(**kwargs)
        except TypeError:pass
        else:raise AssertionError(kwargs)
    assert c.exec_js('42')==42
finally:c.close()
''')


@pytest.mark.parametrize('value', [float('nan'), float('inf'), 1e100])
def test_timeout_validation_rejects_unrepresentable_deadlines(value):
    with pytest.raises(ValueError):
        pyv8.Context(Window, timeout=value)
    c = pyv8.Context(Window)
    try:
        for call in (lambda: c.exec_js('42', timeout=value), lambda: c.set_deadline(value),
                     lambda: setattr(c, 'timeout', value)):
            with pytest.raises(ValueError): call()
        assert c.exec_js('42') == 42
    finally: c.close()


@pytest.mark.parametrize('kind', ['ArrayBuffer', 'SharedArrayBuffer'])
@pytest.mark.parametrize('view', ['b', 'new Uint8Array(b,2,3)', 'new DataView(b,2,3)'])
def test_copy_and_fixed_export_share_exact_byte_ranges(kind, view):
    c = pyv8.Context(Window)
    try:
        value = c.exec_js(f'var b=new {kind}(8);new Uint8Array(b).set([0,1,2,3,4,5,6,7]);{view}')
        expected = bytes(range(8)) if view == 'b' else b'\x02\x03\x04'
        assert bytes(value) == expected
        exported = value._buffer_view()
        assert exported.tobytes() == expected
        assert exported.readonly
        c.close()
        assert exported.tobytes() == expected
    finally: c.close()


def test_serializer_keyword_roundtrip_and_closed_owner():
    c = pyv8.Context(Window)
    serializer = pyv8.Serializer(context=c)
    data = serializer.serialize(value={'answer': 42}, transfer=(), host_writer=None, host_check=None)
    assert serializer.deserialize(data=data, transfer=(), host_reader=None) == {'answer': 42}
    with pytest.raises(TypeError): serializer.serialize(value=1, unknown=1)
    c.close()
    with pytest.raises(RuntimeError): serializer.deserialize(data=data)


@pytest.mark.parametrize('expression', [
    'pyv8.Script()', 'pyv8.Script(context=c, source="42", unknown=1)',
    'pyv8.PromiseResolver()', 'pyv8.PromiseResolver(context=c, unknown=1)',
    'pyv8.Serializer()', 'pyv8.Serializer(context=c, unknown=1)',
    'pyv8.JSPromise(unknown=1)', 'c.define_global()',
    'c.define_global(name="x", value=1, unknown=1)',
])
def test_native_argument_parse_failures_are_type_errors(expression):
    child(f'''
c=pyv8.Context(Window)
try:
    try:{expression}
    except TypeError:pass
    else:raise AssertionError('invalid argument accepted')
    assert c.exec_js('42')==42
finally:c.close()
''')


def test_compiled_script_and_global_binding_keyword_contracts():
    c = pyv8.Context(global_this=Window, timeout=None)
    try:
        script = pyv8.Script(context=c, source='answer', filename='contract.js')
        c.define_global(name='answer', value=42, attributes=0)
        assert c.exec_js(program=script, filename=None, timeout=None) == 42
        c.set_deadline(seconds=1)
        c.clear_deadline()
        # These APIs have METH_O signatures, unlike the keyword-capable ones above.
        with pytest.raises(TypeError): c.to_js(value=1)
        with pytest.raises(TypeError): pyv8.current_context(isolate=None)
    finally: c.close()


def test_stub_has_no_duplicate_members():
    tree = ast.parse((ROOT / 'pyv8/_pyv8.pyi').read_text(encoding='utf-8'))
    for node in tree.body:
        if not isinstance(node, ast.ClassDef): continue
        seen = set()
        for method in node.body:
            if not isinstance(method, ast.FunctionDef): continue
            # Property getter/setter pairs are intentional repetitions.
            if any(isinstance(d, ast.Attribute) and d.attr == 'setter' for d in method.decorator_list):
                continue
            assert method.name not in seen, (node.name, method.name)
            seen.add(method.name)
