import pytest
import pyv8


class Host:
    __v8_idl_spec__ = pyv8.IdlSpec(
        methods=(pyv8.MethodSpec(name="echo", callback="echo"),
                 pyv8.MethodSpec(name="text", callback="text"),
                 pyv8.MethodSpec(name="number", callback="number")),
        attributes=(pyv8.AttributeSpec(name="value", getter="get_value", setter="set_value"),),
    )

    def echo(self, value):
        return value

    def text(self, value):
        return value._to_string()

    def number(self, value):
        return value._to_number()

    def get_value(self):
        return self.value

    def set_value(self, value):
        self.value = value


@pytest.fixture
def context():
    owner = pyv8.Context(Host(), preserve_arguments=True)
    try:
        yield owner
    finally:
        owner.close()


def test_preserved_arguments_do_not_probe_objects(context):
    assert context.exec_js("""(() => {
        const {proxy,revoke}=Proxy.revocable({},{});revoke();
        const object={get unused(){throw Error('unexpected read')}};
        for(const item of [object,[object],proxy]){
            if(echo(item)!==item)return false;
            value=item;if(value!==item)return false;
        }
        return true;
    })()""")


def test_default_context_keeps_eager_data_conversion():
    class Root:
        pass
    owner = pyv8.Context(Root())
    observed = []
    try:
        owner.expose(capture=lambda value: observed.append(value))
        owner.exec_js("capture({items:[1,2]})")
        assert observed == [{"items": [1, 2]}]
    finally:
        owner.close()


@pytest.mark.parametrize("preserve", [False, True])
def test_argument_mode_controls_when_getters_run(preserve):
    observed = []
    owner = pyv8.Context(object(), preserve_arguments=preserve)
    try:
        owner.expose(capture=observed.append)
        owner.exec_js("globalThis.reads=0; capture({get value(){reads++;return 42}})")
        assert owner.exec_js("reads") == (0 if preserve else 1)
        if preserve:
            assert isinstance(observed[0], pyv8.JSObject)
            assert observed[0]._get("value") == 42
        else:
            assert observed == [{"value": 42}]
        assert owner.exec_js("reads") == 1
    finally:
        observed.clear()
        owner.close()


def test_argument_mode_is_per_context_on_shared_isolate():
    isolate = pyv8.Isolate()
    eager = isolate.context(object())
    preserved = isolate.context(object(), preserve_arguments=True)
    try:
        for owner in (eager, preserved):
            owner.expose(is_handle=lambda value: isinstance(value, pyv8.JSObject))
        for _ in range(3):
            assert preserved.exec_js("is_handle({})") is True
            assert eager.exec_js("is_handle({})") is False
    finally:
        eager.close()
        preserved.close()
        del eager, preserved
        import gc
        gc.collect()
        isolate.close()


@pytest.mark.parametrize("expression, expected", [
    ("undefined", pyv8.Undefined), ("null", pyv8.Null),
    ("false", False), ("42", 42), ("'text'", "text"),
])
def test_preserved_mode_keeps_primitive_argument_conversion(context, expression, expected):
    observed = []
    context.expose(capture=observed.append)
    context.exec_js(f"capture({expression})")
    assert observed == [expected]
    assert type(observed[0]) is type(expected)


def test_preserved_host_object_keeps_python_identity(context):
    class Payload:
        pass

    observed = []
    host = Payload()
    context.expose(capture=observed.append, host=host)
    context.exec_js("capture(host)")
    assert observed[0] is host


def test_exposed_callback_can_ignore_an_unreadable_extra_argument(context):
    context.expose(first=lambda value, *_extra: value)
    assert context.exec_js("""(() => {
        const {proxy, revoke} = Proxy.revocable({}, {});
        revoke();
        return first(42, proxy);
    })()""") == 42


def test_explicit_snapshot_preserves_aliases_and_does_not_change_return_conversion(context):
    observed = []
    context.expose(capture=observed.append)
    context.exec_js("""
        const child = {value: 42};
        const source = {a: child, b: child};
        source.self = source;
        capture(source);
    """)
    snapshot = observed[0]._to_python()
    assert snapshot["a"] is snapshot["b"]
    assert snapshot["self"] is snapshot
    snapshot["a"]["value"] = 99
    assert context.exec_js("child.value") == 42
    assert context.exec_js("({items: [1, 2]})") == {"items": [1, 2]}


