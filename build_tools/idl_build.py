"""Validate the generated IDL shipped in source and wheel distributions."""
import json
from pathlib import Path
from zipfile import BadZipFile, ZipFile


def validate_packaged_idl(root):
    root = Path(root)
    version_file = root / 'CHROMIUM_VERSION'
    archive = root / 'pyv8c' / 'pyv8c' / 'idl' / 'idl.zip'
    try:
        expected = version_file.read_text(encoding='utf-8').strip()
        with ZipFile(archive) as package:
            metadata = json.loads(package.read('idl.json'))
        actual = metadata['idl']['chromium_version']
    except (OSError, BadZipFile, KeyError, ValueError, TypeError) as error:
        raise RuntimeError(f'Cannot validate packaged IDL against {version_file}: {error}') from error
    if not expected or actual != expected:
        raise RuntimeError(f'Packaged IDL Chromium version {actual!r} does not match '
                           f'{version_file}: {expected!r}')
