"""Baseline serialization helpers separated from Chromium source parsing."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path


def write_baseline(output_path: Path, document: dict, *, compress: bool = False) -> Path:
    """Write a compact schema 5 baseline as JSON or a single-member ZIP."""
    payload = (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    output_path = Path(output_path)
    if not (compress or output_path.suffix.lower() == ".zip"):
        output_path.write_bytes(payload)
        return output_path
    archive_path = output_path if output_path.suffix.lower() == ".zip" else output_path.with_suffix(".zip")
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        archive.writestr(archive_path.with_suffix(".json").name, payload)
    return archive_path

