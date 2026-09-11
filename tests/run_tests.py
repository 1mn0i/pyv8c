"""Run the entire suite, including pytest functions and unittest TestCases.

Prefer ``python -m pytest``. This direct-script entry point forwards pytest
arguments and always runs relative to the source tree, regardless of cwd.
"""

import os
from pathlib import Path
import sys


def main():
    project_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project_root))
    os.chdir(project_root)
    try:
        import pytest
    except ImportError:
        print("pytest未安装，请先执行：python -m pip install pytest", file=sys.stderr)
        return 1
    # The former selector remains accepted, but never selects a partial suite.
    arguments = [argument for argument in sys.argv[1:] if argument != "--pytest"]
    return pytest.main(arguments)


if __name__ == "__main__":
    raise SystemExit(main())
