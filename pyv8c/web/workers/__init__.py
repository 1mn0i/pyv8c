"""Worker API, runtime, and structured-clone support."""

from .clone import structured_clone

__all__ = ["Worker", "WorkerGlobalScope", "DedicatedWorkerGlobalScope", "structured_clone"]


def __getattr__(name):
    if name in {"Worker", "WorkerGlobalScope", "DedicatedWorkerGlobalScope"}:
        from .api import DedicatedWorkerGlobalScope, Worker, WorkerGlobalScope
        return {
            "Worker": Worker,
            "WorkerGlobalScope": WorkerGlobalScope,
            "DedicatedWorkerGlobalScope": DedicatedWorkerGlobalScope,
        }[name]
    raise AttributeError(name)
