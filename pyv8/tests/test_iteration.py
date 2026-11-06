"""Synchronous iterable bindings: semantics, lifetime and host failures."""
import pytest
from pyv8c import pyv8
from tests.values import snapshot


def source_context(kind=pyv8.IterationKind.pair):
    class Source:
        __v8_idl_spec__ = pyv8.IdlSpec(iteration_kind=kind, iteration_source="__v8_iteration_source__")
        def __init__(self): self.items = [("a", "a"), ("b", "b")]
        def __v8_iteration_source__(self):
            items = iter(tuple(self.items))
            return lambda: next(items, None)
    owner = Source()
    context = pyv8.Context(type("Global", (), {})())
    context.expose(source=owner)
    return context, owner


@pytest.mark.parametrize("kind,default", [(2, "entries"), (3, "values")])
def test_iteration_default_and_completion_stays_done(kind, default):
    context, source = source_context(kind)
    assert context.exec_js(f"Object.getPrototypeOf(source)[Symbol.iterator] === source.{default}")
    assert snapshot(context.exec_js("Array.from(source.keys())")) == ["a", "b"]
    context.exec_js("globalThis.iter = source.values(); iter.next(); iter.next(); iter.next()")
    source.items.append(("c", "c"))
    assert context.exec_js("iter.next().done") is True
    assert snapshot(context.exec_js("Array.from(source.values())")) == ["a", "b", "c"]
    context.close()


def test_for_each_this_arg_exception_and_ignores_next_override():
    context, source = source_context()
    result = snapshot(context.exec_js("""(() => {
        const iter=source.entries(); Object.getPrototypeOf(iter).next=()=>({done:true});
        const out=[]; source.forEach(function(v,k,s){out.push([v,k,s===source,this.tag])},{tag:1});
        try { source.forEach(()=>{throw new RangeError('stop')}) } catch(e) {out.push(e.name)}
        try { source.forEach(null) } catch(e) {out.push(e.name)}
        try { source.values.call({}) } catch(e) {out.push(e.name)}
        return out;
    })()"""))
    assert result == [["a", "a", True, 1], ["b", "b", True, 1], "RangeError", "TypeError", "TypeError"]
    context.close()


def test_source_failure_and_malformed_pair_propagate():
    context, source = source_context()
    source.items = [("broken",)]
    assert context.exec_js("(()=>{try{source.values().next()}catch(e){return e.name}})()") == "TypeError"
    context.close()


@pytest.mark.parametrize("options", [
    {"array":1}, {"iterate":"source"}, {"array_methods":1},
    {"iteration_kind":2}, {"iteration_source":"source"},
    {"iteration_kind":1,"iteration_source":"source"},
    {"iteration_kind":2,"iteration_source":"source","array_methods":1},
])
def test_invalid_combinations_and_removed_fields_fail(options):
    with pytest.raises(TypeError): pyv8.IdlSpec(**options)


def test_cursor_releases_on_gc_and_context_close():
    import weakref, gc
    class Source:
        __v8_idl_spec__ = pyv8.IdlSpec(iteration_kind=2, iteration_source="__v8_iteration_source__")
        def __init__(self): self.refs=[]
        def __v8_iteration_source__(self):
            items=iter((("key","value"),))
            def cursor(): return next(items, None)
            self.refs.append(weakref.ref(cursor))
            return cursor
    owner=Source()
    context=pyv8.Context(type("Global",(),{})())
    context.expose(source=owner)
    context.exec_js("for(let i=0;i<32;i++) source.values().next()")
    for _ in range(3):
        context.gc();context.exec_js("0");gc.collect()
    assert all(ref() is None for ref in owner.refs)
    context.exec_js("globalThis.retained=source.values()")
    assert owner.refs[-1]() is not None
    context.close();gc.collect()
    assert all(ref() is None for ref in owner.refs)


def test_factory_must_return_a_callable():
    class Source:
        __v8_idl_spec__ = pyv8.IdlSpec(iteration_kind=2, iteration_source="__v8_iteration_source__")
        def __v8_iteration_source__(self): return 3
    context=pyv8.Context(type("Global",(),{})())
    context.expose(source=Source())
    assert context.exec_js("(()=>{try{source.values()}catch(e){return e.name}})()") == 'TypeError'
    context.close()


def test_cursor_python_failure_is_preserved_at_the_boundary():
    class Source:
        __v8_idl_spec__ = pyv8.IdlSpec(iteration_kind=2, iteration_source="__v8_iteration_source__")
        def __v8_iteration_source__(self):
            def cursor(): raise ValueError('cursor failure')
            return cursor
    context=pyv8.Context(type("Global",(),{})())
    context.expose(source=Source())
    try:
        with pytest.raises(ValueError, match="cursor failure"):
            context.exec_js("source.values().next()")
        assert context.exec_js("42") == 42
    finally: context.close()


def test_close_during_cursor_call_is_safe():
    class Source:
        __v8_idl_spec__ = pyv8.IdlSpec(iteration_kind=2, iteration_source="__v8_iteration_source__")
        def __v8_iteration_source__(self):
            def cursor():
                context.close()
                return ("a", "b")
            return cursor
    context=pyv8.Context(type("Global",(),{})())
    context.expose(source=Source())
    try:
        with pytest.raises(pyv8.JSException, match="context is closed"):
            context.exec_js("source.values().next()")
    finally: context.close()
