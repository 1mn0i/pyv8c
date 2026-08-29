"""Worker public types.

The implementations remain coupled to network events during this migration;
this module is the domain entry point for callers and future extraction.
"""

from ..network.api import DedicatedWorkerGlobalScope, Worker, WorkerGlobalScope

__all__ = ["Worker", "WorkerGlobalScope", "DedicatedWorkerGlobalScope"]
