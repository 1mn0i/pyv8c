import importlib.util
import json
import platform
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


def _load_v8_build_module():
    module_path = Path(__file__).resolve().parents[1] / "v8_build.py"
    spec = importlib.util.spec_from_file_location("pyv8_v8_build", module_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


v8_build = _load_v8_build_module()


class TestV8BuildConfiguration(unittest.TestCase):
    """测试 V8 版本选择、产物校验和缓存复用。"""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        (self.root / "V8_VERSION").write_text("12.5.99\n", encoding="ascii")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_file_sha256_reads_build_artifacts_in_chunks(self):
        """构建产物指纹应稳定，并支持较大的 ICU 数据文件。"""

        artifact = self.root / "artifact.bin"
        artifact.write_bytes(b"pyv8" * 1024)

        self.assertEqual(
            v8_build.file_sha256(artifact),
            "08e1ed84046e5749ce4d469e86d2da84056dc7f92532087f5febe1c536c3440f",
        )

    @staticmethod
    def _write_headers(v8_home, version):
        parts = [int(part) for part in version.split(".")]
        if len(parts) == 3:
            parts.append(0)
        include_dir = v8_home / "include"
        include_dir.mkdir(parents=True, exist_ok=True)
        (include_dir / "v8.h").write_text("// test V8 header\n", encoding="ascii")
        (include_dir / "v8-version.h").write_text(
            "\n".join(
                [
                    f"#define V8_MAJOR_VERSION {parts[0]}",
                    f"#define V8_MINOR_VERSION {parts[1]}",
                    f"#define V8_BUILD_NUMBER {parts[2]}",
                    f"#define V8_PATCH_LEVEL {parts[3]}",
                ]
            )
            + "\n",
            encoding="ascii",
        )

    @staticmethod
    def _write_build_artifacts(config):
        config.library_dir.mkdir(parents=True, exist_ok=True)
        config.library_file.write_bytes(b"test library")
        config.output_dir.mkdir(parents=True, exist_ok=True)
        (config.output_dir / "icudtl.dat").write_bytes(b"test ICU data")

    def test_default_configuration_reuses_bundled_v8(self):
        """未指定版本时应继续使用仓库中已有的 V8。"""

        bundled_home = self.root / "v8"
        self._write_headers(bundled_home, "12.5.99")
        config = v8_build.V8BuildConfig.from_environment(self.root, {})
        self._write_build_artifacts(config)

        config.prepare()

        self.assertEqual(config.version, "12.5.99")
        self.assertEqual(config.v8_home, bundled_home.resolve())
        self.assertFalse(config.download_and_build)

    def test_selected_version_uses_isolated_managed_checkout(self):
        """显式下载其他版本时不得覆盖仓库自带的 V8。"""

        bundled_home = self.root / "v8"
        self._write_headers(bundled_home, "12.5.99")
        config = v8_build.V8BuildConfig.from_environment(
            self.root,
            {
                "PYV8_V8_VERSION": "13.1.201.22",
                "PYV8_BUILD_V8": "1",
                "PYV8_V8_JOBS": "3",
            },
        )

        self.assertEqual(config.version, "13.1.201.22")
        self.assertEqual(
            config.v8_home,
            (self.root / ".pyv8-build" / "versions" / "13.1.201.22" / "v8").resolve(),
        )
        self.assertTrue(config.download_and_build)
        self.assertEqual(config.jobs, 3)

    def test_local_selection_is_used_by_later_builds(self):
        """交互选择应保存到本地文件并被后续 wheel 构建自动读取。"""

        selected_file = v8_build.save_selected_v8_version(
            self.root, "13.1.201.22"
        )
        config = v8_build.V8BuildConfig.from_environment(self.root, {})

        self.assertEqual(selected_file, self.root / ".pyv8-version")
        self.assertEqual(config.version, "13.1.201.22")
        self.assertEqual(
            config.v8_home,
            (self.root / ".pyv8-build" / "versions" / "13.1.201.22" / "v8").resolve(),
        )

    def test_cached_managed_build_skips_download(self):
        """版本、架构和 GN 参数一致时应直接复用缓存产物。"""

        config = v8_build.V8BuildConfig.from_environment(
            self.root,
            {
                "PYV8_V8_VERSION": "13.1.201.22",
                "PYV8_BUILD_V8": "1",
            },
        )
        self._write_headers(config.v8_home, config.version)
        self._write_build_artifacts(config)
        config.stamp_file.write_text(
            json.dumps(config._stamp_data(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        config.prepare()

        self.assertEqual(v8_build.read_v8_header_version(config.v8_home), config.version)

    def test_mismatched_external_v8_is_rejected(self):
        """外部 V8 的头文件版本必须与打包时选择的版本一致。"""

        external_home = self.root / "external" / "v8"
        self._write_headers(external_home, "12.5.99")
        config = v8_build.V8BuildConfig.from_environment(
            self.root,
            {"PYV8_V8_VERSION": "13.1.201.22"},
            v8_home=external_home,
        )
        self._write_build_artifacts(config)

        with self.assertRaisesRegex(v8_build.V8BuildError, "contains 12.5.99"):
            config.validate()

    def test_invalid_version_is_rejected_before_running_git(self):
        """只允许官方数字版本 tag，不能把任意 Git ref 传入构建流程。"""

        with self.assertRaisesRegex(v8_build.V8BuildError, "invalid V8 version"):
            v8_build.V8BuildConfig.from_environment(
                self.root, {"PYV8_V8_VERSION": "../../main"}
            )

    def test_remote_tags_are_grouped_into_readable_release_choices(self):
        """版本列表应按发布分支收敛，而不是直接打印数千个 Git tag。"""

        refs = "\n".join(
            [
                "a refs/tags/15.0.2",
                "b refs/tags/15.0.2.1",
                "c refs/tags/15.0.2.2",
                "d refs/tags/14.9.3",
                "e refs/tags/12.5.99",
                "f refs/tags/not-a-version",
            ]
        )
        versions = v8_build.parse_v8_version_refs(refs)
        choices = v8_build.recommended_v8_versions(
            versions, "12.5.99", limit=3
        )

        self.assertEqual(
            versions,
            ["15.0.2.2", "15.0.2.1", "15.0.2", "14.9.3", "12.5.99"],
        )
        self.assertEqual(choices, ["12.5.99", "15.0.2.2", "14.9.3"])

    def test_interactive_selection_accepts_number_or_exact_known_tag(self):
        """交互菜单应支持编号，并允许输入列表外的已知精确 tag。"""

        versions = ["15.0.2.2", "14.9.3.1", "13.1.201.22", "12.5.99"]
        answers = iter(["99", "13.1.201.22"])
        output = []

        selected = v8_build.select_v8_version(
            versions,
            "12.5.99",
            limit=3,
            input_func=lambda prompt: next(answers),
            output=output.append,
        )

        self.assertEqual(selected, "13.1.201.22")
        self.assertTrue(any("Invalid selection" in line for line in output))

    def test_gn_configuration_builds_compatible_monolith(self):
        """GN 参数应生成无 pointer compression 和 sandbox 的 release monolith。"""

        config = v8_build.V8BuildConfig.from_environment(
            self.root,
            {
                "PYV8_V8_VERSION": "13.1.201.22",
                "PYV8_BUILD_V8": "1",
            },
        )

        self.assertIn("v8_monolithic=true", config.gn_args)
        self.assertIn("v8_enable_pointer_compression=false", config.gn_args)
        self.assertIn("v8_enable_sandbox=false", config.gn_args)
        self.assertIn("v8_enable_temporal_support=true", config.gn_args)
        expected_cpu = "arm64" if platform.machine().lower() in {"arm64", "aarch64"} else "x64"
        self.assertIn(f'target_cpu="{expected_cpu}"', config.gn_args)

    def test_explicit_proxy_is_forwarded_to_depot_tools_and_cipd(self):
        """CIPD 应使用与 Git 相同的代理，不能在 Git clone 后改为直连。"""

        config = v8_build.V8BuildConfig.from_environment(self.root, {})
        with patch.dict(
            v8_build.os.environ,
            {"PYV8_PROXY": "http://127.0.0.1:7890"},
            clear=True,
        ), patch.object(config, "_windows_git_cmd_dir", return_value=None):
            environment = config._command_environment()

        self.assertEqual(environment["HTTP_PROXY"], "http://127.0.0.1:7890")
        self.assertEqual(environment["HTTPS_PROXY"], "http://127.0.0.1:7890")
        if v8_build.os.name == "nt":
            self.assertEqual(environment["DEPOT_TOOLS_UPDATE"], "0")

    def test_no_proxy_mode_clears_environment_and_git_proxy(self):
        """直连模式必须同时绕过环境变量和 Git 全局代理。"""

        config = v8_build.V8BuildConfig.from_environment(
            self.root,
            {"PYV8_NO_PROXY": "1"},
        )
        with patch.dict(
            v8_build.os.environ,
            {
                "HTTP_PROXY": "http://environment.invalid",
                "https_proxy": "http://environment.invalid",
                "ALL_PROXY": "socks5://environment.invalid",
                "PYV8_PROXY": "http://explicit.invalid",
            },
            clear=True,
        ), patch.object(
            config, "_windows_git_cmd_dir", return_value=None
        ), patch.object(
            v8_build, "_git_proxy", side_effect=AssertionError("must not query Git proxy")
        ):
            environment = config._command_environment()

        self.assertNotIn("HTTP_PROXY", environment)
        self.assertNotIn("HTTPS_PROXY", environment)
        self.assertNotIn("ALL_PROXY", environment)
        self.assertNotIn("PYV8_PROXY", environment)
        self.assertEqual(environment["NO_PROXY"], "*")
        self.assertEqual(environment["no_proxy"], "*")
        self.assertEqual(environment["GIT_CONFIG_COUNT"], "2")
        self.assertEqual(environment["GIT_CONFIG_KEY_0"], "http.proxy")
        self.assertEqual(environment["GIT_CONFIG_VALUE_0"], "")
        self.assertEqual(environment["GIT_CONFIG_KEY_1"], "https.proxy")
        self.assertEqual(environment["GIT_CONFIG_VALUE_1"], "")

    def test_proxy_and_no_proxy_options_are_mutually_exclusive(self):
        """同一命令不能既指定代理又要求强制直连。"""

        with self.assertRaisesRegex(v8_build.V8BuildError, "cannot be enabled together"):
            v8_build.V8BuildConfig.from_environment(
                self.root,
                {},
                proxy="http://127.0.0.1:7890",
                no_proxy=True,
            )

    def test_git_proxy_is_forwarded_when_proxy_environment_is_missing(self):
        """只配置了 git proxy 时也应让 CIPD 自动继承。"""

        config = v8_build.V8BuildConfig.from_environment(self.root, {})
        completed = v8_build.subprocess.CompletedProcess(
            ["git", "config"], 0, stdout="http://127.0.0.1:7890\n", stderr=""
        )
        with patch.dict(v8_build.os.environ, {}, clear=True), patch.object(
            v8_build.subprocess, "run", return_value=completed
        ):
            environment = config._command_environment()

        self.assertEqual(environment["HTTP_PROXY"], "http://127.0.0.1:7890")
        self.assertEqual(environment["HTTPS_PROXY"], "http://127.0.0.1:7890")

    @unittest.skipUnless(v8_build.os.name == "nt", "Windows depot_tools behavior")
    def test_real_git_directory_is_added_for_depot_tools(self):
        """Windows 下应绕过 Scoop shim，让 depot_tools 能生成 git.bat。"""

        config = v8_build.V8BuildConfig.from_environment(self.root, {})
        git_cmd_dir = self.root / "git" / "cmd"
        with patch.dict(
            v8_build.os.environ,
            {"PATH": "original-path", "HTTP_PROXY": "http://proxy.invalid"},
            clear=True,
        ), patch.object(
            config, "_windows_git_cmd_dir", return_value=git_cmd_dir
        ):
            environment = config._command_environment()

        path_entries = environment["PATH"].split(v8_build.os.pathsep)
        self.assertEqual(path_entries[0], str(config.depot_tools_home))
        self.assertEqual(path_entries[1], str(git_cmd_dir))
        self.assertEqual(path_entries[2], "original-path")

    @unittest.skipUnless(v8_build.os.name == "nt", "Windows depot_tools behavior")
    def test_standard_git_layout_needs_no_bootstrap_junction(self):
        """标准 Git/cmd 布局应直接交给 depot_tools，不创建额外目录联接。"""

        config = v8_build.V8BuildConfig.from_environment(self.root, {})
        git_cmd_dir = self.root / "Git" / "cmd"
        git_cmd_dir.mkdir(parents=True)
        (git_cmd_dir / "git.exe").write_bytes(b"")

        self.assertEqual(
            config._windows_bootstrap_git_cmd_dir(git_cmd_dir), git_cmd_dir
        )
        self.assertFalse((config.build_root / "bootstrap-tools").exists())

    @unittest.skipUnless(v8_build.os.name == "nt", "Windows V8 toolchain behavior")
    def test_vswhere_installation_is_exposed_to_v8(self):
        """非默认目录中的 Visual Studio 应通过 vs2022_install 传给 V8。"""

        visual_studio = self.root / "Visual Studio" / "2022" / "Community"
        visual_studio.mkdir(parents=True)
        vswhere_result = v8_build.subprocess.CompletedProcess(
            ["vswhere"],
            0,
            stdout=json.dumps(
                [
                    {
                        "installationPath": str(visual_studio),
                        "installationVersion": "17.14.0",
                    }
                ]
            ),
            stderr="",
        )
        environment = {"PATH": ""}
        with patch.object(v8_build.shutil, "which", return_value="vswhere.exe"), patch.object(
            v8_build.subprocess, "run", return_value=vswhere_result
        ):
            v8_build.V8BuildConfig._configure_visual_studio_environment(environment)

        self.assertEqual(environment["vs2022_install"], str(visual_studio))
        self.assertEqual(environment["GYP_MSVS_OVERRIDE_PATH"], str(visual_studio))

    @unittest.skipUnless(v8_build.os.name == "nt", "Windows V8 toolchain behavior")
    def test_explicit_windows_sdk_is_exposed_to_v8(self):
        """非默认盘的 Windows SDK 应通过 WINDOWSSDKDIR 传给 GN。"""

        sdk_path = self.root / "Windows Kits" / "10"
        sdk_path.mkdir(parents=True)
        environment = {"PYV8_WINDOWS_SDK": str(sdk_path)}

        v8_build.V8BuildConfig._configure_windows_sdk_environment(environment)

        self.assertEqual(Path(environment["WINDOWSSDKDIR"]), sdk_path.resolve())

    def test_version_query_uses_explicit_proxy(self):
        """交互菜单查询 tag 时也必须使用 PYV8_PROXY，不能只代理后续下载。"""

        completed = v8_build.subprocess.CompletedProcess(
            ["git", "ls-remote"],
            0,
            stdout="a refs/tags/15.2.20.1\n",
            stderr="",
        )
        with patch.dict(
            v8_build.os.environ,
            {"PYV8_PROXY": "http://127.0.0.1:7890"},
            clear=True,
        ), patch.object(
            v8_build.subprocess, "run", return_value=completed
        ) as run_command:
            versions = v8_build.fetch_v8_versions()

        environment = run_command.call_args.kwargs["env"]
        self.assertEqual(versions, ["15.2.20.1"])
        self.assertEqual(environment["HTTP_PROXY"], "http://127.0.0.1:7890")
        self.assertEqual(environment["HTTPS_PROXY"], "http://127.0.0.1:7890")

    def test_version_query_can_force_direct_connection(self):
        """版本查询的直连参数必须覆盖当前进程中的代理设置。"""

        completed = v8_build.subprocess.CompletedProcess(
            ["git", "ls-remote"],
            0,
            stdout="a refs/tags/15.2.20.1\n",
            stderr="",
        )
        with patch.dict(
            v8_build.os.environ,
            {"HTTPS_PROXY": "http://environment.invalid"},
            clear=True,
        ), patch.object(
            v8_build.subprocess, "run", return_value=completed
        ) as run_command:
            versions = v8_build.fetch_v8_versions(no_proxy=True)

        environment = run_command.call_args.kwargs["env"]
        self.assertEqual(versions, ["15.2.20.1"])
        self.assertNotIn("HTTPS_PROXY", environment)
        self.assertEqual(environment["NO_PROXY"], "*")
        self.assertEqual(environment["GIT_CONFIG_VALUE_0"], "")
        self.assertEqual(environment["GIT_CONFIG_VALUE_1"], "")

    def test_partial_fetch_workspace_becomes_resumable_git_checkout(self):
        """只有 .gclient 的中断状态应转成可重复 fetch 的本地 Git 仓库。"""

        config = v8_build.V8BuildConfig.from_environment(
            self.root,
            {
                "PYV8_V8_VERSION": "15.2.20.1",
                "PYV8_BUILD_V8": "1",
            },
        )
        workspace = config.v8_home.parent
        workspace.mkdir(parents=True)
        gclient_file = config._ensure_gclient_config(workspace)

        with patch.object(config, "_run") as run_command:
            config._initialize_v8_checkout(workspace)

        commands = [call.args[0] for call in run_command.call_args_list]
        self.assertTrue(gclient_file.is_file())
        self.assertTrue(config.v8_home.is_dir())
        self.assertEqual(commands[0], ["git", "init"])
        self.assertEqual(
            commands[1],
            ["git", "remote", "add", "origin", v8_build.V8_GIT_URL],
        )

    def test_unrelated_gclient_workspace_is_rejected(self):
        """不能把其他 Chromium checkout 的 .gclient 当作 V8 workspace 使用。"""

        config = v8_build.V8BuildConfig.from_environment(
            self.root,
            {
                "PYV8_V8_VERSION": "15.2.20.1",
                "PYV8_BUILD_V8": "1",
            },
        )
        workspace = config.v8_home.parent
        workspace.mkdir(parents=True)
        (workspace / ".gclient").write_text(
            'solutions = [{"name": "chromium", "url": "https://example.invalid"}]\n',
            encoding="utf-8",
        )

        with self.assertRaisesRegex(v8_build.V8BuildError, "does not describe"):
            config._ensure_gclient_config(workspace)

    def test_gclient_sync_is_pinned_to_selected_tag(self):
        """同步依赖时必须锁定所选 tag，不能让 gclient 切回 V8 主分支。"""

        config = v8_build.V8BuildConfig.from_environment(
            self.root,
            {
                "PYV8_V8_VERSION": "15.2.20.1",
                "PYV8_BUILD_V8": "1",
                "PYV8_V8_JOBS": "6",
            },
        )
        with patch.object(
            config, "_depot_command", return_value=["gclient", "sync"]
        ) as depot_command:
            command = config._gclient_sync_command()

        self.assertEqual(command, ["gclient", "sync"])
        depot_command.assert_called_once_with(
            "gclient",
            "sync",
            "-D",
            "--no-history",
            "--revision",
            "v8@refs/tags/15.2.20.1",
            "-j6",
        )

    def test_root_fetch_refspec_only_tracks_selected_tag(self):
        """恢复出的根仓库只能抓取所选 tag，不能让 gclient 下载所有分支。"""

        config = v8_build.V8BuildConfig.from_environment(
            self.root,
            {
                "PYV8_V8_VERSION": "15.2.20.1",
                "PYV8_BUILD_V8": "1",
            },
        )
        with patch.object(config, "_run") as run_command:
            config._configure_v8_fetch_refspec()

        run_command.assert_called_once_with(
            [
                "git",
                "config",
                "--replace-all",
                "remote.origin.fetch",
                "+refs/tags/15.2.20.1:refs/tags/15.2.20.1",
            ],
            cwd=config.v8_home,
        )

    def test_gn_python_command_preserves_gn_args_as_one_argument(self):
        """GN 参数在 Windows 下不能经过 bat 二次拆分。"""

        config = v8_build.V8BuildConfig.from_environment(self.root, {})
        config.depot_tools_home.mkdir(parents=True)
        (config.depot_tools_home / "gn.py").write_text("", encoding="ascii")
        gn_argument = 'target_cpu="x64" v8_monolithic=true'

        command = config._depot_python_command(
            "gn", "gen", "out/pyv8-x64.release", f"--args={gn_argument}"
        )

        self.assertEqual(command[0], v8_build.sys.executable)
        self.assertEqual(command[-1], f"--args={gn_argument}")
        self.assertEqual(len(command), 5)

    def test_v8_source_compatibility_patches_are_temporary(self):
        """MSVC 兼容补丁只在构建期间生效，结束后必须恢复 V8 checkout。"""

        config = v8_build.V8BuildConfig.from_environment(
            self.root,
            {
                "PYV8_V8_VERSION": "15.2.20.1",
                "PYV8_BUILD_V8": "1",
            },
        )
        source = config.v8_home / "src" / "objects" / "backing-store.cc"
        source.parent.mkdir(parents=True)
        original = (
            b"return isolate->heap()->allocator()->RetryCustomAllocate(\n"
            b"        fn, internal::AllocationType::kOld,\n"
        )
        source.write_bytes(original)

        atomics_header = (
            config.v8_home / "src" / "objects" / "js-atomics-synchronization.h"
        )
        atomics_original = (
            b"  ExternalPointerMember<kWaiterQueueNodeTag> waiter_queue_head_;\n"
            b"  std::atomic<uint32_t> state_;\n"
        )
        atomics_header.write_bytes(atomics_original)

        extended_map_sources = {
            config.v8_home / "src" / "objects" / "map.h": (
                b"  std::atomic<uint8_t> bit_field_ex_;\n"
                b"  // Leaves kTaggedSize-1 unused bytes, they will be used by subclasses.\n"
            ),
            config.v8_home / "src" / "objects" / "map.tq": (
                b"  bit_field_ex: ExtendedMapBitFields;\n"
                b"  // Leaves kTaggedSize-1 unused bytes, they will be used by subclasses.\n"
            ),
            config.v8_home / "src" / "objects" / "js-interceptor-map.h": (
                b"  uint8_t extended_padding_[kTaggedSize - 1];\n"
            ),
            config.v8_home / "src" / "objects" / "js-interceptor-map.tq": (
                b"  @ifnot(TAGGED_SIZE_8_BYTES) extended_padding[3]: uint8;\n"
                b"  @if(TAGGED_SIZE_8_BYTES) extended_padding[7]: uint8;\n"
            ),
            config.v8_home / "src" / "objects" / "js-interceptor-map-inl.h": (
                b"void JSInterceptorMap::clear_extended_padding() {\n"
                b"  memset(extended_padding_, 0, sizeof(extended_padding_));\n"
                b"}\n"
            ),
        }
        for path, content in extended_map_sources.items():
            path.write_bytes(content)

        with config._source_compatibility_patches():
            patched = source.read_bytes()
            self.assertIn(b"[&fn]() { return fn(); }", patched)
            self.assertIn(
                b"uint32_t no_pointer_compression_padding_;",
                atomics_header.read_bytes(),
            )
            self.assertIn(
                b"uint8_t extended_map_padding_[3];",
                (config.v8_home / "src" / "objects" / "map.h").read_bytes(),
            )
            self.assertIn(
                b"extended_padding[4]: uint8;",
                (
                    config.v8_home
                    / "src"
                    / "objects"
                    / "js-interceptor-map.tq"
                ).read_bytes(),
            )

        self.assertEqual(source.read_bytes(), original)
        self.assertEqual(atomics_header.read_bytes(), atomics_original)
        for path, content in extended_map_sources.items():
            self.assertEqual(path.read_bytes(), content)


    def test_new_extended_map_layout_patch_is_temporary(self):
        config = v8_build.V8BuildConfig.from_environment(
            self.root, {"PYV8_V8_VERSION": "15.4.68", "PYV8_BUILD_V8": "1"})
        root = config.v8_home / "src" / "objects"
        root.mkdir(parents=True)
        sources = {
            "map.h": b"  std::atomic<uint8_t> bit_field_ex_;\n  // Leaves kTaggedSize-1 unused bytes, they will be used by subclasses.\n",
            "map.tq": b"  bit_field_ex: ExtendedMapBitFields;\n  // Leaves kTaggedSize-1 unused bytes, they will be used by subclasses.\n",
            "js-interceptor-map.h": b"  uint8_t flags_;\n  uint8_t extended_padding_[kTaggedSize - 2];\n",
            "js-interceptor-map.tq": b"  @ifnot(TAGGED_SIZE_8_BYTES) extended_padding[2]: uint8;\n  @if(TAGGED_SIZE_8_BYTES) extended_padding[6]: uint8;\n",
            "js-interceptor-map-inl.h": b"void JSInterceptorMap::init_flags_and_clear_extended_padding() {\n  flags_ = 0;\n  memset(extended_padding_, 0, sizeof(extended_padding_));\n}\n",
        }
        for name, contents in sources.items():
            (root / name).write_bytes(contents)
        with config._source_compatibility_patches():
            self.assertIn(b"extended_map_padding_[3]", (root / "map.h").read_bytes())
            self.assertIn(b"extended_padding_[3]", (root / "js-interceptor-map.h").read_bytes())
            self.assertIn(b"extended_padding[3]", (root / "js-interceptor-map.tq").read_bytes())
            self.assertIn(b"memset(extended_map_padding_", (root / "js-interceptor-map-inl.h").read_bytes())
        for name, contents in sources.items():
            self.assertEqual((root / name).read_bytes(), contents)


if __name__ == "__main__":
    unittest.main()
