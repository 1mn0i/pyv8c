"""What building every interface costs, and what it builds.

The threshold is deliberately loose: the point is to catch a change that makes compilation
quadratic, not to police a machine's speed.  The member count is the structural half -- it is what
a page sees, so it may only move when a decision about the binding does.
"""

import time

import pytest

pytest.importorskip("pyv8c.pyv8", reason="native pyv8 extension is not importable")

from pyv8c.engine.bindings import Idl, Bindings  # noqa: E402
from pyv8c.platform.bootstrap import bootstrap_runtime  # noqa: E402


BUDGET_MS = 5000
# The measured browser's omissions affect the count as well as the IDL release.
MEMBERS = {("154.0.8030.0", "Chrome/156.0.8078.80"): 10367,
           ("145.0.7632.162", None): 10308}


def test_every_interface_compiles_within_budget():
    bindings = Bindings(Idl.load(), runtime=bootstrap_runtime(),
                                 stub_logging="off")
    started = time.perf_counter()
    members = 0
    for name in bindings.idl.names():
        spec = bindings.class_config_for(name)
        members += len(spec.methods) + len(spec.attributes)
        iteration = bindings.idl.interface(name).iteration
        if iteration and iteration["kind"] != "indexed":
            # These generated operation-table members moved from MethodSpec into native views.
            # Count them to preserve the pre-refactor structural guard; array intrinsics were
            # never operation-table members and remain outside this count.
            members += len(iteration["methods"])
    elapsed = (time.perf_counter() - started) * 1000

    reference = bindings.idl.profile.browser if bindings.idl.profile is not None else None
    assert members == MEMBERS[(bindings.idl.metadata["chromium_version"], reference)], "a page's member count moved"
    assert elapsed < BUDGET_MS, f"compiling took {elapsed:.0f} ms"
