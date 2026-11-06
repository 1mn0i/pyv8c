"""堆上限与外部内存记账；上限行为在独立进程里验证，越界终止脚本而不是进程。"""

import gc
import subprocess
import sys
import textwrap
import unittest

from pyv8c import pyv8

#: 必须返回数组并由 Python 持有：返回数字会被 V8 当作无用分配消掉，测不到堆上限。
HOLD_STRINGS = (
    "(()=>{const c=[];for(let i=0;i<%(count)d;i++)"
    "c.push(String.fromCharCode(120).repeat(1024));return c;})()"
)


class TestHeapLimits(unittest.TestCase):
    """验证有界堆增长与外部内存记账；上限用 16MB，默认上限撞不到。"""

    def test_the_context_reports_its_isolate_heap_statistics(self):
        """堆统计：三个字段都是字节，上限与提交量大于 0，使用量不超过提交量。"""
        context = pyv8.Context(object)
        statistics = context.heap_statistics()
        self.assertEqual(sorted(statistics), ["limit", "total", "used"])
        self.assertGreater(statistics["limit"], 0)
        self.assertGreater(statistics["total"], 0)
        self.assertGreaterEqual(statistics["total"], statistics["used"])

    def run_code(self, code, timeout=120):
        return subprocess.run(
            [sys.executable, "-c", textwrap.dedent(code)],
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    def test_a_script_that_stays_inside_the_budget_runs(self):
        """预算之内照常：64MB 预算下持有 32MB 存活对象仍然成功。"""

        result = self.run_code(
            """
            from pyv8c import pyv8

            pyv8.configure(params=["--max-old-space-size=64"])
            context = pyv8.Context(object)
            held = context.exec_js(%(hold)r)
            print(round(held.length * 1024 / (1024 * 1024)))
            context.close()
            """
            % {"hold": HOLD_STRINGS % {"count": 32 * 1024}}
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.split(), ["32"])

    def test_a_script_that_outgrows_the_budget_is_stopped(self):
        """预算用尽即终止脚本：64MB 预算下无限申请抛 JavaScriptTerminated，
        进程正常退出，随后同一个上下文照常执行。"""

        result = self.run_code(
            """
            from pyv8c import pyv8

            pyv8.configure(params=["--max-old-space-size=64"])
            context = pyv8.Context(object)
            try:
                context.exec_js(
                    "var a = []; for (let i = 0; i < 200000000; i++) a.push(i); 1")
                print("no termination")
            except pyv8.JavaScriptTerminated:
                print("terminated", context.heap_terminations)
            print("next", context.exec_js("1 + 1"))
            context.close()
            """
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.split(), ["terminated", "1", "next", "2"])

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
