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
                bridge.comfy_auth.save(root, url, True, "test", "password")
                requests = []
                expected = "Basic " + base64.b64encode(b"test:password").decode()
                def handler(request):
                    self.assertEqual(request.headers.get("Authorization"), expected)
                    requests.append(request.url.path)
                    if request.url.path == "/upload/image":
                        return httpx.Response(200, json={"name": "example.png", "subfolder": "canvas"})
                    if request.url.path == "/prompt":
                        return httpx.Response(200, json={"prompt_id": "pid"})
                    if request.url.path == "/history/pid":
                        return httpx.Response(200, json={"pid": {"outputs": {"8": {"images": [{"filename": "result.png", "type": "output"}]}}}})
                    if request.url.path == "/view":
                        return httpx.Response(200, content=b"generated-image")
                    return httpx.Response(200, json={})
                original_client = httpx.AsyncClient
                transport = httpx.MockTransport(handler)
                async def run():
                    await bridge.call("/system_stats")
                    await bridge.upload_comfy("test-ref")
                    await bridge.execute("abc123", {"images": []}, "image")
                with patch.object(bridge.httpx, "AsyncClient", side_effect=lambda **kw: original_client(transport=transport, **kw)), \
                        patch.object(bridge, "media_bytes", new=AsyncMock(return_value=b"reference")), \
                        patch.object(bridge, "validate", return_value="image"), \
                        patch.object(bridge, "make_graph", return_value={}), \
                        patch.object(bridge, "update") as update:
                    asyncio.run(run())
                self.assertEqual(requests, ["/system_stats", "/upload/image", "/prompt", "/history/pid", "/view"])
                self.assertEqual((bridge.MEDIA / "abc123.png").read_bytes(), b"generated-image")
                self.assertEqual(update.call_args.kwargs["status"], "completed")
            finally:
                sys.path[:] = original_path
