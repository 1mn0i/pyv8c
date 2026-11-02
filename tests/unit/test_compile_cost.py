"""What building every interface costs, and what it builds.

The threshold is deliberately loose: the point is to catch a change that makes compilation
quadratic, not to police a machine's speed.  The member count is the structural half -- it is what
a page sees, so it may only move when a decision about the binding does.
"""

import time

import pytest

pytest.importorskip("pyv8", reason="native pyv8 extension is not importable")

from pyv8c.engine.bindings import Idl, Bindings  # noqa: E402
from pyv8c.platform.bootstrap import bootstrap_runtime  # noqa: E402


#: Measured on 2026-10-15: 10367 compiled members over 1254 interfaces, in 410-850 ms.  Read with
#: the committed profile in force, so 10361 -> 10367 when the profile moved to Chrome 155.
BUDGET_MS = 5000
#: 154 now compiles the whole baseline against the measured 154 profile (10367 was
#: the count under the older 155 profile the repository carried).
MEMBERS = {"154.0.8030.0": 10400, "145.0.7632.162": 10308}


def test_every_interface_compiles_within_budget():
    bindings = Bindings(Idl.load(), runtime=bootstrap_runtime(),
                                 stub_logging="off")
    started = time.perf_counter()
    members = 0
    for name in bindings.idl.names():
        spec = bindings.class_config_for(name)
        #: A global's context-dependent members install from the late lists; they
        #: are the same page-visible members, only placed after every operation.
        members += (len(spec.methods) + len(spec.attributes)
                    + len(spec.late_methods) + len(spec.late_attributes)
                    + len(spec.late_template_attributes) + len(spec.tail_methods))
        iteration = bindings.idl.interface(name).iteration
        if iteration and iteration["kind"] != "indexed":
            # These generated operation-table members moved from MethodSpec into native views.
            # Count them to preserve the pre-refactor structural guard; array intrinsics were
            # never operation-table members and remain outside this count.
            members += len(iteration["methods"])
    elapsed = (time.perf_counter() - started) * 1000

    assert members == MEMBERS[bindings.idl.metadata["chromium_version"]], "a page's member count moved"
    assert elapsed < BUDGET_MS, f"compiling took {elapsed:.0f} ms"
