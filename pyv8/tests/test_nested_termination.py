import pytest
from pyv8c import pyv8


def test_nested_termination_cannot_be_swallowed_by_a_python_reporter():
    context = pyv8.Context(object())
    seen = []
    def reenter():
        try:
            context.exec_js('while(true){}', timeout=0.02)
        except pyv8.JavaScriptTerminated:
            seen.append('inner')
    try:
        context.expose(reenter=reenter)
        with pytest.raises(pyv8.JavaScriptTerminated):
            context.exec_js('reenter();let end=Date.now()+200;while(Date.now()<end){};42', timeout=0.1)
        assert seen == ['inner']
        assert context.exec_js('42') == 42
    finally:
        context.close()
