"""Heap termination must leave room to compile, reclaim and run again."""
import subprocess
import sys

import pytest


SCRIPT = r'''
import pyv8
pyv8.configure(params=["--max-old-space-size=256"])
isolate = pyv8.Isolate()
queue = pyv8.MicrotaskQueue(isolate)
context = isolate.context(object, microtask_queue=queue)
sibling = pyv8.Isolate()
other = sibling.context(object)
try:
    other.exec_js("globalThis.marker = 42")
    ceiling = context.heap_statistics()['limit'] + 256 * 1024 * 1024
    for round in range(3):
        allocation = "globalThis.a=[];for(let i=0;i<200000000;i++)a.push(i)"
        if MODE == 'bulk':
            # Preserve large strings while growing a separate array; replay the
            # same mixed workload that previously aborted the native process.
            context.exec_js("globalThis.a='a'.repeat(64*1024*1024);a.indexOf('X')")
            allocation = ("globalThis.b='b'.repeat(96*1024*1024);b.indexOf('Y');"
                          "globalThis.c=[];for(let i=0;i<200000000;i++)c.push(i)")
        try:
            if MODE == 'checkpoint':
                context.exec_js("Promise.resolve().then(()=>{"+allocation+"});0")
                queue.checkpoint()
            else:
                context.exec_js(allocation)
        except pyv8.JavaScriptTerminated:
            pass
        else:
            raise AssertionError('heap allocation was not terminated')
        # Force a distinct compilation with the oversized array still rooted.
        compiled = pyv8.Script(context, "1+1; // recovery " + str(round))
        assert context.exec_js(compiled) == 2
        del compiled
        assert context.heap_statistics()['limit'] <= ceiling
        assert other.exec_js('marker') == 42
        context.exec_js('a=null;globalThis.b=null;globalThis.c=null')
        isolate.gc()
        print('recovered', round, flush=True)
    assert context.heap_terminations >= 3
finally:
    context.close()
    queue.close()
    isolate.close()
    other.close()
    sibling.close()
print('closed', flush=True)
'''


@pytest.mark.parametrize('mode', ['script', 'checkpoint', 'bulk'])
def test_heap_termination_allows_compilation_reclamation_and_repeated_use(mode):
    code = 'MODE = ' + repr(mode) + '\n' + SCRIPT
    result = subprocess.run([sys.executable, '-u', '-X', 'faulthandler', '-c', code],
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.splitlines() == ['recovered 0', 'recovered 1', 'recovered 2', 'closed']


def test_heap_stop_allows_diagnostics_but_renewed_growth_is_stopped():
    code = r'''
import pyv8
pyv8.configure(params=['--max-old-space-size=256'])
isolate = pyv8.Isolate()
context = isolate.context(object)
try:
    for name in ('a', 'b'):
        previous = context.heap_terminations
        try:
            context.exec_js('globalThis.'+name+'=[];for(let i=0;i<200000000;i++)'+name+'.push(i)')
        except pyv8.JavaScriptTerminated:
            pass
        else:
            raise AssertionError('renewed allocation was not terminated')
        assert context.heap_terminations > previous
        assert context.exec_js(name+'.length > 0') is True
    context.exec_js('a=null;b=null')
    isolate.gc()
    assert context.exec_js('1+1') == 2
finally:
    context.close()
    isolate.close()
print('closed')
'''
    result = subprocess.run([sys.executable, '-u', '-X', 'faulthandler', '-c', code],
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.splitlines() == ['closed']
