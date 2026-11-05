"""Failures must not turn unknown or dirty V8 state into a successful build."""
from pathlib import Path
from unittest.mock import patch

import pytest

from tests.test_v8_build import v8_build


def test_checkout_failure_is_not_silently_bypassed(tmp_path, monkeypatch):
    (tmp_path / 'V8_VERSION').write_text('15.4.68')
    config = v8_build.V8BuildConfig.from_environment(tmp_path, {'PYV8_BUILD_V8': '1'})
    temporal = config.v8_home / 'src/objects/js-temporal-objects.cc'
    temporal.parent.mkdir(parents=True)
    temporal.write_text('fixture')
    monkeypatch.setattr(config, '_ensure_depot_tools', lambda: None)
    monkeypatch.setattr(config, '_bootstrap_depot_tools', lambda: None)
    def fail():
        raise v8_build.V8BuildError('checkout must be clean')
    monkeypatch.setattr(config, '_checkout_v8', fail)
    monkeypatch.setattr(config, '_build_v8', lambda: pytest.fail('must not build'))
    with pytest.raises(v8_build.V8BuildError, match='must be clean'):
        config.prepare()


def test_patch_setup_failure_restores_earlier_files(tmp_path, monkeypatch):
    (tmp_path / 'V8_VERSION').write_text('15.4.68')
    config = v8_build.V8BuildConfig.from_environment(tmp_path, {})
    directory = config.v8_home / 'src/objects'
    directory.mkdir(parents=True)
    backing = directory / 'backing-store.cc'
    original = b'return isolate->heap()->allocator()->RetryCustomAllocate(\n        fn, internal::AllocationType::kOld,\n'
    backing.write_bytes(original)
    atomics = directory / 'js-atomics-synchronization.h'
    atomics.write_bytes(b'  ExternalPointerMember<kWaiterQueueNodeTag> waiter_queue_head_;\n  std::atomic<uint32_t> state_;\n')
    write = Path.write_bytes
    def fail_second(path, data):
        if path == atomics:
            raise OSError('simulated write failure')
        return write(path, data)
    with patch.object(Path, 'write_bytes', fail_second):
        with pytest.raises(OSError, match='simulated write failure'):
            with config._source_compatibility_patches():
                pytest.fail('must not reach body')
    assert backing.read_bytes() == original


def test_microtask_abi_requires_evidence_or_explicit_choice(tmp_path, monkeypatch):
    monkeypatch.delenv('PYV8_CPPGC_MICROTASK_QUEUE', raising=False)
    with pytest.raises(v8_build.V8BuildError, match='cannot determine'):
        v8_build.cppgc_microtask_queue_enabled(tmp_path)
    monkeypatch.setenv('PYV8_CPPGC_MICROTASK_QUEUE', '0')
    assert v8_build.cppgc_microtask_queue_enabled(tmp_path) is False
    obj = tmp_path / 'obj'
    obj.mkdir()
    (obj / 'v8_base.ninja').write_text('defines = -DV8_ENABLE_WEBASSEMBLY\n')
    assert v8_build.cppgc_microtask_queue_enabled(tmp_path) is False
    (obj / 'v8_base.ninja').write_text('defines = -DV8_CPPGC_MICROTASK_QUEUE\n')
    with pytest.raises(v8_build.V8BuildError, match='conflicts'):
        v8_build.cppgc_microtask_queue_enabled(tmp_path)
    monkeypatch.delenv('PYV8_CPPGC_MICROTASK_QUEUE')
    assert v8_build.cppgc_microtask_queue_enabled(tmp_path) is True
