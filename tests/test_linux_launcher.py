"""Linux 启动器回归测试，不下载 ComfyUI / 模型，不修改系统依赖。"""
from __future__ import annotations

import importlib.util
import json
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

    def setUp(self):
        import comfy_detect
        comfy_detect._cache.clear()
        self.addCleanup(comfy_detect._cache.clear)
        # 不探测测试宿主机真实的 ComfyUI 或其他本机服务。
        for name, value in (("probe", {}), ("process_installs", []), ("common_installs", []), ("port_open", False)):
            mocker = patch.object(comfy_detect, name, return_value=value)
            mocker.start()
            self.addCleanup(mocker.stop)

    def test_system_interpreter(self):
        self.assertEqual(self.paths.COMFY_PY, Path(sys.executable))

    def test_pack_view_marks_existing_models_without_install_state(self):
        gpu_info = {"cc": 12, "vram_gb": 24}
        with patch.object(self.main, "system_check", return_value={"gpu": gpu_info, "ram_gb": 64}), \
                patch.object(self.paths, "load", return_value={}), \
                patch.object(self.installer, "file_done", return_value=True):
            packs = self.main.pack_view()
        qwen = next(p for p in packs if p["id"] == "qwen")
        self.assertTrue(qwen["available"])
        self.assertFalse(qwen["installed"])
        self.assertEqual(qwen["present_count"], qwen["file_count"])
        self.assertEqual(qwen["missing"], {})

    def test_pack_view_marks_partial_and_registered_models(self):
        gpu_info = {"cc": 12, "vram_gb": 24}
        with patch.object(self.main, "system_check", return_value={"gpu": gpu_info, "ram_gb": 64}), \
                patch.object(self.paths, "load", return_value={"packs": ["qwen"]}), \
                patch.object(self.installer, "file_done", side_effect=lambda fid: fid != "qwen_vae"):
            qwen = next(p for p in self.main.pack_view() if p["id"] == "qwen")
        self.assertFalse(qwen["available"])
        self.assertFalse(qwen["installed"])
        self.assertEqual(qwen["present_count"], 2)
        self.assertEqual(list(qwen["missing"]), ["qwen_vae"])
        with patch.object(self.main, "system_check", return_value={"gpu": gpu_info, "ram_gb": 64}), \
                patch.object(self.paths, "load", return_value={"packs": ["qwen"]}), \
                patch.object(self.installer, "file_done", return_value=True):
            qwen = next(p for p in self.main.pack_view() if p["id"] == "qwen")
        self.assertTrue(qwen["available"])
        self.assertTrue(qwen["installed"])

    def test_empty_disabled_pack_is_not_marked_available(self):
        with patch.object(self.main, "system_check", return_value={"gpu": {"cc": 8.6, "vram_gb": 8}, "ram_gb": 32}), \
                patch.object(self.paths, "load", return_value={}):
            h3 = next(p for p in self.main.pack_view() if p["id"] == "h3_fl")
        self.assertFalse(h3["available"])
        self.assertEqual(h3["file_count"], 0)

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
        with patch.object(self.installer, "python_has", return_value=False), patch.object(self.installer, "run") as run:
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
        with patch.object(self.installer, "python_has", return_value=False), patch.object(self.installer, "run") as run:
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
        import comfy_detect
        with patch.object(comfy_detect, "select", return_value={"running": False, "directory_known": True}), \
                patch.object(comfy_detect, "audit", return_value={"running": False}), \
                patch.object(self.main, "system_check", return_value={"gpu": {"index": 0}, "ram_gb": 64}), \
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

    def test_comfy_url_rejects_remote_and_credentials(self):
        for url in ("https://127.0.0.1:8188", "http://example.com:8188", "http://user:pass@localhost:8188",
                    "http://127.0.0.1:8188/path", "http://127.0.0.1:8188?x=1"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                self.paths.validate_comfy_url(url)
        self.assertEqual(self.paths.validate_comfy_url("http://localhost:8190/"), "http://127.0.0.1:8190")

    def test_discover_running_instance_custom_port(self):
        import comfy_detect
        root = self.root / "existing-running"
        process = {"directory": str(root), "url": "http://127.0.0.1:8199", "pid": 123}
        with patch.object(comfy_detect, "process_installs", return_value=[process]), \
                patch.object(comfy_detect, "probe", side_effect=lambda url: {"nodes": {"KSampler": {}}} if url == process["url"] else {}), \
                patch.object(self.paths, "load", return_value={}):
            result = comfy_detect.discover(force=True)
        self.assertTrue(result["running"])
        self.assertEqual(result["directory"], str(root))
        self.assertEqual(result["url"], process["url"])

    def test_discover_stopped_existing_install(self):
        import comfy_detect
        root = self.root / "existing-stopped"
        with patch.object(comfy_detect, "common_installs", return_value=[root]), \
                patch.object(self.paths, "load", return_value={}):
            result = comfy_detect.discover(force=True)
        self.assertEqual(result["directory"], str(root))
        self.assertFalse(result["running"])

    def test_explicit_directory_mismatch(self):
        import comfy_detect
        root = self.root / "other-comfy"
        process = {"directory": str(root), "url": self.paths.comfy_url(), "pid": 123}
        with patch.object(comfy_detect, "process_installs", return_value=[process]), \
                patch.object(comfy_detect, "probe", return_value={"nodes": {"KSampler": {}}}), \
                patch.object(self.paths, "load", return_value={"comfy_dir": str(self.root / "chosen")}):
            self.assertTrue(comfy_detect.discover(force=True)["mismatch"])
            with self.assertRaises(ValueError):
                comfy_detect.select()

    def test_external_instance_does_not_install_pytorch(self):
        import comfy_detect
        with patch.object(comfy_detect, "discover", return_value={"running": True}), \
                patch.object(self.installer, "run") as run:
            self.installer.install_comfy("cu130")
        run.assert_not_called()

    def test_loaded_gguf_plugin_is_not_reinstalled(self):
        import comfy_detect
        with patch.object(comfy_detect, "discover", return_value={"runtime": {"nodes": {"UnetLoaderGGUF": {}}}}), \
                patch.object(self.installer, "run") as run:
            self.installer.ensure_gguf_node()
        run.assert_not_called()

    def test_install_packs_only_downloads_missing_models(self):
        gpu_info = {"cc": 12.0, "vram_gb": 24}
        with patch.object(self.installer, "file_done", side_effect=lambda fid: fid != "qwen_vae"), \
                patch.object(self.installer, "download") as download, \
                patch.object(self.installer, "expose_model") as expose, \
                patch.object(self.installer, "write_installed"), \
                patch.object(self.paths, "load", return_value={}), \
                patch.object(self.paths, "save"):
            self.installer.install_packs(["qwen"], gpu_info, 64, [])
        download.assert_called_once()
        self.assertIn("qwen_image_2.1_vae_bf16.safetensors", download.call_args.args[0])
        self.assertEqual(expose.call_count, 3)

    def test_stop_all_never_stops_unowned_comfy(self):
        self.assertNotIn("comfyui", self.services.procs)
        with patch.object(self.services, "stop") as stop:
            self.services.stop_all()
        stop.assert_not_called()

    def test_api_model_reuse_and_ambiguous_names(self):
        import comfy_detect
        import catalog
        filename = Path(catalog.dest_of("lora_canon")).name
        def nodes(names):
            return {"LoraLoaderModelOnly": {"input": {"required": {"lora_name": [names]}}}}
        self.assertEqual(comfy_detect.api_model("lora_canon", nodes(["custom/" + filename])), "custom/" + filename)
        self.assertIsNone(comfy_detect.api_model("lora_canon", nodes(["a/" + filename, "b/" + filename])))
        with patch.object(comfy_detect, "discover", return_value={"runtime": {"nodes": nodes(["custom/" + filename])}}):
            self.assertEqual(self.installer.model_name("lora_canon"), "custom/" + filename)
            self.assertTrue(self.installer.file_done("lora_canon"))

    def test_local_models_and_extra_model_paths_are_reused(self):
        import comfy_detect
        import catalog
        root = self.root / "with-models"
        root.mkdir(exist_ok=True)
        shared = self.root / "shared-models"
        shared.mkdir(exist_ok=True)
        model = shared / "canon_qwen21.safetensors"
        model.write_bytes(b"1234")
        (root / "extra_model_paths.yaml").write_text(
            "shared:\n  base_path: '" + shared.as_posix() + "'\n  loras: .\n", encoding="utf-8")
        fake = {**catalog.FILES["lora_canon"], "size": 4}
        with patch.dict(catalog.FILES, {"lora_canon": fake}):
            self.assertEqual(comfy_detect.local_model("lora_canon", root), model)

    def test_workflow_templates_cover_all_model_variants(self):
        import workflows
        for vram in (12, 16, 24, 48):
            gpu_info = {"cc": 12.0, "vram_gb": vram}
            ids = ["qwen", "qwen_fine", "canon", "film", "moss", "h3_fl", "h3_ref"]
            with self.subTest(vram=vram):
                inst = workflows.expected_install(ids, gpu_info, 64)
                templates = workflows.templates(inst)
                self.assertIn("qwen-canon", templates)
                self.assertIn("h3-ref2va-turbo", templates)
                nodes = workflows.required_nodes(inst)
                self.assertIn("MiniMaxH3ReferenceToVideo", nodes)
                self.assertEqual("UnetLoaderGGUF" in nodes, vram < 24)

    def test_workflows_only_fill_missing_files(self):
        import workflows
        inst = workflows.expected_install(["qwen"], {"cc": 12.0, "vram_gb": 24}, 64)
        root = self.root / "workflow-export"
        written = workflows.export(inst, root)
        self.assertTrue(written)
        sample = Path(written[0])
        sample.write_text('{"user_edited": true}', encoding="utf-8")
        self.assertEqual(workflows.export(inst, root), [])
        self.assertEqual(json.loads(sample.read_text(encoding="utf-8")), {"user_edited": True})
        other = Path(written[1])
        other.unlink()
        self.assertEqual(workflows.export(inst, root), [str(other)])

    def test_audit_reports_missing_nodes_models_workflows(self):
        import comfy_detect
        detected = {"directory": str(self.root / "audit-empty"), "url": self.paths.comfy_url(),
                    "running": True, "runtime": {"nodes": {"KSampler": {}}}}
        with patch.object(comfy_detect, "discover", return_value=detected):
            result = comfy_detect.audit(["qwen", "h3_fl"], {"cc": 12.0, "vram_gb": 16}, 64)
        self.assertIn("TextEncodeQwenImage21", result["missing_nodes"])
        self.assertIn("UnetLoaderGGUF", result["missing_nodes"])
        self.assertNotIn("UnetLoaderGGUF", result["core_missing"])
        self.assertTrue(result["plugin"]["required"])
        self.assertTrue(any(not model["present"] for model in result["models"]))
        self.assertTrue(all(not w["present"] for w in result["workflows"]))

    def test_start_reuses_external_process_without_spawning_comfy(self):
        import comfy_detect
        with patch.object(comfy_detect, "select", return_value={"running": True, "nodes_ready": True}), \
                patch.object(self.services, "_spawn") as spawn:
            self.services.start_all(0)
        self.assertEqual([call.args[0] for call in spawn.call_args_list], ["worker", "canvas"])

    def test_occupied_comfy_port_prevents_duplicate_start(self):
        import comfy_detect
        with patch.object(comfy_detect, "select", return_value={"running": False}), \
                patch.object(comfy_detect, "port_open", return_value=True), \
                patch.object(self.services, "_spawn") as spawn:
            with self.assertRaises(RuntimeError):
                self.services.start_all(0)
        spawn.assert_not_called()

    def test_health_identifies_external_comfy(self):
        response = Mock(is_success=True)
        response.json.return_value = {"system": {"comfyui_version": "0.43.2"}}
        with patch.object(self.services.httpx, "get", return_value=response):
            result = self.services.health()["comfyui"]
        self.assertTrue(result["running"])
        self.assertTrue(result["external"])

    def test_existing_conflicting_model_is_never_overwritten(self):
        import catalog
        fid = "lora_film"
        src = self.installer.model_path(fid)
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_bytes(b"source")
        target = self.paths.comfy_dir() / "models" / catalog.dest_of(fid)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"original user model")
        with self.assertRaises(RuntimeError):
            self.installer.expose_model(fid)
        self.assertEqual(target.read_bytes(), b"original user model")

    def test_existing_cuda_torch_is_preserved(self):
        self.paths.COMFY_DIR.mkdir(exist_ok=True)
        (self.paths.COMFY_DIR / "main.py").touch()
        with patch.object(self.installer, "python_has", return_value=True), \
                patch.object(self.installer, "run") as run:
            self.installer.install_comfy("cu130")
        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(len(commands), 1)
        self.assertIn("-r", commands[0])
        self.assertNotIn("torch", commands[0])

    def test_probe_uses_comfy_api_not_arbitrary_http_response(self):
        import httpx
        def handler(request):
            if request.url.path == "/system_stats":
                return httpx.Response(200, json={"system": {"comfyui_version": "0.43.2"}})
            return httpx.Response(200, json={"KSampler": {}, "SaveImage": {}})
        transport = httpx.MockTransport(handler)
        original = httpx.Client
        # 暂时取消 setUp 中的只读 probe mock，用真实实现测试 HTTP 解析。
        spec = importlib.util.spec_from_file_location("probe_test", ROOT / "launcher" / "comfy_detect.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with patch.object(module.httpx, "Client", side_effect=lambda **kw: original(transport=transport, **kw)):
            result = module.probe("http://127.0.0.1:8188")
        self.assertIn("SaveImage", result["nodes"])
        invalid = httpx.MockTransport(lambda request: httpx.Response(200, json={"status": "ok"}))
        with patch.object(module.httpx, "Client", side_effect=lambda **kw: original(transport=invalid, **kw)):
            result = module.probe("http://127.0.0.1:8188")
        self.assertFalse(result["api_ready"])
        self.assertFalse(result["nodes_ready"])
        self.assertTrue(result["diagnostics"])

    def test_node_timeout_does_not_hide_healthy_comfy(self):
        import httpx
        spec = importlib.util.spec_from_file_location("probe_partial", ROOT / "launcher" / "comfy_detect.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        original = httpx.Client
        def handler(request):
            if request.url.path == "/system_stats":
                return httpx.Response(200, json={"system": {}})
            raise httpx.ReadTimeout("slow plugin", request=request)
        with patch.object(module.httpx, "Client", side_effect=lambda **kw: original(transport=httpx.MockTransport(handler), **kw)):
            result = module.probe("http://127.0.0.1:8188")
        self.assertTrue(result["api_ready"])
        self.assertFalse(result["nodes_ready"])
        self.assertIn("60 秒", result["diagnostics"][0])
        import comfy_detect
        with patch.object(comfy_detect, "probe", return_value=result):
            detected = comfy_detect.discover(force=True)
        self.assertTrue(detected["running"])
        self.assertFalse(detected["nodes_ready"])

    def test_probe_diagnoses_auth_redirect_and_html(self):
        import httpx
        spec = importlib.util.spec_from_file_location("probe_errors", ROOT / "launcher" / "comfy_detect.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        original = httpx.Client
        for status, text, hint in ((401, "auth", "认证"), (403, "forbidden", "认证"),
                                   (302, "redirect", "重定向"), (404, "missing", "原生端口"),
                                   (200, "<html>portal</html>", "不是 JSON")):
            transport = httpx.MockTransport(lambda request: httpx.Response(status, text=text))
            with self.subTest(status=status), \
                    patch.object(module.httpx, "Client", side_effect=lambda **kw: original(transport=transport, **kw)):
                result = module.probe("http://127.0.0.1:8188")
                self.assertFalse(result["api_ready"])
                self.assertIn(hint, result["diagnostics"][0])

    def test_basic_auth_storage_is_bound_to_one_origin(self):
        auth = self.paths.comfy_auth
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            url = "http://127.0.0.1:8188"
            self.assertIsNone(auth.client_auth(root, url))
            auth.save(root, url, True, "user", "secret-test-password")
            self.assertEqual(auth.client_auth(root, url), ("user", "secret-test-password"))
            self.assertIsNone(auth.client_auth(root, "http://127.0.0.1:8190"))
            self.assertIsNone(auth.client_auth(root, "http://example.com:8188"))
            self.assertNotIn("secret-test-password", json.dumps(auth.status(root, url)))
            auth.save(root, url, True, "user", None)
            self.assertEqual(auth.client_auth(root, url)[1], "secret-test-password")
            for new_url, username in ((url, "another-user"), ("http://127.0.0.1:8190", "user")):
                with self.assertRaises(ValueError):
                    auth.save(root, new_url, True, username, None)
            if sys.platform.startswith("linux"):
                self.assertEqual((root / "comfy-auth.json").stat().st_mode & 0o777, 0o600)
            auth.save(root, url, False)
            self.assertFalse((root / "comfy-auth.json").exists())

    def test_bearer_storage_and_auth_type_switch(self):
        auth = self.paths.comfy_auth
        url = "http://127.0.0.1:8188"
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for text in ("example-token", "Bearer example-token", "Authorization: Bearer example-token"):
                auth.save(root, url, True, auth_type="bearer", token=text)
                self.assertEqual(auth.client_headers(root, url), {"Authorization": "Bearer example-token"})
                self.assertIsNone(auth.client_auth(root, url))
                self.assertEqual(auth.client_headers(root, "http://127.0.0.1:8190"), {})
                self.assertEqual(auth.client_headers(root, "http://example.com"), {})
                self.assertNotIn("example-token", json.dumps(auth.status(root, url)))
                self.assertEqual(auth.status(root, url)["type"], "bearer")
            auth.save(root, url, True, auth_type="bearer")
            self.assertTrue(auth.status(root, url)["token_saved"])
            for token in ("", "one two", "abc\r\nInjected: value"):
                with self.subTest(token=repr(token)), self.assertRaises(ValueError):
                    auth.save(root, url, True, auth_type="bearer", token=token)
            with self.assertRaises(ValueError):
                auth.save(root, url, True, username="user")
            auth.save(root, url, True, "user", "password")
            self.assertEqual(auth.client_headers(root, url), {})
            with self.assertRaises(ValueError):
                auth.save(root, url, True, auth_type="bearer")

    def test_probe_sends_basic_auth_without_following_redirects(self):
        import base64
        import httpx
        spec = importlib.util.spec_from_file_location("probe_auth", ROOT / "launcher" / "comfy_detect.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        original = httpx.Client
        requests = []
        expected = "Basic " + base64.b64encode(b"user:password").decode()
        def handler(request):
            requests.append(request)
            self.assertEqual(request.headers.get("Authorization"), expected)
            return httpx.Response(200, json={"system": {}} if request.url.path == "/system_stats" else {"KSampler": {}})
        with patch.object(self.paths, "auth_for", return_value=("user", "password")), \
                patch.object(module.httpx, "Client", side_effect=lambda **kw: original(transport=httpx.MockTransport(handler), **kw)):
            result = module.probe("http://127.0.0.1:8188")
        self.assertTrue(result["nodes_ready"])
        self.assertEqual(len(requests), 2)

    def test_auth_settings_api_never_returns_password(self):
        from fastapi.testclient import TestClient
        auth_file = self.paths.ONEKEY / "comfy-auth.json"
        self.addCleanup(lambda: auth_file.unlink(missing_ok=True))
        settings = self.paths.SETTINGS
        old = settings.read_bytes() if settings.exists() else None
        def restore():
            settings.write_bytes(old) if old is not None else settings.unlink(missing_ok=True)
        self.addCleanup(restore)
        with TestClient(self.main.app) as client:
            response = client.post("/api/settings", json={"comfy_url": "http://127.0.0.1:8188", "comfy_auth": {
                "enabled": True, "username": "test-user", "password": "super-secret-test"}})
            self.assertEqual(response.status_code, 200)
            self.assertNotIn("super-secret-test", response.text)
            self.assertNotIn("super-secret-test", settings.read_text(encoding="utf-8"))
            check = client.get("/api/check")
            self.assertNotIn("super-secret-test", check.text)
            self.assertTrue(check.json()["comfy_auth"]["password_saved"])
            response = client.post("/api/settings", json={"comfy_auth": {"enabled": True, "username": "test-user"}})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(self.paths.auth_for("http://127.0.0.1:8188")[1], "super-secret-test")
            response = client.post("/api/settings", json={"comfy_auth": {"enabled": False}})
            self.assertEqual(response.status_code, 200)
            self.assertFalse(auth_file.exists())

    def test_health_uses_basic_auth(self):
        response = Mock(is_success=True)
        response.json.return_value = {"system": {}}
        with patch.object(self.paths, "auth_for", return_value=("user", "password")), \
                patch.object(self.services.httpx, "get", return_value=response) as get:
            self.assertTrue(self.services.health()["comfyui"]["ok"])
        self.assertEqual(get.call_args.kwargs["auth"], ("user", "password"))

    def test_bearer_api_save_probe_health_without_token_disclosure(self):
        import httpx
        from fastapi.testclient import TestClient
        auth_file = self.paths.ONEKEY / "comfy-auth.json"
        self.addCleanup(lambda: auth_file.unlink(missing_ok=True))
        settings = self.paths.SETTINGS
        old = settings.read_bytes() if settings.exists() else None
        self.addCleanup(lambda: settings.write_bytes(old) if old is not None else settings.unlink(missing_ok=True))
        with TestClient(self.main.app) as client:
            response = client.post("/api/settings", json={"comfy_url": "http://127.0.0.1:8188", "comfy_auth": {
                "enabled": True, "type": "bearer", "token": "Bearer example-secret-token"}})
            self.assertEqual(response.status_code, 200)
            self.assertNotIn("example-secret-token", response.text)
            check = client.get("/api/check")
            self.assertTrue(check.json()["comfy_auth"]["token_saved"])
            self.assertNotIn("example-secret-token", check.text)
            self.assertNotIn("example-secret-token", settings.read_text(encoding="utf-8"))
        spec = importlib.util.spec_from_file_location("probe_bearer", ROOT / "launcher" / "comfy_detect.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        original = httpx.Client
        def handler(request):
            self.assertEqual(request.headers.get("Authorization"), "Bearer example-secret-token")
            return httpx.Response(200, json={"system": {}} if request.url.path == "/system_stats" else {"KSampler": {}})
        with patch.object(module.httpx, "Client", side_effect=lambda **kw: original(transport=httpx.MockTransport(handler), **kw)):
            self.assertTrue(module.probe("http://127.0.0.1:8188")["nodes_ready"])
        response = Mock(is_success=True)
        response.json.return_value = {"system": {}}
        with patch.object(self.services.httpx, "get", return_value=response) as get:
            self.assertTrue(self.services.health()["comfyui"]["ok"])
        self.assertIsNone(get.call_args.kwargs["auth"])
        self.assertEqual(get.call_args.kwargs["headers"], {"Authorization": "Bearer example-secret-token"})

    def test_process_is_reported_even_if_api_is_unavailable(self):
        import comfy_detect
        root = self.root / "supervisor-comfy"
        process = {"directory": str(root), "url": self.paths.comfy_url(), "pid": 598}
        partial = {"api_ready": False, "nodes_ready": False, "diagnostics": ["/system_stats: HTTP 401"], "nodes": {}}
        with patch.object(comfy_detect, "process_installs", return_value=[process]), \
                patch.object(comfy_detect, "probe", return_value=partial):
            result = comfy_detect.discover(force=True)
        self.assertTrue(result["process_running"])
        self.assertTrue(result["running"])
        self.assertFalse(result["api_ready"])
        self.assertEqual(result["instances"][0]["pid"], 598)

    def test_audit_does_not_mark_unknown_nodes_as_missing(self):
        import comfy_detect
        result = {"directory": str(self.root), "url": self.paths.comfy_url(), "running": True,
                  "nodes_ready": False, "runtime": {"nodes": {}}, "diagnostics": ["timeout"]}
        with patch.object(comfy_detect, "discover", return_value=result):
            audit = comfy_detect.audit(["qwen"], {"cc": 12, "vram_gb": 24}, 64)
        self.assertEqual(audit["missing_nodes"], [])
        self.assertEqual(audit["core_missing"], [])
        self.assertEqual(audit["diagnostics"], ["timeout"])

    def test_install_repairs_files_when_port_is_busy_and_api_unknown(self):
        import comfy_detect
        result = {"existing": True, "directory_known": True, "occupied": True,
                  "running": False, "diagnostics": ["/object_info: timeout"]}
        gpu_info = {"cc": 12, "vram_gb": 24}
        with patch.object(self.main, "system_check", return_value={"gpu": gpu_info, "ram_gb": 64, "torch": "cu130"}), \
                patch.object(comfy_detect, "select", return_value=result), \
                patch.object(comfy_detect, "audit", return_value={"core_missing": [], "nodes_ready": False}), \
                patch.object(self.paths, "load", return_value={}), \
                patch.object(self.installer, "install_comfy") as install_comfy, \
                patch.object(self.installer, "install_packs") as install_packs, \
                patch.object(self.services, "health", return_value={"canvas": {"running": False}}):
            self.main._install(["qwen"])
        install_comfy.assert_not_called()
        install_packs.assert_called_once_with(["qwen"], gpu_info, 64, [])

    def test_start_does_not_reuse_process_without_node_api(self):
        import comfy_detect
        with patch.object(comfy_detect, "select", return_value={"running": True, "nodes_ready": False,
                                                               "diagnostics": ["/object_info: HTTP 401"]}), \
                patch.object(self.services, "_spawn") as spawn:
            with self.assertRaisesRegex(RuntimeError, "HTTP 401"):
                self.services.start_all(0)
        spawn.assert_not_called()

    @unittest.skipUnless(sys.platform.startswith("linux"), "需要 Linux 符号链接和 /proc 支持")
    def test_detect_real_running_comfy_commandline(self):
        import comfy_detect
        root = self.root / "proc-comfy"
        root.mkdir(exist_ok=True)
        (root / "folder_paths.py").touch()
        (root / "main.py").write_text("import time; time.sleep(60)", encoding="utf-8")
        proc = subprocess.Popen([sys.executable, "main.py", "--port", "8199"], cwd=root)
        try:
            spec = importlib.util.spec_from_file_location("proc_test", ROOT / "launcher" / "comfy_detect.py")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            result = module.process_installs()
            selected = next(p for p in result if p["pid"] == proc.pid)
            self.assertEqual(selected["directory"], str(root))
            self.assertEqual(selected["url"], "http://127.0.0.1:8199")
        finally:
            proc.terminate()
            proc.wait(timeout=5)

    @unittest.skipUnless(sys.platform.startswith("linux"), "需要 Linux 符号链接支持")
    def test_downloaded_model_is_linked_without_copying(self):
        import catalog
        fid = "lora_fix"
        src = self.installer.model_path(fid)
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_bytes(b"model")
        self.installer.expose_model(fid)
        dst = self.paths.comfy_dir() / "models" / catalog.dest_of(fid)
        self.assertTrue(dst.is_symlink())
        self.assertEqual(dst.resolve(), src.resolve())
        # 多次补齐不重复复制、不覆盖链接。
        with patch.dict(catalog.FILES, {fid: {**catalog.FILES[fid], "size": 5}}):
            self.installer.expose_model(fid)


if __name__ == "__main__":
    unittest.main()
