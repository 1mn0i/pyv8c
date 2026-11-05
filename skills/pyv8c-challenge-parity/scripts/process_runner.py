"""Own one worker tree; never terminate a user's existing Chrome processes."""

from __future__ import annotations
import ctypes
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


class WindowsJob:
    """Assign the gated worker before it can launch transport/browser children."""

    def __init__(self, process):
        from ctypes import wintypes as w

        class Basic(ctypes.Structure):
            _fields_ = [
                ("process_time", ctypes.c_int64),
                ("job_time", ctypes.c_int64),
                ("flags", w.DWORD),
                ("minimum", ctypes.c_size_t),
                ("maximum", ctypes.c_size_t),
                ("active_limit", w.DWORD),
                ("affinity", ctypes.c_size_t),
                ("priority", w.DWORD),
                ("scheduling", w.DWORD),
            ]

        class IO(ctypes.Structure):
            _fields_ = [
                (name, ctypes.c_uint64)
                for name in (
                    "read_ops",
                    "write_ops",
                    "other_ops",
                    "read_bytes",
                    "write_bytes",
                    "other_bytes",
                )
            ]

        class Extended(ctypes.Structure):
            _fields_ = [
                ("basic", Basic),
                ("io", IO),
                ("process_memory", ctypes.c_size_t),
                ("job_memory", ctypes.c_size_t),
                ("peak_process", ctypes.c_size_t),
                ("peak_job", ctypes.c_size_t),
            ]

        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        self.api.CreateJobObjectW.restype = w.HANDLE
        self.api.CreateJobObjectW.argtypes = [ctypes.c_void_p, w.LPCWSTR]
        self.api.SetInformationJobObject.argtypes = [
            w.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            w.DWORD,
        ]
        self.api.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
        self.api.QueryInformationJobObject.argtypes = [
            w.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            w.DWORD,
            ctypes.c_void_p,
        ]
        self.api.TerminateJobObject.argtypes = [w.HANDLE, w.UINT]
        self.api.CloseHandle.argtypes = [w.HANDLE]
        self.handle = self.api.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            limits = Extended()
            limits.basic.flags = 0x2000  # KILL_ON_JOB_CLOSE
            if not self.api.SetInformationJobObject(
                self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            if not self.api.AssignProcessToJobObject(self.handle, int(process._handle)):
                raise ctypes.WinError(ctypes.get_last_error())
        except BaseException:
            self.api.CloseHandle(self.handle)
            raise

    def active(self):
        class Accounting(ctypes.Structure):
            _fields_ = [
                ("times", ctypes.c_int64 * 4),
                ("faults", ctypes.c_uint32),
                ("total", ctypes.c_uint32),
                ("active", ctypes.c_uint32),
                ("ended", ctypes.c_uint32),
            ]

        info = Accounting()
        if not self.api.QueryInformationJobObject(
            self.handle, 1, ctypes.byref(info), ctypes.sizeof(info), None
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        return info.active

    def terminate(self):
        if not self.api.TerminateJobObject(self.handle, 1):
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self):
        if not self.api.CloseHandle(self.handle):
            raise ctypes.WinError(ctypes.get_last_error())


def _wait_for_log_release(paths, timeout=5):
    """Wait for terminated Windows descendants to release inherited log handles."""
    from ctypes import wintypes as w

    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, ctypes.c_void_p,
                                w.DWORD, w.DWORD, w.HANDLE]
    api.CreateFileW.restype = w.HANDLE
    api.CloseHandle.argtypes = [w.HANDLE]
    deadline = time.monotonic() + timeout
    for path in paths:
        while True:
            handle = api.CreateFileW(str(path), 0x80000000, 0, None, 3, 0, None)
            if handle != ctypes.c_void_p(-1).value:
                if not api.CloseHandle(handle):
                    raise ctypes.WinError(ctypes.get_last_error())
                break
            error = ctypes.get_last_error()
            if error not in (32, 33) or time.monotonic() >= deadline:
                raise ctypes.WinError(error)
            time.sleep(0.01)


def run_worker(script, config, directory, timeout):
    directory = Path(directory)
    cfg = directory / "config.json"
    cfg.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
    result_path = directory / "result.json"
    stdout_path, stderr_path = directory / "stdout.log", directory / "stderr.log"
    result = {
        "status": "error",
        "observation": {"status": "not_observed"},
        "errors": [],
    }
    job = proc = None
    timed_out = False
    cleanup = {"status": "ok", "forced": False}
    try:
        with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
            proc = subprocess.Popen(
                [sys.executable, str(script), "--worker", str(cfg)],
                stdin=subprocess.PIPE,
                stdout=stdout,
                stderr=stderr,
                start_new_session=os.name != "nt",
            )
            if os.name == "nt":
                job = WindowsJob(proc)
            proc.stdin.write(b"start\n")
            proc.stdin.flush()
            proc.stdin.close()
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
            if job:
                active = job.active()
                if active:
                    cleanup.update(forced=True, remaining_before_termination=active)
                    job.terminate()
                    deadline = time.monotonic() + 5
                    while job.active() and time.monotonic() < deadline:
                        time.sleep(0.05)
                    if job.active():
                        cleanup.update(status="error", error="owned job still active")
            else:
                try:
                    os.killpg(proc.pid, 0)
                except ProcessLookupError:
                    pass
                else:
                    cleanup["forced"] = True
                    os.killpg(proc.pid, signal.SIGKILL)
            proc.wait(timeout=5)
        if result_path.exists():
            result = json.loads(result_path.read_text(encoding="utf-8"))
        result["returncode"] = proc.returncode
        if timed_out:
            result["status"] = "timeout"
        elif proc.returncode != 0 or result.get("status") == "running":
            result["status"] = "error"
        if cleanup["forced"] and not timed_out:
            result["status"] = "error"
            cleanup["reason"] = "worker exited with owned descendants still active"
    except BaseException as exc:
        result["status"] = "error"
        result.setdefault("errors", []).append(
            {"phase": "supervisor", "class": type(exc).__name__, "message": str(exc)}
        )
        if proc is not None:
            try:
                if job:
                    job.terminate()
                elif os.name != "nt":
                    os.killpg(proc.pid, signal.SIGKILL)
                else:
                    proc.kill()  # ownership failure leaves worker behind the unopened gate
                proc.wait(timeout=5)
            except BaseException as failure:
                cleanup.update(status="error", error=str(failure))
    finally:
        if proc is not None and proc.stdin is not None:
            try:
                proc.stdin.close()
            except OSError as exc:
                cleanup.update(status="error", error=str(exc))
        if job:
            try:
                job.close()
            except OSError as exc:
                cleanup.update(status="error", error=str(exc))
        if os.name == "nt" and cleanup["forced"]:
            try:
                _wait_for_log_release((stdout_path, stderr_path))
            except OSError as exc:
                cleanup.update(status="error", error=f"log release: {exc}")
    if cleanup["status"] == "error":
        result["status"] = "error"
    result["process_cleanup"] = cleanup
    result.setdefault("evidence", {}).update(
        stdout=str(stdout_path), stderr=str(stderr_path), worker_result=str(result_path)
    )
    return result
