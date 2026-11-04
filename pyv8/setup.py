#!/usr/bin/env python
"""
Setup script for pyv8 - Python bindings for the V8 JavaScript engine.
This is a simplified setup.py that works with pyproject.toml.

项目元数据在pyproject.toml中定义，
此文件仅包含扩展模块的配置。
"""
import os
import json
import hashlib
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
# The directories under src belong to the source layout, not to the build, so the
# list is collected by recursing: a new group must not need a new glob here.
sources = sorted(
    str(path.relative_to(PROJECT_ROOT)).replace(os.sep, "/")
    for path in (PROJECT_ROOT / "src").rglob("*.cpp")
)

# 库配置
libraries = ["v8_monolith"]

# 平台特定配置
if os.name == 'nt':
    extra_compile_args = ["/O2", "/GL", "/MT", "/EHsc", "/Gy", "/Zi", "/std:c++20", "/Zc:__cplusplus"]
    extra_link_args = []
    libraries.extend(['Dbghelp', 'Shlwapi', 'Winmm', 'Advapi32',
                      #: Rust std's Windows imports, linked in for Temporal.
                      'Ntdll', 'Ws2_32', 'Userenv', 'Bcrypt', 'Crypt32',
                      'Secur32', 'Iphlpapi', 'Netapi32', 'User32', 'Gdi32',
                      'PowrProf', 'Setupapi', 'Cfgmgr32', 'Ole32', 'OleAut32'])
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
            #: Temporal's implementation lives in the Rust temporal_capi/temporal_rs
            #: rlibs; lld-link consumes their LLVM bitcode directly.  V8's own
            #: mksnapshot link line states the exact set and order, including the
            #: allocator shim rlibs that no module-level scan would find.
            mksnapshot = config.output_dir / "obj" / "mksnapshot.ninja"
            rust_libraries = []
            extra_builtins = []
            if mksnapshot.is_file():
                text = mksnapshot.read_text(encoding="utf-8", errors="replace")
                line = next(line for line in text.splitlines()
                            if line.startswith("build ./mksnapshot.exe"))
                for token in line.split():
                    if token.endswith(".rlib"):
                        path = config.output_dir / token
                        if path.is_file():
                            rust_libraries.append(path)
                    elif token.endswith(".lib") and "clang_rt.builtins" in token:
                        path = (config.output_dir / token).resolve()
                        if path.is_file():
                            extra_builtins.append(path)
            seen = set()
            rust_libraries = [path for path in rust_libraries
                              if not (str(path) in seen or seen.add(str(path)))]
            extension.extra_link_args = (
                [str(path) for path in rust_libraries]
                + [str(path) for path in extra_builtins]
                + list(extension.extra_link_args or [])
            )

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
        source_hashes = {
            path.relative_to(PROJECT_ROOT).as_posix(): file_sha256(path)
            for path in sorted((PROJECT_ROOT / "src").rglob("*"))
            if path.suffix in {".cpp", ".h", ".def"}
        }
        for name in ("setup.py", "pyproject.toml", "v8_build.py"):
            source_hashes[name] = file_sha256(PROJECT_ROOT / name)
        return {
            'bridge_sources_sha256': hashlib.sha256(json.dumps(source_hashes, sort_keys=True).encode()).hexdigest(),
            'python_abi': sysconfig.get_config_var('SOABI'),
            'compile_args': extra_compile_args,
            'link_args': extra_link_args,
            'toolchain_environment': {name: os.environ.get(name) for name in
                                     ('CC', 'CXX', 'CFLAGS', 'CPPFLAGS', 'LDFLAGS',
                                      'VSINSTALLDIR', 'VCToolsInstallDir', 'WindowsSDKVersion',
                                      'DISTUTILS_USE_SDK', 'PYV8_VS_HOME')},
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
        fingerprint['toolchain'] = self._toolchain_identity()
        fingerprint_file = PROJECT_ROOT / 'build' / 'pyv8-extension.json'
        previous = None
        if fingerprint_file.is_file():
            try:
                recorded = json.loads(fingerprint_file.read_text(encoding='utf-8'))
                previous = recorded.get('inputs', recorded)
            except (OSError, ValueError):
                pass
        if previous != fingerprint:
            self.force = True
        return fingerprint_file, fingerprint

    def _toolchain_identity(self):
        compiler = self.compiler
        if getattr(compiler, 'compiler_type', None) == 'msvc' and not compiler.initialized:
            compiler.initialize()
        identity = {}
        for name in ('compiler_type', 'cc', 'linker', 'compiler', 'compiler_so',
                     'compiler_cxx', 'linker_so', 'include_dirs', 'library_dirs',
                     'compile_options', 'compile_options_debug'):
            value = getattr(compiler, name, None)
            identity[name] = list(value) if isinstance(value, tuple) else value
        executables = {}
        for name in ('cc', 'linker', 'compiler', 'compiler_so', 'compiler_cxx', 'linker_so'):
            value = identity[name]
            command = value[0] if isinstance(value, list) and value else value
            if not isinstance(command, str):
                continue
            resolved = shutil.which(command) or command
            path = Path(resolved)
            if path.is_file():
                executables[name] = {'path': str(path.resolve()), 'sha256': file_sha256(path)}
        identity['executables'] = executables
        return identity

    def build_extensions(self):
        # build_ext has selected/customized the actual compiler by this point.
        if hasattr(self.compiler, "initialize") and not getattr(self.compiler, "initialized", False):
            self.compiler.initialize()
        v8_bin = (self._v8_config.v8_home / "third_party" / "llvm-build"
                  / "Release+Asserts" / "bin")
        clang_cl = v8_bin / "clang-cl.exe"
        lld_link = v8_bin / "lld-link.exe"
        if clang_cl.is_file() and lld_link.is_file():
            #: Temporal's Rust rlibs are LLVM bitcode: link.exe cannot read them,
            #: lld-link can.  clang-cl's /GL keeps the extension objects in the
            #: same bitcode form, so one toolchain links the whole extension.
            self.compiler.cc = str(clang_cl)
            self.compiler.compiler = [str(clang_cl)]
            self.compiler.compiler_so = [str(clang_cl)]
            self.compiler.compiler_cxx = [str(clang_cl)]
            self.compiler.linker = str(lld_link)
            self.compiler.linker_so = [str(lld_link)]
            #: lld-link has no manifest step and treats /LTCG itself.
            ldflags = getattr(self.compiler, "_ldflags", None)
            if isinstance(ldflags, dict):
                for key, flags in list(ldflags.items()):
                    ldflags[key] = [
                        flag for flag in flags
                        if not flag.upper().startswith(("/MANIFEST", "/LTCG"))
                    ]
        # MSVC still needs initialization to resolve its automatically chosen tools.
        self._fingerprint_file, self._extension_fingerprint = self._require_rebuild_for_changed_v8(self._v8_config)
        super().build_extensions()

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
            self._v8_config = config
            super().run()
            self._copy_icu_data(config)
            fingerprint_file, fingerprint = self._fingerprint_file, self._extension_fingerprint
            fingerprint_file.parent.mkdir(parents=True, exist_ok=True)
            binary = Path(self.get_ext_fullpath('pyv8._pyv8'))
            record = {'inputs': fingerprint,
                      'binary_sha256': file_sha256(binary) if binary.is_file() else None,
                      'compiler': {name: getattr(self.compiler, name, None)
                                   for name in ('compiler_type', 'cc', 'linker')}}
            if fingerprint_file.is_file():
                try:
                    prior = json.loads(fingerprint_file.read_text(encoding='utf-8'))
                    if prior.get('binary_sha256') == record['binary_sha256']:
                        record['compiler'] = prior.get('compiler', record['compiler'])
                except (OSError, ValueError):
                    pass
            fingerprint_file.write_text(json.dumps(record, indent=2, sort_keys=True) + '\n', encoding='utf-8')
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
        depends=sorted(str(path.relative_to(PROJECT_ROOT)).replace(os.sep, "/")
                       for path in (PROJECT_ROOT / "src").rglob("*")
                       if path.suffix in {".h", ".def"}),
        # Every source keeps including its neighbours by bare file name, so the
        # layout groups are added as include roots rather than written into the
        # includes themselves.
        include_dirs=[
            'src',
            'src/isolate',
            'src/values',
            'src/handles',
            'src/tools',
            'src/interfaces',
        ],
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
