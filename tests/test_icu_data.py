import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import pyv8


class TestICUDataCompatibility(unittest.TestCase):
    """验证 V8 扩展与 ICU 数据文件必须来自同一次构建。"""

    def test_intl_datetime_format_remains_stable(self):
        """匹配的 ICU 数据应支持重复创建 Intl 日期格式化器。"""

        context = pyv8.Context(object)
        for _ in range(100):
            result = context.exec_js(
                "new Intl.DateTimeFormat('zh-CN').format(new Date(0))"
            )
            self.assertTrue(result)

    def test_mismatched_icu_data_is_rejected_before_v8_startup(self):
        """错误 ICU 数据应报告 RuntimeError，而不是触发原生 OOM。"""

        package_dir = Path(pyv8.__file__).resolve().parent
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_root = Path(temporary_directory)
            temporary_package = temporary_root / "pyv8"
            shutil.copytree(package_dir, temporary_package)

            icu_path = temporary_package / "icudtl.dat"
            icu_data = bytearray(icu_path.read_bytes())
            icu_data[-1] ^= 0x01
            icu_path.write_bytes(icu_data)

            environment = os.environ.copy()
            environment["PYTHONPATH"] = str(temporary_root)
            # Importing starts nothing, so the check is asked for by the first use --
            # and it still answers before V8 itself comes up.
            result = subprocess.run(
                [sys.executable, "-c", "import pyv8; pyv8.Context(object)"],
                cwd=temporary_root,
                env=environment,
                capture_output=True,
                text=True,
                timeout=30,
            )

        output = result.stdout + result.stderr
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ICU data does not match this _pyv8 build", output)
        self.assertNotIn("Fatal process out of memory", output)


if __name__ == "__main__":
    unittest.main()
