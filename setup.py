#!/usr/bin/env python
"""
Setup script for pyv8 - Python bindings for the V8 JavaScript engine.
This is a simplified setup.py that works with pyproject.toml.

项目元数据在pyproject.toml中定义，
此文件仅包含扩展模块的配置。
"""
import os
import json
import shutil
import sys
import sysconfig
from pathlib import Path

from setuptools import Command, Extension, setup
from setuptools.command.build_ext import build_ext

PROJECT_ROOT = Path(__file__).resolve().parent
ABI3_MINIMUM_PYTHON = (3, 12)
ABI3_BUILD_OVERRIDE = 'PYV8_ALLOW_NEWER_ABI3_BUILD'
sys.path.insert(0, str(PROJECT_ROOT))

from v8_build import (  # noqa: E402
    V8BuildConfig,
    V8BuildError,
    cppgc_microtask_queue_enabled,
    file_sha256,
    fetch_v8_versions,
    print_v8_version_choices,
    read_default_v8_version,
    save_selected_v8_version,
    select_v8_version,
)

# 获取源文件列表
sources = sorted(
    str(path.relative_to(PROJECT_ROOT)).replace(os.sep, "/")
    for source_dir in (PROJECT_ROOT / "src", PROJECT_ROOT / "src" / "faker")
    for path in source_dir.glob("*.cpp")
)

# 库配置
libraries = ["v8_monolith"]

# 平台特定配置
if os.name == 'nt':
    extra_compile_args = ["/O2", "/GL", "/MT", "/EHsc", "/Gy", "/Zi", "/std:c++20", "/Zc:__cplusplus"]
    extra_link_args = []
    libraries.extend(['Dbghelp', 'Shlwapi', 'Winmm', 'Advapi32'])
else:
    extra_compile_args = ['-std=c++20', '-fpermissive', '-Wunused-variable']
    extra_link_args = []

if sys.platform.startswith('linux'):
    libraries.append('rt')

if sys.platform.startswith('darwin'):
    extra_compile_args.append('-stdlib=libc++')

if os.environ.get('PYV8_SANITIZERS'):
    if os.name == 'nt':
        raise RuntimeError('PYV8_SANITIZERS requires the Linux validation environment')
    sanitizers = os.environ['PYV8_SANITIZERS'].split(',')
    if not set(sanitizers) <= {'address', 'undefined'}:
        raise RuntimeError('PYV8_SANITIZERS accepts address,undefined')
    flags = ['-fsanitize=' + ','.join(sanitizers), '-fno-omit-frame-pointer']
    extra_compile_args.extend(flags + ['-O1', '-g'])
    extra_link_args.extend(flags)


