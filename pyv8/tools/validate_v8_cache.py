"""Verify that restored Windows V8 build outputs can link the extension."""
from pathlib import Path
import sys

NATIVE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(NATIVE_ROOT))

from build_support import _snapshot_link_inputs
from v8_build import V8BuildConfig, V8BuildError, cppgc_microtask_queue_enabled


def validate_cache(config):
    if not config._can_reuse_build():
        raise V8BuildError('V8 build stamp, headers, library or ICU data is missing or incompatible')
    llvm = config.v8_home / 'third_party' / 'llvm-build' / 'Release+Asserts'
    for tool in ('clang-cl.exe', 'lld-link.exe'):
        if not (llvm / 'bin' / tool).is_file():
            raise V8BuildError(f'V8 LLVM tool is missing: {tool}')
    if not (config.output_dir / 'obj' / 'mksnapshot.ninja').is_file():
        raise V8BuildError('V8 mksnapshot link metadata is missing')
    if not _snapshot_link_inputs(config.output_dir):
        raise V8BuildError('V8 mksnapshot Rust link inputs are missing')
    cppgc_microtask_queue_enabled(config.output_dir)


def main():
    try:
        config = V8BuildConfig.from_environment(NATIVE_ROOT, download_and_build=True)
        validate_cache(config)
    except (OSError, ValueError, V8BuildError) as error:
        print(f'V8 cache is invalid: {error}', file=sys.stderr)
        return 1
    print(f'V8 cache validated: {config.version}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
