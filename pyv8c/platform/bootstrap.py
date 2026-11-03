"""Create one Feature runtime per Browser."""

from ..features.runtime import FeatureRuntime
from ..features.catalog import CATALOG


def bootstrap_runtime():
    """Return a fresh Browser-local FeatureRuntime.

    The catalog is process-shared; caches and loaded state belong to one Browser.
    """
    runtime = FeatureRuntime(CATALOG)
    runtime.preload_eager()
    return runtime