class PyV8BuildExt(build_ext):
    """Build the extension against a selected local or managed V8 version."""

    user_options = build_ext.user_options + [
        ('v8-version=', None, 'V8 Git tag to use'),
        ('v8-home=', None, 'existing V8 source or binary directory'),
        ('v8-output=', None, 'existing or generated V8 output directory'),
        ('v8-jobs=', None, 'number of parallel V8 build jobs'),
        ('proxy=', None, 'HTTP/HTTPS proxy used to download V8'),
        ('no-proxy', None, 'download V8 without using environment or Git proxies'),
        ('download-v8', None, 'download and build the selected V8 version'),
        ('force-v8-build', None, 'rebuild V8 even when a matching cache exists'),
    ]
    boolean_options = build_ext.boolean_options + [
        'no-proxy', 'download-v8', 'force-v8-build'
    ]

    def initialize_options(self):
        super().initialize_options()
        self.v8_version = None
        self.v8_home = None
        self.v8_output = None
        self.v8_jobs = None
        self.proxy = None
        self.no_proxy = None
        self.download_v8 = None
        self.force_v8_build = None

    def _configuration(self):
        return V8BuildConfig.from_environment(
            PROJECT_ROOT,
            version=self.v8_version,
            v8_home=self.v8_home,
            output_dir=self.v8_output,
            jobs=self.v8_jobs,
            proxy=self.proxy,
            no_proxy=True if self.no_proxy else None,
            download_and_build=True if self.download_v8 else None,
            force_build=True if self.force_v8_build else None,
        )

    def _configure_extensions(self, config):
        icu_data = config.icu_data_file()
        if icu_data is None:
            raise V8BuildError(
                'icudtl.dat was not found; set PYV8_ICU_DATA to the matching V8 ICU data file'
            )
        icu_sha256 = file_sha256(icu_data)
        # v8::MicrotaskQueue's layout follows this macro, and V8 only passes it
        # on its own compile command line; without it every call into a queue
        # from the extension would dispatch through the wrong vtable slot.
        cppgc_queues = cppgc_microtask_queue_enabled(config.output_dir)
        if not cppgc_queues:
            print(
                'V8 was built without cppgc microtask queues; '
                'Context.enqueue_microtask will refuse to run'
            )
        for extension in self.extensions:
            if extension.name != 'pyv8._pyv8':
                continue
            extension.include_dirs = list(extension.include_dirs or []) + [
                str(config.include_dir)
            ]
            extension.library_dirs = list(extension.library_dirs or []) + [
                str(config.library_dir)
            ]
            extension.define_macros = [
                macro
                for macro in (extension.define_macros or [])
                if macro[0] not in ('PYV8_ICU_DATA_SHA256', 'V8_CPPGC_MICROTASK_QUEUE')
            ] + [('PYV8_ICU_DATA_SHA256', f'"{icu_sha256}"')]
            if cppgc_queues:
                extension.define_macros.append(('V8_CPPGC_MICROTASK_QUEUE', None))

    def _copy_icu_data(self, config):
        source = config.icu_data_file()
        if source is None:
            raise V8BuildError(
                'icudtl.dat was not found; set PYV8_ICU_DATA to the matching V8 ICU data file'
            )
        if self.inplace:
            destination = PROJECT_ROOT / 'pyv8' / 'icudtl.dat'
        else:
            destination = Path(self.build_lib) / 'pyv8' / 'icudtl.dat'
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.resolve() != destination.resolve():
            shutil.copy2(source, destination)

    @staticmethod
    def _build_fingerprint(config):
        library_stat = config.library_file.stat()
        icu_data = config.icu_data_file()
        if icu_data is None:
            raise V8BuildError(
                'icudtl.dat was not found; set PYV8_ICU_DATA to the matching V8 ICU data file'
            )
        return {
            'version': config.version,
            'v8_home': str(config.v8_home),
            'output_dir': str(config.output_dir),
            'gn_args': config.gn_args,
            'cppgc_microtask_queue': cppgc_microtask_queue_enabled(config.output_dir),
            'library_size': library_stat.st_size,
            'library_mtime_ns': library_stat.st_mtime_ns,
            'icu_sha256': file_sha256(icu_data),
        }

    def _require_rebuild_for_changed_v8(self, config):
        fingerprint = self._build_fingerprint(config)
        fingerprint_file = PROJECT_ROOT / 'build' / 'pyv8-extension.json'
        previous = None
        if fingerprint_file.is_file():
            try:
                previous = json.loads(fingerprint_file.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                pass
        if previous != fingerprint:
            self.force = True
        return fingerprint_file, fingerprint

    def run(self):
        build_python = sys.version_info[:2]
        if (build_python != ABI3_MINIMUM_PYTHON and
                os.environ.get(ABI3_BUILD_OVERRIDE) != '1'):
            required = '.'.join(map(str, ABI3_MINIMUM_PYTHON))
            actual = '.'.join(map(str, build_python))
            raise RuntimeError(
                f'pyv8 abi3 wheels must be built with Python {required} '
                f'headers (current build interpreter is {actual}); '
                f'set {ABI3_BUILD_OVERRIDE}=1 only for local non-release builds'
            )
        if sysconfig.get_config_var('Py_GIL_DISABLED'):
            raise RuntimeError(
                'pyv8 does not support free-threaded CPython builds; '
                'use a GIL-enabled CPython interpreter'
            )
        try:
            config = self._configuration()
            config.prepare()
            self._configure_extensions(config)
            fingerprint_file, fingerprint = self._require_rebuild_for_changed_v8(config)
            super().run()
            self._copy_icu_data(config)
            fingerprint_file.parent.mkdir(parents=True, exist_ok=True)
            fingerprint_file.write_text(
                json.dumps(fingerprint, indent=2, sort_keys=True) + '\n',
                encoding='utf-8',
            )
        except V8BuildError as exc:
            raise RuntimeError(f'V8 preparation failed: {exc}') from exc


class BuildV8(Command):
    """Download and compile one official V8 release tag."""

    description = 'download and build a selected V8 release'
    user_options = [
        ('v8-version=', None, 'V8 Git tag to download and build'),
        ('v8-home=', None, 'V8 checkout directory (must end with v8)'),
        ('v8-output=', None, 'V8 output directory'),
        ('v8-jobs=', None, 'number of parallel build jobs'),
        ('proxy=', None, 'HTTP/HTTPS proxy used to download V8'),
        ('no-proxy', None, 'download V8 without using environment or Git proxies'),
        ('select', None, 'interactively select an official V8 release tag'),
        ('force', 'f', 'rebuild an existing matching cache'),
    ]
    boolean_options = ['no-proxy', 'select', 'force']

    def initialize_options(self):
        self.v8_version = None
        self.v8_home = None
        self.v8_output = None
        self.v8_jobs = None
        self.proxy = None
        self.no_proxy = False
        self.select = False
        self.force = False

    def finalize_options(self):
        if self.v8_jobs is not None:
            self.v8_jobs = int(self.v8_jobs)
        if self.select and self.v8_version:
            raise ValueError('--select and --v8-version cannot be used together')
        if self.proxy and self.no_proxy:
            raise ValueError('--proxy and --no-proxy cannot be used together')

    def run(self):
        try:
            selected_version = self.v8_version
            if self.select:
                selected_version = select_v8_version(
                    fetch_v8_versions(
                        proxy=self.proxy,
                        no_proxy=True if self.no_proxy else None,
                    ),
                    read_default_v8_version(PROJECT_ROOT),
                )

            config_options = {
                'version': selected_version,
                'v8_home': self.v8_home,
                'output_dir': self.v8_output,
                'jobs': self.v8_jobs,
                'proxy': self.proxy,
                'no_proxy': True if self.no_proxy else None,
                'force_build': bool(self.force),
            }
            config = None
            reused_existing = False
            if not self.force:
                try:
                    config = V8BuildConfig.from_environment(
                        PROJECT_ROOT,
                        download_and_build=False,
                        **config_options,
                    )
                    config.prepare()
                    reused_existing = True
                except V8BuildError:
                    config = None
            if config is None:
                config = V8BuildConfig.from_environment(
                    PROJECT_ROOT,
                    download_and_build=True,
                    **config_options,
                )
                config.prepare()

            save_selected_v8_version(PROJECT_ROOT, config.version)
            if reused_existing:
                print(
                    f'Reusing existing V8 {config.version} from {config.v8_home} '
                    '(use --force to rebuild)'
                )
            else:
                print(f'V8 {config.version} build completed in {config.v8_home}')
            print(f'V8 library: {config.library_file}')
        except V8BuildError as exc:
            raise RuntimeError(f'V8 build failed: {exc}') from exc


class ListV8Versions(Command):
    """List discoverable V8 release tags without downloading V8 source."""

    description = 'list available V8 release choices'
    user_options = [
        ('limit=', None, 'maximum number of recommended release choices'),
        ('proxy=', None, 'HTTP/HTTPS proxy used to query V8 versions'),
        ('no-proxy', None, 'query V8 versions without environment or Git proxies'),
    ]
    boolean_options = ['no-proxy']

    def initialize_options(self):
        self.limit = 12
        self.proxy = None
        self.no_proxy = False

    def finalize_options(self):
        self.limit = int(self.limit)
        if self.limit < 1:
            raise ValueError('--limit must be at least 1')
        if self.proxy and self.no_proxy:
            raise ValueError('--proxy and --no-proxy cannot be used together')

    def run(self):
        try:
            print_v8_version_choices(
                fetch_v8_versions(
                    proxy=self.proxy,
                    no_proxy=True if self.no_proxy else None,
                ),
                read_default_v8_version(PROJECT_ROOT),
                self.limit,
            )
        except V8BuildError as exc:
            raise RuntimeError(f'failed to list V8 versions: {exc}') from exc

# 设置扩展模块
ext_modules = [
    Extension(
        'pyv8._pyv8',
        sources=sources,
        include_dirs=['src'],
        library_dirs=[],
        libraries=libraries,
        extra_compile_args=extra_compile_args,
        extra_link_args=extra_link_args,
        define_macros=[('Py_LIMITED_API', '0x030C0000')],
        py_limited_api=True,
        language='c++'
    )
]

# 使用setuptools的setup函数
setup(
    ext_modules=ext_modules,
    cmdclass={
        'build_ext': PyV8BuildExt,
        'build_v8': BuildV8,
        'list_v8_versions': ListV8Versions,
    },
    options={'bdist_wheel': {'py_limited_api': 'cp312'}},
)
