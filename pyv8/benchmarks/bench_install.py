"""What it costs to install the same class into one more realm.

This historical experiment measured a build where every context built its own templates,
so every member's name was converted to V8
strings and registered again for each realm.  A per-class plan cached across
realms was on the table; this is the bill it would have saved, measured against a
class with nothing to install.

On this machine, for one realm, across runs:

    nothing to install           0.25 - 0.27 ms
    60 members (120 callbacks)   0.37 - 0.47 ms
    per member                   1.7 - 3.6 us
    ten realms                   1.0 - 2.2 ms

So a full set of DOM interfaces -- a few thousand members -- would reinstall for
roughly ten milliseconds per realm, once, on a path that already costs a few
milliseconds to build the realm at all.  That is not worth the per-isolate string
handles and callback data it would take to share the work, so the install path is
left as it was. Current template sharing differs; repeat the measurement before
using the historical figures to draw conclusions.

The other half of that plan item was the runtime string: a replaceable attribute
rebuilds its V8 name on every write.  Only ``[Replaceable]`` attributes do, the
host declares none itself (it passes through whatever the IDL says), and the write
is dominated by the property creation that follows -- so that half was left alone
too.
"""

import timeit

import pyv8
from pyv8 import AttributeSpec, IdlSpec

MEMBERS = 60


def attribute(name):
    return AttributeSpec(
        name=name,
        getter="get_" + name,
        setter="set_" + name,
        location=0,
        property_flags=0,
        receiver_check=0,
        cross_origin_get_check=1,
        cross_origin_set_check=0,
        side_effect=1,
    )


class Empty:
    __v8_idl_spec__ = IdlSpec(constructor_behavior=0, has_constructor=0)


def build_loaded():
    namespace = {
        "__v8_idl_spec__": IdlSpec(
            constructor_behavior=0,
            has_constructor=0,
            attributes=tuple(attribute("attr%d" % i) for i in range(MEMBERS)),
        ),
        "__init__": lambda self, win=None: None,
    }
    for i in range(MEMBERS):
        namespace["get_attr%d" % i] = (lambda index: lambda self, win=None: index)(i)
        namespace["set_attr%d" % i] = (lambda index: lambda self, win=None, value=None: None)(i)
    return type("Loaded", (), namespace)


Loaded = build_loaded()


def one_realm(cls):
    context = pyv8.Context(cls())
    context.close()


def measure(cls):
    return min(timeit.repeat(lambda: one_realm(cls), number=5, repeat=5)) / 5 * 1000


def main():
    empty = measure(Empty)
    loaded = measure(Loaded)
    print("one realm, nothing to install      : %8.2f ms" % empty)
    print("one realm, %d members (%d callbacks): %8.2f ms" % (MEMBERS, MEMBERS * 2, loaded))
    print("installation per realm             : %8.2f ms" % (loaded - empty))
    print("per member                         : %8.1f us" % ((loaded - empty) * 1000 / MEMBERS))
    print("ten realms                         : %8.2f ms" % ((loaded - empty) * 10))


if __name__ == "__main__":
    main()
