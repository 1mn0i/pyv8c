"""Compilation is part of a native execution budget, not an unguarded prelude."""
import subprocess
import sys

import pytest


@pytest.mark.parametrize('entry', ['exec', 'script'])
def test_compilation_observes_budget_and_leaves_context_usable(entry):
    code = r'''
import pyv8
source = 'var values=[' + '0,' * 400000 + '0]; values.length'
isolate = pyv8.Isolate()
context = isolate.context(object, timeout=0.001 if ENTRY == 'script' else 0)
try:
    try:
        if ENTRY == 'script':
            pyv8.Script(context, source)
        else:
            context.exec_js(source, timeout=0.001)
    except pyv8.JavaScriptTerminated:
        pass
    else:
        raise AssertionError('compilation escaped the execution budget')
    context.timeout = 0
    assert context.exec_js('1+1') == 2
finally:
    context.close()
    isolate.close()
print('recovered')
'''
    result = subprocess.run([sys.executable, '-u', '-X', 'faulthandler', '-c',
                             'ENTRY = ' + repr(entry) + '\n' + code],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.splitlines() == ['recovered']


@pytest.mark.parametrize('pretend', ['spoof', 'raising'])
def test_script_argument_requires_a_real_native_script(pretend):
    code = r'''
import pyv8
class Pretender:
    @property
    def __class__(self):
        if PRETEND == 'raising':
            raise LookupError('native type validation read __class__')
        return pyv8.Script
isolate = pyv8.Isolate()
context = isolate.context(object)
try:
    try:
        context.exec_js(Pretender())
    except TypeError:
        pass
    else:
        raise AssertionError('a spoofed Script was accepted')
    assert context.exec_js('1+1') == 2
finally:
    context.close()
    isolate.close()
print('recovered')
'''
    result = subprocess.run([sys.executable, '-u', '-X', 'faulthandler', '-c',
                             'PRETEND = ' + repr(pretend) + '\n' + code],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.splitlines() == ['recovered']


@pytest.mark.parametrize('timeout', [0, 2])
def test_explicit_exec_budget_overrides_context_compilation_default(timeout):
    code = r'''
import pyv8
source = 'var values=[' + '0,' * 400000 + '0]; values.length'
isolate = pyv8.Isolate()
context = isolate.context(object, timeout=0.000001)
try:
    assert context.exec_js(source, timeout=TIMEOUT) == 400001
    assert context.timeout == 0.000001
finally:
    context.close()
    isolate.close()
print('completed')
'''
    result = subprocess.run([sys.executable, '-u', '-X', 'faulthandler', '-c',
                             'TIMEOUT = ' + repr(timeout) + '\n' + code],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.splitlines() == ['completed']


@pytest.mark.parametrize('invalid', ['filename', 'isolate'])
def test_invalid_program_does_not_consume_pending_termination(invalid):
    code = r'''
import pyv8
isolate = pyv8.Isolate()
context = isolate.context(object)
sibling = pyv8.Isolate()
other = sibling.context(object)
compiled = pyv8.Script(other, '1+1')
try:
    context.terminate()
    try:
        if INVALID == 'filename':
            context.exec_js('1+1', filename=object())
        else:
            context.exec_js(compiled)
    except (TypeError, RuntimeError):
        pass
    else:
        raise AssertionError('invalid program was accepted')
    try:
        context.exec_js('1+1')
    except pyv8.JavaScriptTerminated:
        pass
    else:
        raise AssertionError('invalid program consumed pending termination')
    assert context.exec_js('1+1') == 2
finally:
    context.close()
    isolate.close()
    other.close()
    sibling.close()
print('recovered')
'''
    result = subprocess.run([sys.executable, '-u', '-X', 'faulthandler', '-c',
                             'INVALID = ' + repr(invalid) + '\n' + code],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.splitlines() == ['recovered']
