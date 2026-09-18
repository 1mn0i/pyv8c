#!/usr/bin/env python
"""Build ``v8n._v8n`` against the V8 that this checkout already provisions.

V8 provisioning stays in the parent project.  ``v8_build.py`` already knows how
to check out, patch and build V8 15.2.20.1 and caches the result under
``_pyv8/.pyv8-build``; reusing it means both extensions link the same library,
the same headers and the same ``icudtl.dat`` instead of drifting apart.  What
lives here is only what is specific to the new extension: its sources, its
include path, and the two macros that record which V8 it was built against.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

from setuptools import Extension, setup
from setuptools.command.build_ext import build_ext

NATIVE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = NATIVE_ROOT.parent  # the _pyv8 checkout, which owns V8_VERSION

# The facade package lives at PROJECT_ROOT/v8n and is reached through
# package-dir in pyproject.toml; the extension lands inside it.
PACKAGE_NAME = "v8n"
EXTENSION_NAME = "v8n._v8n"
ICU_DATA_NAME = "icudtl.dat"

# An abi3 wheel must be built from the oldest supported interpreter's headers,
# because the build interpreter fixes the limited-API baseline baked into the
# object files.
ABI3_MINIMUM_PYTHON = (3, 12)
ABI3_BUILD_OVERRIDE = "PYV8_ALLOW_NEWER_ABI3_BUILD"

sys.path.insert(0, str(PROJECT_ROOT))

from v8_build import (  # noqa: E402
    V8BuildConfig,
    V8BuildError,
    cppgc_microtask_queue_enabled,
    file_sha256,
)

sources = sorted(
    str(path.relative_to(NATIVE_ROOT)).replace(os.sep, "/")
    for path in (NATIVE_ROOT / "src").rglob("*.cpp")
)

libraries = ["v8_monolith"]
extra_compile_args: list[str] = []
extra_link_args: list[str] = []

if os.name == "nt":
    # Identical to the existing extension: /MT keeps the CRT static so the
    # extension never depends on a redistributable the host may lack.
    extra_compile_args = ["/O2", "/GL", "/MT", "/EHsc", "/Gy", "/Zi", "/std:c++20", "/Zc:__cplusplus"]
    libraries.extend(["Dbghelp", "Shlwapi", "Winmm", "Advapi32"])
else:
    extra_compile_args = ["-std=c++20"]
    extra_link_args = []
    if sys.platform.startswith("linux"):
        libraries.append("rt")
    if sys.platform.startswith("darwin"):
        extra_compile_args.append("-stdlib=libc++")

if os.environ.get("PYV8_SANITIZERS"):
    if os.name == "nt":
        raise RuntimeError("PYV8_SANITIZERS requires the Linux validation environment")
    sanitizers = os.environ["PYV8_SANITIZERS"].split(",")
    if not set(sanitizers) <= {"address", "undefined"}:
        raise RuntimeError("PYV8_SANITIZERS accepts address,undefined")
    flags = ["-fsanitize=" + ",".join(sanitizers), "-fno-omit-frame-pointer"]
    extra_compile_args.extend(flags + ["-O1", "-g"])
    extra_link_args.extend(flags)


class V8NBuildExt(build_ext):
    """Build the extension against a selected local or managed V8 version."""

    user_options = build_ext.user_options + [
        ("v8-version=", None, "V8 Git tag to use"),
        ("v8-home=", None, "existing V8 source or binary directory"),
        ("v8-output=", None, "existing or generated V8 output directory"),
        ("v8-jobs=", None, "number of parallel V8 build jobs"),
        ("download-v8", None, "download and build the selected V8 version"),
        ("force-v8-build", None, "rebuild V8 even when a matching cache exists"),
    ]
    boolean_options = build_ext.boolean_options + ["download-v8", "force-v8-build"]

    def initialize_options(self):
        super().initialize_options()
        self.v8_version = None
        self.v8_home = None
        self.v8_output = None
        self.v8_jobs = None
        self.download_v8 = None
        self.force_v8_build = None

    def _configuration(self) -> V8BuildConfig:
        return V8BuildConfig.from_environment(
            PROJECT_ROOT,
            version=self.v8_version,
            v8_home=self.v8_home,
            output_dir=self.v8_output,
            jobs=self.v8_jobs,
            download_and_build=True if self.download_v8 else None,
            force_build=True if self.force_v8_build else None,
        )

    def _configure_extension(self, config: V8BuildConfig) -> tuple[Path, str]:
        icu_data = config.icu_data_file()
        if icu_data is None:
            raise V8BuildError(
                "icudtl.dat was not found; set PYV8_ICU_DATA to the matching V8 ICU data file"
            )
        icu_sha256 = file_sha256(icu_data)

        # v8::MicrotaskQueue's layout follows V8_CPPGC_MICROTASK_QUEUE, which V8
        # passes on its own compile command line and ships in no public header.
        # Built without it, this translation unit disagrees with the library
        # about the class and faults on every virtual call into a queue, so the
        # answer is read back from the V8 build and compiled in.
        cppgc_queues = cppgc_microtask_queue_enabled(config.output_dir)

        for extension in self.extensions:
            if extension.name != EXTENSION_NAME:
                continue
            extension.include_dirs = list(extension.include_dirs or []) + [
                str(NATIVE_ROOT / "include"),
                str(NATIVE_ROOT / "src"),
                str(config.include_dir),
            ]
            extension.library_dirs = list(extension.library_dirs or []) + [
                str(config.library_dir)
            ]
            extension.define_macros = [
                macro
                for macro in (extension.define_macros or [])
                if macro[0] not in ("PYV8_ICU_DATA_SHA256", "V8_CPPGC_MICROTASK_QUEUE")
            ] + [("PYV8_ICU_DATA_SHA256", f'"{icu_sha256}"')]
            if cppgc_queues:
                extension.define_macros.append(("V8_CPPGC_MICROTASK_QUEUE", None))
        return icu_data, icu_sha256

    def _copy_icu_data(self, icu_data: Path) -> None:
        """Place icudtl.dat where the built package will look for it.

        The digest is compiled into the extension, so a stale copy next to the
        module fails loudly on import rather than silently pairing this build
        with another V8's data.
        """
        if self.inplace:
            destination = NATIVE_ROOT.parent / PACKAGE_NAME / ICU_DATA_NAME
        else:
            destination = Path(self.build_lib) / PACKAGE_NAME / ICU_DATA_NAME
        destination.parent.mkdir(parents=True, exist_ok=True)
        if icu_data.resolve() != destination.resolve():
            shutil.copy2(icu_data, destination)

    @staticmethod
    def _fingerprint(config: V8BuildConfig, icu_sha256: str) -> dict:
        library = config.library_file.stat()
        return {
            "version": config.version,
            "v8_home": str(config.v8_home),
            "output_dir": str(config.output_dir),
            "gn_args": config.gn_args,
            "cppgc_microtask_queue": cppgc_microtask_queue_enabled(config.output_dir),
            "library_size": library.st_size,
            "library_mtime_ns": library.st_mtime_ns,
            "icu_sha256": icu_sha256,
        }

    def _require_rebuild_for_changed_v8(self, fingerprint: dict) -> None:
        """Force a rebuild when the linked V8 is not the one we built against.

        setuptools only tracks source timestamps, so a V8 rebuilt in place under
        the same name would otherwise leave the extension linked against the old
        symbols.
        """
        fingerprint_file = NATIVE_ROOT / "build" / "v8n-extension.json"
        if fingerprint_file.is_file():
            try:
                previous = json.loads(fingerprint_file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                previous = None
            if previous != fingerprint:
                self.force = True

    def run(self):
        build_python = sys.version_info[:2]
        if (
            build_python != ABI3_MINIMUM_PYTHON
            and os.environ.get(ABI3_BUILD_OVERRIDE) != "1"
        ):
            required = ".".join(map(str, ABI3_MINIMUM_PYTHON))
            actual = ".".join(map(str, build_python))
            raise RuntimeError(
                f"v8n abi3 wheels must be built with Python {required} headers "
                f"(current build interpreter is {actual}); "
                f"set {ABI3_BUILD_OVERRIDE}=1 only for local non-release builds"
            )
        if sysconfig_gil_disabled():
            raise RuntimeError(
                "v8n does not support free-threaded CPython builds; "
                "use a GIL-enabled CPython interpreter"
            )

        try:
            config = self._configuration()
            config.prepare()
            icu_data, icu_sha256 = self._configure_extension(config)
            fingerprint = self._fingerprint(config, icu_sha256)
            self._require_rebuild_for_changed_v8(fingerprint)
            super().run()
            self._copy_icu_data(icu_data)
            fingerprint_file = NATIVE_ROOT / "build" / "v8n-extension.json"
            fingerprint_file.parent.mkdir(parents=True, exist_ok=True)
            fingerprint_file.write_text(
                json.dumps(fingerprint, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        except V8BuildError as exc:
            raise RuntimeError(f"V8 preparation failed: {exc}") from exc


def sysconfig_gil_disabled() -> bool:
    import sysconfig

    return bool(sysconfig.get_config_var("Py_GIL_DISABLED"))


setup(
    ext_modules=[
        Extension(
            EXTENSION_NAME,
            sources=sources,
            include_dirs=[],
            library_dirs=[],
            libraries=libraries,
            extra_compile_args=extra_compile_args,
            extra_link_args=extra_link_args,
            define_macros=[("Py_LIMITED_API", "0x030C0000")],
            py_limited_api=True,
            language="c++",
        )
    ],
    cmdclass={"build_ext": V8NBuildExt},
    options={"bdist_wheel": {"py_limited_api": "cp312"}},
)
