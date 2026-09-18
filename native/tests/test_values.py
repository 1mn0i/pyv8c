"""Stage 2 acceptance: the three-state boundary, and a realm that releases it.

The rule under test is that a value leaving JavaScript is a primitive, the
original Python host object, or a handle -- never a silently expanded structure
-- and that closing the realm takes every handle with it.
"""

from __future__ import annotations

import pytest

import v8n


@pytest.fixture()
def realm():
    realm = v8n.Realm()
    try:
        yield realm
    finally:
        realm.close()


def test_primitives_convert(realm):
    assert realm.evaluate("1 + 1") == 2
    assert realm.evaluate("1.5") == 1.5
    assert realm.evaluate("'text'") == "text"
    assert realm.evaluate("true") is True
    assert realm.evaluate("false") is False
    assert realm.evaluate("null") is v8n.Null
    assert realm.evaluate("undefined") is v8n.Undefined
    assert bool(v8n.Undefined) is False
    assert bool(v8n.Null) is False


def test_objects_come_back_as_handles(realm):
    # The boundary does not enumerate: reading one property of one object must
    # not run arbitrary JavaScript on the way out.
    result = realm.evaluate("({a: 1, b: [1, 2, 3]})")
    assert isinstance(result, v8n.Value)
    assert result.a == 1
    assert result.b.to_python() == [1, 2, 3]


def test_to_python_materialises_on_request(realm):
    assert realm.evaluate("({a: 1, b: [1, 2], c: {d: 3}})").to_python() == {
        "a": 1,
        "b": [1, 2],
        "c": {"d": 3},
    }


def test_to_python_preserves_cycles(realm):
    value = realm.evaluate("(() => { const o = {name: 'loop'}; o.self = o; return o; })()")
    materialised = value.to_python()
    assert materialised["name"] == "loop"
    # The cycle has to come back as the same Python dict, not a copy that would
    # recurse forever.
    assert materialised["self"] is materialised


def test_to_python_preserves_shared_references(realm):
    materialised = realm.evaluate(
        "(() => { const shared = {n: 1}; return {a: shared, b: shared}; })()"
    ).to_python()
    assert materialised["a"] is materialised["b"]


def test_functions_stay_handles_and_are_callable(realm):
    function = realm.evaluate("(x => x * 2)")
    assert isinstance(function, v8n.Value)
    assert function(21) == 42
    assert function("not a number") != 42  # coerced by JavaScript, not an error


def test_calling_a_non_callable_says_so(realm):
    with pytest.raises(TypeError, match="not callable"):
        realm.evaluate("({})")()


def test_a_missing_property_reads_as_undefined(realm):
    # JavaScript answers undefined for a property that does not exist; the
    # wrapper must not turn that into an error, because `if (obj.maybe)` is how
    # JavaScript is written.
    assert realm.evaluate("({})").missing is v8n.Undefined
    assert realm.evaluate("({a: 1})").b is v8n.Undefined


def test_a_host_object_round_trips_to_the_same_instance(realm):
    class Host:
        def __init__(self, value):
            self.value = value

    host = Host(7)
    identity = realm.evaluate("(o => o)")
    assert identity(host) is host

    # And the wrapper reads and writes Python attributes, which is what keeps a
    # host object usable without an installed interface.
    read = realm.evaluate("(o => o.value)")
    write = realm.evaluate("(o => { o.value = 9; })")
    assert read(host) == 7
    write(host)
    assert host.value == 9
    assert identity(host) is host  # still the same wrapper, not a second one


def test_handle_becomes_invalid_when_the_realm_closes():
    realm = v8n.Realm()
    handle = realm.evaluate("({a: 1})")
    assert handle.is_valid is True
    realm.close()
    assert handle.is_valid is False
    with pytest.raises(RuntimeError):
        handle.to_python()
    with pytest.raises(RuntimeError):
        realm.evaluate("1")


