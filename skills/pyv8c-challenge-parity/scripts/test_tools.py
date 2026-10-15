"""Tests of evidence semantics and real process ownership (no live websites)."""

import json
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import harness
import process_runner
from trace_analyze import analyze


class EvidenceTests(unittest.TestCase):
    def test_browser_close_error_is_visible_and_on_creator_thread(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            native_file = root / "native.bin"
            native_file.write_bytes(b"test")
            threads = []

            class Page:
                def goto(self, url):
                    pass

                def evaluate(self, expression):
                    return '{"type":"null"}'

                def run_until_idle(self, timeout):
                    pass

            class Browser:
                def __init__(self, **kwargs):
                    threads.append(threading.get_ident())
                    self.idl_path = native_file

                def new_profile(self):
                    return SimpleNamespace(new_page=Page)

                def close(self):
                    threads.append(threading.get_ident())
                    raise RuntimeError("close failed")

            native = SimpleNamespace(
                __file__=str(native_file), v8_version=lambda: "test"
            )
            package = SimpleNamespace(_pyv8=native, __version__="test")
            modules = {
                "pyv8c": SimpleNamespace(Browser=Browser),
                "pyv8": package,
                "pyv8._pyv8": native,
            }
            config = {
                "side": "here",
                "mode": "fixture",
                "resources": {},
                "trace": False,
                "viewport": {"width": 100, "height": 100},
                "url": "https://local.test/",
                "seconds": 0,
            }
            with patch.dict("sys.modules", modules):
                harness.worker(config, root / "result.json")
            result = json.loads((root / "result.json").read_text())
            self.assertEqual(result["status"], "error")
            self.assertEqual(result["cleanup"]["status"], "error")
            self.assertEqual(threads, [threading.get_ident()] * 2)
            self.assertTrue(
                any(error["phase"] == "cleanup.browser" for error in result["errors"])
            )

    def test_structured_trace_retains_realm_and_value_type(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "trace.jsonl"
            p.write_text(
                json.dumps(
                    {
                        "kind": "trace",
                        "realm": "FRAME",
                        "access": "getter",
                        "member": "location",
                        "value": {"type": "undefined"},
                        "error": {"type": "undefined"},
                    }
                )
                + "\n"
            )
            result = analyze(p)
            self.assertEqual(result["unparsed_trace_events"], 0)
            self.assertEqual(result["groups"][0]["realm"], "FRAME")
            self.assertEqual(result["groups"][0]["signal"], "undefined")

    def test_failures_never_compare_equal(self):
        for status in ("error", "timeout"):
            result = {"status": status, "observation": {"status": "not_observed"}}
            self.assertEqual(harness.compare(result, result), "inconclusive")

    def test_not_ready_never_means_success(self):
        result = {"status": "not_ready", "observation": {"status": "value", "value": 1}}
        self.assertEqual(harness.compare(result, result), "target_not_reached")

    def test_unobserved_is_not_undefined(self):
        a = {"status": "ok", "observation": {"status": "not_observed"}}
        self.assertEqual(harness.compare(a, a), "not_observed")
        b = {
            "status": "ok",
            "observation": {"status": "value", "value": {"type": "undefined"}},
        }
        self.assertEqual(harness.compare(b, b), "equal")

    def test_null_differs_from_undefined(self):
        a = {
            "status": "ok",
            "observation": {"status": "value", "value": {"type": "undefined"}},
        }
        b = {
            "status": "ok",
            "observation": {"status": "value", "value": {"type": "null"}},
        }
        self.assertEqual(harness.compare(a, b), "different")

    def test_fixture_requires_exact_main_resource(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "fixture.json"
            p.write_text(json.dumps({"url": "https://local.test/", "resources": {}}))
            with self.assertRaises(ValueError):
                harness.load_fixture(p)

    def test_fixture_body_file_and_query_are_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "fixture.json"
            (p.parent / "main.html").write_text("hello")
            p.write_text(
                json.dumps(
                    {
                        "url": "https://local.test/?v=1",
                        "resources": {
                            "https://local.test/?v=1": {"body_file": "main.html"}
                        },
                    }
                )
            )
            f = harness.load_fixture(p)
            self.assertEqual(f["resources"]["https://local.test/?v=1"]["body"], "hello")

    def test_trace_has_no_void_name_suppression(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "trace.log"
            p.write_text(
                "[TOP] method: Node.appendChild(x) -> undefined\n[FRAME] getter: Foo.x -> undefined\n[TOP] method: X.y() -> 1, error: TypeError\n"
            )
            groups = analyze(p)["groups"]
            self.assertTrue(
                any(
                    g["name"] == "Node.appendChild" and g["signal"] == "undefined"
                    for g in groups
                )
            )
            self.assertEqual({g["realm"] for g in groups}, {"TOP", "FRAME"})

    def test_unknown_structured_trace_preserves_raw_sample(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "trace.jsonl"
            p.write_text('{"kind":"trace","args":[{"unknown":42}]}\n')
            result = analyze(p)
            self.assertEqual(result["unparsed_trace_events"], 1)
            self.assertIn("42", result["groups"][0]["samples"][0]["raw"])


class ProcessTests(unittest.TestCase):
    def run_script(self, body, timeout=3):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script = root / "worker.py"
            script.write_text(
                "import sys,json,time,subprocess\nfrom pathlib import Path\n"
                'assert sys.stdin.readline().strip()=="start"\n'
                'p=Path(sys.argv[2]).with_name("result.json")\n' + body,
                encoding="utf-8",
            )
            return process_runner.run_worker(script, {}, root, timeout)

    def test_success_and_owner_cleanup(self):
        r = self.run_script(
            'p.write_text(json.dumps({"status":"ok","observation":{"status":"value","value":1}}))'
        )
        self.assertEqual(r["status"], "ok")
        self.assertFalse(r["process_cleanup"]["forced"])

    def test_exception_is_failure(self):
        r = self.run_script('raise RuntimeError("visible failure")')
        self.assertEqual(r["status"], "error")
        self.assertNotEqual(r["returncode"], 0)

    def test_timeout_preserves_partial_observation(self):
        r = self.run_script(
            'p.write_text(json.dumps({"status":"running","stages":{"navigation":"started"}}))\ntime.sleep(60)',
            0.5,
        )
        self.assertEqual(r["status"], "timeout")
        self.assertTrue(r["process_cleanup"]["forced"])
        self.assertEqual(r["stages"]["navigation"], "started")

    def test_orphan_descendant_forced_cleanup_is_not_success(self):
        r = self.run_script(
            'subprocess.Popen([sys.executable,"-c","import time;time.sleep(60)"])\np.write_text(json.dumps({"status":"ok"}))'
        )
        self.assertEqual(r["status"], "error")
        self.assertTrue(r["process_cleanup"]["forced"])

    @unittest.skipUnless(os.name == "nt", "Windows Job Object")
    def test_job_close_failure_is_reported(self):
        original = process_runner.WindowsJob.close

        def fail_after_close(job):
            original(job)
            raise OSError("simulated close reporting failure")

        with patch.object(process_runner.WindowsJob, "close", fail_after_close):
            r = self.run_script('p.write_text(json.dumps({"status":"ok"}))')
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["process_cleanup"]["status"], "error")


if __name__ == "__main__":
    unittest.main()
