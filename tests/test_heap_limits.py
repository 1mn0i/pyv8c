"""堆上限与外部内存记账；上限行为在独立进程里验证，越界那一条会 abort。"""

import gc
import subprocess
import sys
import textwrap
import unittest

import pyv8

#: 必须返回数组并由 Python 持有：返回数字会被 V8 当作无用分配消掉，测不到堆上限。
HOLD_STRINGS = (
    "(()=>{const c=[];for(let i=0;i<%(count)d;i++)"
    "c.push(String.fromCharCode(120).repeat(1024));return c;})()"
)


class TestHeapLimits(unittest.TestCase):
    """验证有界堆增长与外部内存记账；上限用 16MB，默认上限撞不到。"""

    def run_code(self, code, timeout=120):
        return subprocess.run(
            [sys.executable, "-c", textwrap.dedent(code)],
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    def test_a_full_heap_grows_within_its_bound(self):
        """上限内抬升：16MB 上限下持有 20MB 存活对象仍然成功。"""

        result = self.run_code(
            """
            import pyv8

            pyv8.configure(params=["--max-old-space-size=16"])
            context = pyv8.Context(object)
            held = context.exec_js(%(hold)r)
            print(round(held.length * 1024 / (1024 * 1024)))
            """
            % {"hold": HOLD_STRINGS % {"count": 20 * 1024}}
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.split(), ["20"])

    def test_the_growth_stops_at_the_bound(self):
        """越界后不再抬升：同一上限下持有 100MB 仍按原样 OOM。"""

        result = self.run_code(
            """
            import pyv8

            pyv8.configure(params=["--max-old-space-size=16"])
            context = pyv8.Context(object)
            context.exec_js(%(hold)r)
            """
            % {"hold": HOLD_STRINGS % {"count": 100 * 1024}}
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("heap limit", result.stdout + result.stderr)

    def test_sources_and_host_objects_are_reported_and_given_back(self):
        """持有多少就报多少，释放后回到原值。"""

        isolate = pyv8.Isolate()
        context = isolate.context(object)
        try:
            # The global host object is already one.
            baseline = isolate.external_memory
            self.assertGreater(baseline, 0)

            source = "var a = 1;" + "x" * 500
            script = pyv8.Script(context, source)
            self.assertEqual(isolate.external_memory - baseline, len(source))

            del script
            gc.collect()
            self.assertEqual(isolate.external_memory, baseline)
        finally:
            context.close()
            isolate.close()

    def test_a_closed_isolate_reports_nothing(self):
        """关闭之后不再报告。"""

        isolate = pyv8.Isolate()
        context = isolate.context(object)
        script = pyv8.Script(context, "1 + 1")
        del script
        gc.collect()
        context.close()
        isolate.close()
        self.assertEqual(isolate.external_memory, 0)


if __name__ == "__main__":
    unittest.main()
