import importlib.util
import json
from pathlib import Path
from zipfile import ZipFile

import pytest


spec = importlib.util.spec_from_file_location(
    'distribution_idl_build', Path(__file__).parents[3] / 'build_tools' / 'idl_build.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_packaged_idl_version_is_checked(tmp_path):
    (tmp_path / 'CHROMIUM_VERSION').write_text('154.0.8030.0\n', encoding='utf-8')
    archive = tmp_path / 'pyv8c' / 'pyv8c' / 'idl' / 'idl.zip'
    archive.parent.mkdir(parents=True)
    with ZipFile(archive, 'w') as package:
        package.writestr('idl.json', json.dumps({'idl': {'chromium_version': '154.0.8030.0'}}))
    module.validate_packaged_idl(tmp_path)
    (tmp_path / 'CHROMIUM_VERSION').write_text('155.0.0.0', encoding='utf-8')
    with pytest.raises(RuntimeError, match='does not match'):
        module.validate_packaged_idl(tmp_path)


def test_missing_archive_fails_preflight(tmp_path):
    (tmp_path / 'CHROMIUM_VERSION').write_text('154.0.8030.0', encoding='utf-8')
    with pytest.raises(RuntimeError, match='Cannot validate packaged IDL'):
        module.validate_packaged_idl(tmp_path)