def test_a_handle_only_works_with_the_realm_that_made_it():
    first = v8n.Realm()
    second = v8n.Realm()
    try:
        handle = first.evaluate("({a: 1})")
        with pytest.raises(RuntimeError, match="realm that made it"):
            second.evaluate("(o => o)")(handle)
    finally:
        first.close()
        second.close()


def test_javascript_exception_becomes_a_python_error(realm):
    with pytest.raises(RuntimeError, match="boom"):
        realm.evaluate("throw new Error('boom')")


def test_the_host_owns_the_error_mapping(realm):
    class V8TypeError(Exception):
        __v8_error_name__ = "TypeError"

    v8n._v8n.set_error_map({"TypeError": V8TypeError})
    try:
        with pytest.raises(V8TypeError, match="not a function"):
            realm.evaluate("null()")
    finally:
        v8n._v8n.set_error_map({})


def test_a_python_error_crosses_into_javascript_and_back(realm):
    class Custom(Exception):
        __v8_error_name__ = "Error"

    def blow_up():
        raise Custom("from python")

    rethrow = realm.evaluate("(f => { try { f(); } catch (error) { throw error; } })")
    with pytest.raises(Custom, match="from python"):
        rethrow(blow_up)


def test_a_python_callable_can_be_passed_and_called_by_javascript(realm):
    # The other half of the round trip: a host object is a value JavaScript can
    # hold, and a callable has to be something JavaScript can call.
    def double(value):
        return value * 2

    apply = realm.evaluate("(f, x) => f(x)")
    assert apply(double, 21) == 42
    # And the function identity survives the crossing in both directions.
    identity = realm.evaluate("(f => f)")
    assert identity(double) is double


def test_one_python_callable_is_one_javascript_function(realm):
    # The shape of the leak this ownership design exists to prevent: a binding
    # that mints a fresh JavaScript function, and a fresh record, on every
    # crossing.  A process that hands over callbacks would then grow for as long
    # as it runs.  JavaScript can see the difference directly, so the property
    # is observable without measuring memory.
    def callback():
        return 1

    same = realm.evaluate("((f, g) => f === g)")
    assert same(callback, callback) is True

    # Repeating it does not mint anything new.
    for _ in range(100):
        assert same(callback, callback) is True


def test_a_handle_offers_the_javascript_operations_explicitly(realm):
    """Conversion is asked for, never applied on the way out.

    A handle is a reference; what a host does with it -- coerce it, read one
    property, call it -- is one explicit operation each, which is what keeps the
    boundary three-state instead of eagerly expanding whatever crosses.
    """
    box = realm.evaluate("(x => Object(x))")

    assert box("42").to_number() == 42.0
    assert box("nope").to_number() != box("nope").to_number()  # NaN, not an error
    assert box(7).to_string() == "7"

    # Boolean() answers for the value, not for the primitive behind it: every
    # boxed primitive is an object, and every object is truthy.  A value that
    # arrives as a primitive never needs this -- it is already a Python bool,
    # str, int, float, None or one of the two null singletons.
    assert box("").to_boolean() is True
    assert box({}).to_boolean() is True
    # String() of a plain object is the JavaScript answer, not a Python one.
    assert realm.evaluate("(x => x)")({"a": 1}).to_string() == "[object Object]"


def test_a_handle_reads_keys_and_deletes_them(realm):
    target = realm.evaluate("(x => x)")({"a": 1, "b": 2})
    assert target.keys() == ("a", "b")

    # Deleting is a JavaScript operation and answers the way JavaScript does:
    # false when the property is non-configurable, not an exception.
    assert target.delete("a") is True
    assert target.keys() == ("b",)

    # get() rather than getattr: a JavaScript property named after one of the
    # wrapper's own methods has to stay reachable.
    shadowing = realm.evaluate("(x => x)")({"to_python": 5})
    assert shadowing.get("to_python") == 5
    assert shadowing.to_python() == {"to_python": 5}


