"""只接受本机连接；画布额外允许 Cloudflare Quick Tunnel 的 Host。"""
from __future__ import annotations

import ipaddress
import re

from starlette.responses import PlainTextResponse


def allowed_host(authority: str, allow_tunnel: bool) -> bool:
    authority = authority.lower()
    # 严格解析 Host；不信任 X-Forwarded-Host / Origin 等可伪造的替代值。
    match = re.fullmatch(r"(\[[0-9a-f:]+\]|[a-z0-9.-]+)(?::([0-9]{1,5}))?", authority)
    if not match or (match[2] and not 1 <= int(match[2]) <= 65535):
        return False
    host = match[1].strip("[]")
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        pass
    return allow_tunnel and bool(re.fullmatch(
        r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*trycloudflare\.com", host))


class LocalAccess:
    def __init__(self, app, allow_tunnel: bool = False):
        self.app = app
        self.allow_tunnel = allow_tunnel

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            peer = (scope.get("client") or ("", 0))[0]
            try:
                local = ipaddress.ip_address(peer).is_loopback
            except ValueError:
                local = False
            hosts = [v.decode("latin-1") for k, v in scope.get("headers", []) if k.lower() == b"host"]
            ok = local and len(hosts) == 1 and allowed_host(hosts[0], self.allow_tunnel)
            if not ok:
                if scope["type"] == "websocket":
                    await send({"type": "websocket.close", "code": 1008})
                else:
                    message = "仅允许本机连接及 trycloudflare.com 画布入口" if self.allow_tunnel else "只允许本机访问"
                    await PlainTextResponse(message, status_code=403)(scope, receive, send)
                return
        await self.app(scope, receive, send)
