"""IDL metadata extraction boundary.

The implementation currently lives beside the generated-C++ parser because
Chromium's IDL and C++ evidence must be merged deterministically. This module
is the stable home for future IDL-only readers.
"""

from .cpp import _idl_metadata

__all__ = ["_idl_metadata"]