def test_a_handle_calls_with_an_explicit_receiver(realm):
    # The iterator protocol needs it: `next` only works when it is called on the
    # iterator it came from, and a method read out of an object is not bound.
    array = realm.evaluate("(x => x)")([1, 2, 3])
    method = array.iterator()
    assert callable(method)

    iterator = method.call(array)
    step = iterator.get("next").call(iterator)
    assert step.get("done") is False
    assert step.get("value") == 1

    # An object with no iterator answers undefined, which is what a caller
    # checks before trying to walk it.
    assert realm.evaluate("({})").iterator() is v8n.Undefined
    assert realm.evaluate("(x => x)")([1, 2]).is_array is True
    assert realm.evaluate("({})").is_array is False


def test_a_python_object_can_be_wrapped_into_a_handle(realm):
    # A handle is a reference to something JavaScript holds, so a host that
    # operates on one of its own objects from Python needs this rather than a
    # conversion: identity survives, and writes reach the object.
    class Host:
        def __init__(self):
            self.value = 5

    host = Host()
    handle = realm.wrap(host)
    assert handle.get("value") == 5

    handle.value = 9
    assert host.value == 9
    assert realm.wrap(host).get("value") == 9
    # The same object always has one JavaScript value.
    assert realm.evaluate("(o => o)")(host) is host


def test_python_containers_cross_as_javascript_containers(realm):
    # A host that answers a query with a structure has to hand over a structure
    # JavaScript can index.  Without this a dict would arrive as a host object
    # whose "properties" are Python attributes -- `obj.get` being the dict's own
    # method -- and a list would have no `length` at all.
    identity = realm.evaluate("(x => x)")

    assert realm.evaluate("(x => Array.isArray(x))")(identity([1, 2, 3])) is True
    assert realm.evaluate("(x => x.length)")(identity([1, 2, 3])) == 3
    assert realm.evaluate("(x => x[1])")(identity((1, 2))) == 2

    assert realm.evaluate("(x => Object.keys(x).join(','))")(identity({"a": 1, "b": 2})) == "a,b"
    assert realm.evaluate("(x => x.a)")(identity({"a": 1})) == 1
    # A dict with a key that shares a name with one of its methods is the case
    # that tells a converted object from a host object.
    assert realm.evaluate("(x => x.get)")(identity({"get": 7})) == 7

    # Recursion is what makes a nested structure usable.
    nested = identity({"items": [1, {"deep": True}]})
    assert realm.evaluate("(x => x.items[1].deep)")(nested) is True


def test_a_bound_method_crosses_as_a_fresh_function_each_time(realm):
    # Identity is pointer identity, and `host.method` builds a new Python object
    # on every access, so two accesses are two JavaScript functions.  Pinning it
    # here because it is a real difference from the previous binding, which
    # keyed its cache by equality and therefore collapsed them.
    #
    # Sharing them would mean keying by (receiver, function), which means the
    # wrapper owns a receiver and prepends it on every call.  That belongs with
    # the bridge, which already knows an interface member's receiver, and not in
    # the value layer, whose job is the three states.
    class Host:
        def method(self):
            return 2

    host = Host()
    same = realm.evaluate("((f, g) => f === g)")
    assert same(host.method, host.method) is False

    # Both still unwrap to the same underlying Python callable, so a caller that
    # holds one of them is not cut off from the host object.
    identity = realm.evaluate("(f => f)")
    assert identity(host.method) == host.method
    assert identity(host.method).__self__ is host


def test_realm_churn_does_not_accumulate():
    psutil = pytest.importorskip("psutil")
    import gc

    process = psutil.Process()

    def private_megabytes():
        gc.collect()
        return process.memory_info().private / 1048576

    for _ in range(3):
        v8n.Realm().close()
    baseline = private_megabytes()

    for _ in range(50):
        realm = v8n.Realm()
        realm.evaluate("({a: [1, 2, 3]})")
        realm.close()

    growth = private_megabytes() - baseline
    assert growth < 32, f"realm churn grew private memory by {growth:.1f} MB"
