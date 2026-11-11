"""Check local prerequisites before starting a potentially lengthy V8 build."""
import platform
import shutil
import sys

def check(version=None, which=shutil.which, implementation=None):
    """Return actionable errors without downloading or modifying anything."""
    version = version or sys.version_info
    errors = []
    implementation = implementation or sys.implementation.name
    if implementation != 'cpython' or tuple(version[:2]) != (3, 12):
        errors.append('Native wheel builds require CPython 3.12; select its interpreter first.')
    if not which('git'):
        errors.append('Git was not found on PATH; install Git before downloading V8.')
    if not which('python') and not which('python3'):
        errors.append('Python was not found on PATH; depot_tools needs a Python executable.')
    return errors


def main():
    errors = check()
    print(f'Host: {platform.system()} {platform.machine()}; interpreter: {sys.executable}')
    if errors:
        for error in errors:
            print(f'ERROR: {error}')
        return 1
    print('Basic prerequisites passed. Platform toolchain and V8 dependencies are checked during build.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
