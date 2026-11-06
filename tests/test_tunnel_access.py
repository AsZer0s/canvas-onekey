import asyncio
import importlib.util
import unittest
from pathlib import Path

from starlette.responses import PlainTextResponse

spec = importlib.util.spec_from_file_location("onekey_access_test", Path(__file__).resolve().parents[1] / "canvas/onekey/access.py")
access = importlib.util.module_from_spec(spec)
spec.loader.exec_module(access)


class TunnelAccessTests(unittest.TestCase):
    def request(self, host, peer="127.0.0.1", tunnel=True, extra=None, kind="http"):
        messages = []
        async def app(scope, receive, send):
            if scope["type"] == "websocket":
                await send({"type": "websocket.accept"})
            else:
                await PlainTextResponse("ok")(scope, receive, send)
        async def send(message):
            messages.append(message)
        async def receive():
            return {"type": "http.request", "body": b""}
        scope = {"type": kind, "method": "GET", "client": (peer, 1234),
                 "headers": [(b"host", host.encode())] + (extra or [])}
        asyncio.run(access.LocalAccess(app, allow_tunnel=tunnel)(scope, receive, send))
        return messages[0]

    def test_tunnel_and_local_hosts(self):
        for host in ("localhost:10027", "127.0.0.1:10027", "[::1]:10027", "trycloudflare.com",
                     "random-quick-tunnel.trycloudflare.com", "RANDOM.trycloudflare.com:443"):
            with self.subTest(host=host):
                self.assertEqual(self.request(host)["status"], 200)

    def test_forged_suffixes_and_malformed_hosts_are_denied(self):
        for host in ("trycloudflare.com.evil.example", "eviltrycloudflare.com", "evil.example",
                     "foo.trycloudflare.com@evil.example", "-bad.trycloudflare.com", "localhost:99999"):
            with self.subTest(host=host):
                self.assertEqual(self.request(host)["status"], 403)

    def test_remote_transport_cannot_bypass_with_tunnel_host(self):
        self.assertEqual(self.request("real.trycloudflare.com", peer="203.0.113.1")["status"], 403)

    def test_forwarded_client_does_not_block_local_cloudflared(self):
        self.assertEqual(self.request("real.trycloudflare.com", extra=[(b"x-forwarded-for", b"203.0.113.1")])["status"], 200)

    def test_forwarded_host_and_duplicate_host_do_not_bypass(self):
        self.assertEqual(self.request("evil.example", extra=[(b"x-forwarded-host", b"real.trycloudflare.com")])["status"], 403)
        self.assertEqual(self.request("localhost", extra=[(b"host", b"real.trycloudflare.com")])["status"], 403)

    def test_worker_does_not_allow_tunnel_host(self):
        self.assertEqual(self.request("real.trycloudflare.com", tunnel=False)["status"], 403)
        self.assertEqual(self.request("localhost:8089", tunnel=False)["status"], 200)

    def test_websocket_allowed_and_denied(self):
        self.assertEqual(self.request("real.trycloudflare.com", kind="websocket")["type"], "websocket.accept")
        message = self.request("evil.example", kind="websocket")
        self.assertEqual(message, {"type": "websocket.close", "code": 1008})