def test_explicit_keys_do_not_read_values_or_include_inherited_and_symbol_keys(context):
    context.exec_js("""
        globalThis.reads = 0;
        globalThis.source = Object.create({inherited: 1});
        Object.defineProperty(source, 'visible', {enumerable: true, get(){reads++;return 42}});
        Object.defineProperty(source, 'hidden', {value: 2});
        source[Symbol('key')] = 3;
    """)
    source = context.glob._get("source")
    assert source._keys() == ["visible"]
    assert context.exec_js("reads") == 0
    assert source._get("visible") == 42
    assert context.exec_js("reads") == 1
    assert source._get("missing") is pyv8.Undefined


def test_explicit_iterator_call_preserves_receiver_and_result_identity(context):
    context.exec_js("""
        globalThis.reads = 0;
        globalThis.source = {
            get [Symbol.iterator]() {
                reads++;
                return function() { if (this !== source) throw 1; return source; };
            }
        };
    """)
    source = context.glob._get("source")
    method = source._iterator_method()
    result = method._invoke(source)
    context.expose(result=result)
    assert context.exec_js("reads === 1 && result === source") is True


@pytest.mark.parametrize("method, hint, expected", [
    ("_to_string", "string", "42"), ("_to_number", "number", 42),
])
def test_explicit_conversion_calls_to_primitive_once(context, method, hint, expected):
    context.exec_js("""
        globalThis.calls = [];
        globalThis.source = {
            [Symbol.toPrimitive](hint) { calls.push(hint); return 42; },
            toString() { throw Error('unexpected toString'); },
            valueOf() { throw Error('unexpected valueOf'); }
        };
    """)
    source = context.glob._get("source")
    assert getattr(source, method)() == expected
    assert context.exec_js("calls") == [hint]


@pytest.mark.parametrize("expression", ["42", "null", "undefined", "Symbol('thrown')", "({marker:42})"])
def test_original_thrown_value_survives_python_conversion(context, expression):
    assert context.exec_js("""(() => {
        const original=""" + expression + """;
        try{text({toString(){throw original}})}catch(error){return error===original}
        return false;
    })()""")


def test_symbol_and_bigint_conversions(context):
    assert context.exec_js("text(123456789012345678901234567890n)") == "123456789012345678901234567890"
    assert context.exec_js("(() => {try{number(1n)}catch(error){return error instanceof TypeError}})()")
    assert context.exec_js("(() => {try{text(Symbol())}catch(error){return error instanceof TypeError}})()")
    assert context.exec_js("number({[Symbol.toPrimitive](hint){return hint==='number'?42:0}})") == 42


def test_lazy_property_read_keeps_identity_and_only_get_trap(context):
    source = context.exec_js("Object.create(null)")
    context.expose(source=source)
    context.exec_js("source.child={get ignored(){throw 1}};globalThis.accesses=[]")
    proxy = context.exec_js("new Proxy(source,{has(){throw 2},get(target,key){accesses.push(key);return target[key]}})")
    child = proxy._get("child")
    assert isinstance(child, pyv8.JSObject)
    context.expose(child=child)
    assert context.exec_js("child===source.child")
    assert context.exec_js("accesses") == ["child"]


def test_exception_original_can_be_rejected_without_copy(context):
    with pytest.raises(pyv8.JSException) as caught:
        context.exec_js("globalThis.sentinel={value:42};throw sentinel")
    context.expose(original=caught.value.original)
    assert context.exec_js("original===sentinel")


def test_preserved_argument_keeps_isolate_alive_until_released():
    import gc

    isolate = pyv8.Isolate()
    context = isolate.context(Host(), preserve_arguments=True)
    captured = []
    context.expose(capture=lambda value: captured.append(value))
    context.exec_js("capture({field:42})")
    context.close()
    assert captured[0]._get("field") == 42
    with pytest.raises(RuntimeError, match="persistent handle"):
        isolate.close()
    captured.clear()
    del context
    gc.collect()
    assert isolate.close()
