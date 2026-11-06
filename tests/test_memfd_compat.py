"""memfd runtime compatibility without GPU, torch or external ComfyUI."""
import ctypes
import importlib.util
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("memfd_plugin_test", ROOT / "launcher/comfy_plugins/canvas_onekey_memfd/__init__.py")
plugin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plugin)
deploy_spec = importlib.util.spec_from_file_location("memfd_deploy_test", ROOT / "launcher/comfy_compat.py")
deploy = importlib.util.module_from_spec(deploy_spec)
deploy_spec.loader.exec_module(deploy)


def legacy_factory(size=0):
    file = open("aimdo", "w+b", buffering=0,
                opener=lambda name, mode: os.memfd_create(name, os.MFD_CLOEXEC))
    file.truncate(size)
    return file


def fixed_factory(size=0):
    return None


class MemfdCompatTests(unittest.TestCase):
    def test_only_detects_actual_legacy_call(self):
        self.assertTrue(plugin.needs_fallback(legacy_factory))
        self.assertFalse(plugin.needs_fallback(fixed_factory))

    def test_supported_python_and_non_linux_are_unchanged(self):
        for platform, memfd in (("linux", lambda *args: 1), ("win32", None)):
            module = types.SimpleNamespace(create_anonymous_file=legacy_factory)
            with self.subTest(platform=platform), patch.object(plugin.sys, "platform", platform), \
                    patch.object(plugin.os, "memfd_create", memfd, create=True):
                self.assertFalse(plugin.install(module))
            self.assertIs(module.create_anonymous_file, legacy_factory)

    def test_fixed_upstream_is_not_patched(self):
        module = types.SimpleNamespace(create_anonymous_file=fixed_factory)
        with patch.object(plugin.sys, "platform", "linux"), patch.object(plugin.os, "memfd_create", None, create=True):
            self.assertFalse(plugin.install(module))
        self.assertIs(module.create_anonymous_file, fixed_factory)

    def test_missing_libc_does_not_replace_factory(self):
        module = types.SimpleNamespace(create_anonymous_file=legacy_factory)
        with patch.object(plugin.sys, "platform", "linux"), patch.object(plugin.os, "memfd_create", None, create=True), \
                patch.object(plugin.ctypes, "CDLL", return_value=object()):
            self.assertFalse(plugin.install(module))
        self.assertIs(module.create_anonymous_file, legacy_factory)

    def test_fallback_updates_imported_alias_and_sizes_file(self):
        module = types.SimpleNamespace(create_anonymous_file=legacy_factory)
        utils = types.SimpleNamespace(create_anonymous_file=legacy_factory)
        with tempfile.TemporaryFile() as backing:
            fd = os.dup(backing.fileno())
            create = Mock(return_value=fd)
            libc = types.SimpleNamespace(memfd_create=create)
            with patch.object(plugin.sys, "platform", "linux"), patch.object(plugin.os, "memfd_create", None, create=True), \
                    patch.object(plugin.ctypes, "CDLL", return_value=libc):
                self.assertTrue(plugin.install(module, utils))
                self.assertIs(utils.create_anonymous_file, module.create_anonymous_file)
                with utils.create_anonymous_file(8192) as file:
                    self.assertEqual(os.fstat(file.fileno()).st_size, 8192)
                    self.assertEqual(file.read(8), b"\0" * 8)
                    file.seek(0)
                    file.write(b"memfd-ok")
                    file.seek(0)
                    self.assertEqual(file.read(8), b"memfd-ok")
                self.assertFalse(plugin.install(module, utils))
            create.assert_called_once_with(b"aimdo", 1)

    def test_invalid_sizes_and_syscall_errors(self):
        module = types.SimpleNamespace(create_anonymous_file=legacy_factory)
        create = Mock(return_value=-1)
        with patch.object(plugin.sys, "platform", "linux"), patch.object(plugin.os, "memfd_create", None, create=True), \
                patch.object(plugin.ctypes, "CDLL", return_value=types.SimpleNamespace(memfd_create=create)):
            self.assertTrue(plugin.install(module))
        for size in (-1, 2 ** 63):
            with self.assertRaises(ValueError):
                module.create_anonymous_file(size)
        with self.assertRaises(TypeError):
            module.create_anonymous_file(1.5)
        create.assert_not_called()
        with patch.object(plugin.ctypes, "get_errno", return_value=1):
            with self.assertRaises(OSError) as error:
                module.create_anonymous_file(0)
        self.assertEqual(error.exception.errno, 1)

    def test_fd_is_closed_if_wrapping_fails(self):
        module = types.SimpleNamespace(create_anonymous_file=legacy_factory)
        with patch.object(plugin.sys, "platform", "linux"), patch.object(plugin.os, "memfd_create", None, create=True), \
                patch.object(plugin.ctypes, "CDLL", return_value=types.SimpleNamespace(memfd_create=Mock(return_value=123))):
            self.assertTrue(plugin.install(module))
        with patch.object(plugin.os, "fdopen", side_effect=OSError("test")), patch.object(plugin.os, "close") as close:
            with self.assertRaises(OSError):
                module.create_anonymous_file()
        close.assert_called_once_with(123)

    @unittest.skipUnless(sys.platform == "linux", "Requires Linux libc memfd")
    def test_real_libc_fallback_with_missing_python_api(self):
        module = types.SimpleNamespace(create_anonymous_file=legacy_factory)
        with patch.object(plugin.os, "memfd_create", None, create=True):
            self.assertTrue(plugin.install(module))
            with module.create_anonymous_file(4096) as file:
                self.assertFalse(os.get_inheritable(file.fileno()))
                self.assertEqual(os.fstat(file.fileno()).st_size, 4096)
                file.write(b"working")
                file.seek(0)
                self.assertEqual(file.read(7), b"working")

    def test_deployment_idempotent_preserves_upstream_and_user_changes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "main.py").touch()
            upstream = root / "comfy/anonymous_file.py"
            upstream.parent.mkdir()
            upstream.write_text("original os.memfd_create(name, 1)", encoding="utf-8")
            self.assertTrue(deploy.ensure(root))
            self.assertFalse(deploy.ensure(root))
            self.assertEqual(upstream.read_text(), "original os.memfd_create(name, 1)")
            self.assertTrue(deploy.status(root)["legacy_call"])
            deploy.plugin_file(root).write_text("user changes", encoding="utf-8")
            with self.assertRaises(RuntimeError):
                deploy.ensure(root)
            self.assertEqual(deploy.plugin_file(root).read_text(), "user changes")
