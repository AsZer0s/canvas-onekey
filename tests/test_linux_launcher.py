"""Linux 启动器回归测试，不下载 ComfyUI / 模型，不修改系统依赖。"""
from __future__ import annotations

import importlib.util
import os
import signal
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path, PurePosixPath
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "launcher"))

import gpu


class GpuTests(unittest.TestCase):
    def test_linux_memory(self):
        with patch.object(gpu.os, "sysconf", create=True, side_effect=[8388608, 4096]):
            self.assertEqual(gpu.ram_gb(), 32.0)

    def test_missing_nvidia_smi(self):
        with patch.object(gpu.shutil, "which", return_value=None):
            cards, error = gpu.nvidia_gpus()
        self.assertEqual(cards, [])
        self.assertIn("nvidia-smi", error)

    def test_linux_nvidia_smi(self):
        result = Mock(returncode=0, stdout="0, NVIDIA RTX 5090, 32607, 12.0, 580.95.05, 1024\n")
        with patch.object(gpu.shutil, "which", return_value="/usr/bin/nvidia-smi"), \
                patch.object(gpu.subprocess, "run", return_value=result) as run:
            cards, error = gpu.nvidia_gpus()
        self.assertEqual(error, "")
        self.assertEqual(cards[0]["cc"], 12.0)
        self.assertEqual(cards[0]["used_gb"], 1.0)
        self.assertNotIn("creationflags", run.call_args.kwargs)

    def test_network_mount_and_nested_local_mount(self):
        mounts = "root / ext4 rw 0 0\nserver:/data /mnt/data nfs4 rw 0 0\nlocal /mnt/data/local ext4 rw 0 0\n"
        with patch.object(Path, "read_text", return_value=mounts), \
                patch.object(Path, "resolve", return_value=PurePosixPath("/mnt/data/project")):
            self.assertTrue(gpu.is_network_path(Path(".")))
        with patch.object(Path, "read_text", return_value=mounts), \
                patch.object(Path, "resolve", return_value=PurePosixPath("/mnt/data/local/project")):
            self.assertFalse(gpu.is_network_path(Path(".")))

    def test_mount_escapes_and_path_boundary(self):
        mounts = "root / ext4 rw 0 0\nserver /mnt/my\\040data cifs rw 0 0\n"
        with patch.object(Path, "read_text", return_value=mounts), \
                patch.object(Path, "resolve", return_value=PurePosixPath("/mnt/my data/project")):
            self.assertTrue(gpu.is_network_path(Path(".")))
        with patch.object(Path, "read_text", return_value=mounts), \
                patch.object(Path, "resolve", return_value=PurePosixPath("/mnt/my database")):
            self.assertFalse(gpu.is_network_path(Path(".")))

    def test_cuda_driver_selection(self):
        self.assertEqual(gpu.torch_index("580.95.05")[0], "cu130")
        self.assertEqual(gpu.torch_index("570.124.06")[0], "cu128")
        self.assertIsNone(gpu.torch_index("550.120")[0])


class LauncherTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 在临时项目根目录加载启动器，避免测试创建真实 data/ 或覆盖已有设置。
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        (cls.root / "launcher").mkdir()
        spec = importlib.util.spec_from_file_location("paths", ROOT / "launcher" / "paths.py")
        module = importlib.util.module_from_spec(spec)
        module.__file__ = str(cls.root / "launcher" / "paths.py")
        sys.modules["paths"] = module
        spec.loader.exec_module(module)
        import installer
        import services
        import main
        cls.paths = module
        cls.installer = installer
        cls.services = services
        cls.main = main

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_system_interpreter(self):
        self.assertEqual(self.paths.COMFY_PY, Path(sys.executable))

    def test_pip_environment(self):
        with patch.object(self.paths, "load", return_value={"mirror": True}), \
                patch.dict(os.environ, {"PIP_BREAK_SYSTEM_PACKAGES": "1"}):
            env = self.paths.pip_env()
        self.assertEqual(env["PIP_INDEX_URL"], "https://pypi.tuna.tsinghua.edu.cn/simple")
        self.assertEqual(env["PIP_BREAK_SYSTEM_PACKAGES"], "1")
        self.assertEqual(env["PIP_CACHE_DIR"], str(self.paths.RUNTIME / "pip-cache"))
        with patch.object(self.paths, "load", return_value={}), \
                patch.dict(os.environ, {"PIP_INDEX_URL": "https://example.com/simple"}):
            self.assertEqual(self.paths.pip_env()["PIP_INDEX_URL"], "https://example.com/simple")

    def test_comfy_install_uses_system_pip(self):
        self.paths.COMFY_DIR.mkdir(exist_ok=True)
        (self.paths.COMFY_DIR / "main.py").touch()
        with patch.object(self.installer, "run") as run:
            self.installer.install_comfy("cu128")
        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(len(commands), 2)
        for cmd in commands:
            self.assertEqual(cmd[:4], [str(self.paths.COMFY_PY), "-m", "pip", "install"])
        self.assertIn("https://download.pytorch.org/whl/cu128", commands[0])
        self.assertIn("-r", commands[1])
        self.assertTrue(self.installer.comfy_ready())

    def test_old_environment_state_is_not_ready(self):
        with patch.object(self.paths, "load", return_value={"comfy": {"ok": True}}):
            self.assertFalse(self.installer.comfy_ready())

    def test_gguf_uses_system_pip(self):
        target = self.paths.COMFY_DIR / "custom_nodes" / "ComfyUI-GGUF"
        target.mkdir(parents=True, exist_ok=True)
        (target / "nodes.py").touch()
        with patch.object(self.installer, "run") as run:
            self.installer.ensure_gguf_node()
        self.assertEqual(run.call_args.args[0][:4], [str(self.paths.COMFY_PY), "-m", "pip", "install"])

    def test_spawn_isolates_process_group(self):
        with patch.object(self.services.subprocess, "Popen") as popen:
            self.services._spawn("test", [sys.executable, "-V"], self.root, {})
        self.assertTrue(popen.call_args.kwargs["start_new_session"])
        self.services.procs.pop("test")

    def test_stop_terminates_process_group(self):
        proc = Mock(pid=12345)
        self.services.procs["test"] = proc
        with patch.object(self.services.os, "killpg", create=True) as killpg, \
                patch.object(self.services.signal, "SIGKILL", 9, create=True):
            self.services.stop("test")
        self.assertEqual(killpg.call_args_list[0].args, (12345, signal.SIGTERM))
        self.assertNotIn("test", self.services.procs)
        proc.wait.assert_any_call(timeout=10)

    def test_stop_force_kills_after_timeout(self):
        proc = Mock(pid=12345)
        proc.wait.side_effect = [subprocess.TimeoutExpired("test", 10), 0]
        self.services.procs["test"] = proc
        with patch.object(self.services.os, "killpg", create=True) as killpg, \
                patch.object(self.services.signal, "SIGKILL", 9, create=True):
            self.services.stop("test")
        self.assertEqual(killpg.call_args_list[-1].args, (12345, 9))

    def test_stop_handles_already_exited_group(self):
        proc = Mock(pid=12345)
        self.services.procs["test"] = proc
        with patch.object(self.services.os, "killpg", create=True, side_effect=ProcessLookupError):
            self.services.stop("test")
        proc.wait.assert_called_once_with()

    @unittest.skipUnless(sys.platform.startswith("linux"), "需要 Linux 进程组支持")
    def test_real_linux_process_start_and_stop(self):
        try:
            self.services._spawn("integration", [sys.executable, "-c", "import time; time.sleep(60)"], self.root, {})
            proc = self.services.procs["integration"]
            self.assertEqual(os.getpgid(proc.pid), proc.pid)
            self.services.stop("integration")
            self.assertIsNotNone(proc.poll())
        finally:
            self.services.stop("integration")

    def test_browser_is_opt_in(self):
        self.assertFalse(self.main.parse_args([]).browser)
        self.assertTrue(self.main.parse_args(["--browser"]).browser)

    def test_default_still_starts_installed_services(self):
        with patch.object(self.main, "system_check", return_value={"gpu": {"index": 0}, "ram_gb": 64}), \
                patch.object(self.paths, "load", return_value={"packs": ["qwen"]}), \
                patch.object(self.installer, "comfy_ready", return_value=True), \
                patch.object(self.installer, "write_installed"), \
                patch.object(self.services, "start_all") as start, \
                patch.object(self.services, "health", return_value={"canvas": {"ok": True}}), \
                patch.object(self.main.webbrowser, "open") as browser:
            self.main._auto_start()
        start.assert_called_once_with(0)
        browser.assert_not_called()

    def test_browser_option_opens_setup_for_new_install(self):
        with patch.object(self.main, "system_check", return_value={}), \
                patch.object(self.paths, "load", return_value={}), \
                patch.object(self.installer, "comfy_ready", return_value=False), \
                patch.object(self.main.webbrowser, "open") as browser:
            self.main._auto_start(open_browser=True)
        browser.assert_called_once_with(f"http://127.0.0.1:{self.paths.PORTS['setup']}/")

    def test_shutdown_stops_services(self):
        from fastapi.testclient import TestClient
        with patch.object(self.services, "stop_all") as stop:
            with TestClient(self.main.app):
                pass
        stop.assert_called_once_with()

    def test_api_smoke_without_gpu(self):
        from fastapi.testclient import TestClient
        with patch.object(gpu, "nvidia_gpus", return_value=([], "未安装驱动")), \
                patch.object(gpu, "ram_gb", return_value=32), \
                patch.object(gpu, "is_network_path", return_value=False):
            self.main._check.clear()
            with TestClient(self.main.app) as client:
                self.assertEqual(client.get("/").status_code, 200)
                check = client.get("/api/check").json()
                self.assertIsNone(check["gpu"])
                self.assertEqual(check["ram_gb"], 32)
                self.assertEqual(client.post("/api/install", json={"packs": []}).status_code, 400)


if __name__ == "__main__":
    unittest.main()
