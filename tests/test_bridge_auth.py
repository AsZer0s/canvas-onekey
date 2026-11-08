"""实际桥接请求的 Basic Auth 回归：API、参考图上传和生成结果下载。"""
import asyncio
import base64
import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

ROOT = Path(__file__).resolve().parents[1]


class BridgeAuthTests(unittest.TestCase):
    def test_all_comfy_requests_use_basic_auth(self):
        self.check_requests("basic")

    def test_all_comfy_requests_use_bearer_auth(self):
        self.check_requests("bearer")

    def test_transient_poll_and_download_failure_does_not_fail_video(self):
        self.check_requests("bearer", transient=True, kind="video")

    def check_requests(self, auth_type, transient=False, kind="image"):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            onekey = ROOT / "canvas" / "onekey"
            original_path = list(sys.path)
            sys.path.insert(0, str(onekey))
            try:
                with patch.dict(os.environ, {"ONEKEY_DIR": str(root)}):
                    spec = importlib.util.spec_from_file_location("bridge_auth_test", onekey / "bridge.py")
                    bridge = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(bridge)
                    bridge.c.close()
                url = "http://127.0.0.1:8188"
                bridge.INSTALLED.write_text('{"comfy": "' + url + '"}', encoding="utf-8")
                if auth_type == "basic":
                    bridge.comfy_auth.save(root, url, True, "test", "password")
                else:
                    bridge.comfy_auth.save(root, url, True, auth_type="bearer", token="example-test-token")
                requests = []
                expected = "Basic " + base64.b64encode(b"test:password").decode() if auth_type == "basic" else "Bearer example-test-token"
                def handler(request):
                    self.assertEqual(request.headers.get("Authorization"), expected)
                    requests.append(request.url.path)
                    if request.url.path == "/upload/image":
                        return httpx.Response(200, json={"name": "example.png", "subfolder": "canvas"})
                    if request.url.path == "/prompt":
                        return httpx.Response(200, json={"prompt_id": "pid"})
                    if request.url.path == "/history/pid":
                        count = requests.count('/history/pid')
                        if transient and count == 1:
                            raise httpx.ConnectError('All connection attempts failed', request=request)
                        if transient and count == 2:
                            return httpx.Response(503)
                        filename = "result.mp4" if kind == "video" else "result.png"
                        return httpx.Response(200, json={"pid": {"outputs": {"8": {"images": [{"filename": filename, "type": "output"}]}}}})
                    if request.url.path == "/view":
                        if transient and requests.count('/view') == 1:
                            raise httpx.ReadTimeout('temporary', request=request)
                        return httpx.Response(200, content=b"generated-image")
                    return httpx.Response(200, json={})
                original_client = httpx.AsyncClient
                transport = httpx.MockTransport(handler)
                async def run():
                    await bridge.call("/system_stats")
                    await bridge.upload_comfy("test-ref")
                    await bridge.execute("abc123", {"images": []}, kind)
                with patch.object(bridge.httpx, "AsyncClient", side_effect=lambda **kw: original_client(transport=transport, **kw)), \
                        patch.object(bridge, "media_bytes", new=AsyncMock(return_value=b"reference")), \
                        patch.object(bridge, "validate", return_value="fl2va" if kind == "video" else "image"), \
                        patch.object(bridge.asyncio, "sleep", new=AsyncMock()), \
                        patch.object(bridge, "make_graph", return_value={}), \
                        patch.object(bridge, "update") as update:
                    asyncio.run(run())
                if transient:
                    self.assertEqual(requests.count('/prompt'), 1)
                    self.assertEqual(requests.count('/history/pid'), 3)
                    self.assertEqual(requests.count('/view'), 2)
                else:
                    self.assertEqual(requests, ["/system_stats", "/upload/image", "/prompt", "/history/pid", "/view"])
                self.assertEqual((bridge.MEDIA / ("abc123.mp4" if kind == "video" else "abc123.png")).read_bytes(), b"generated-image")
                self.assertEqual(update.call_args.kwargs["status"], "completed")
                self.assertFalse(any(call.kwargs.get('status') == 'failed' for call in update.call_args_list))
            finally:
                sys.path[:] = original_path

    def test_retries_are_bounded_and_never_resubmit_prompt_or_retry_auth(self):
        with tempfile.TemporaryDirectory() as folder:
            onekey = ROOT / "canvas/onekey"
            old_path = list(sys.path)
            sys.path.insert(0, str(onekey))
            try:
                with patch.dict(os.environ, {"ONEKEY_DIR": folder}):
                    spec = importlib.util.spec_from_file_location('bridge_retry_test', onekey / 'bridge.py')
                    bridge = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(bridge)
                    bridge.c.close()
                original = httpx.AsyncClient
                requests = []
                status = [None]
                def handler(request):
                    requests.append(request.url.path)
                    if status[0] is not None:
                        return httpx.Response(status[0])
                    raise httpx.ConnectError('temporary', request=request)
                def client(**kw):
                    return original(transport=httpx.MockTransport(handler), **kw)
                async def run():
                    with patch.object(bridge.httpx, 'AsyncClient', side_effect=client):
                        with self.assertRaises(bridge.HTTPException) as error:
                            await bridge.call('/prompt', 'POST', retry_read=True, json={'prompt': {}})
                        self.assertIn('提交结果未知', error.exception.detail)
                        self.assertEqual(requests, ['/prompt'])
                        requests.clear()
                        status[0] = 401
                        with self.assertRaises(bridge.HTTPException):
                            await bridge.call('/history/pid', retry_read=True)
                        self.assertEqual(requests, ['/history/pid'])
                        requests.clear()
                        status[0] = None
                        with patch.object(bridge, 'READ_RETRY_WINDOW', 0):
                            with self.assertRaises(bridge.HTTPException) as error:
                                await bridge.call('/history/pid', retry_read=True)
                        self.assertIn('任务可能仍在 ComfyUI 执行', error.exception.detail)
                        self.assertEqual(requests, ['/history/pid'])
                asyncio.run(run())
            finally:
                sys.path[:] = old_path
