"""ComfyUI 可选 Basic / Bearer 认证，仅向绑定的回环地址发送。"""
from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from urllib.parse import urlsplit


def canonical_url(url: str) -> str:
    u = urlsplit(url)
    if (u.scheme != "http" or u.hostname not in {"localhost", "127.0.0.1", "::1"}
            or u.username or u.password or u.path not in {"", "/"} or u.query or u.fragment):
        raise ValueError("认证只支持不含凭据的本机 HTTP 地址")
    port = u.port if u.port is not None else 80
    if not 1 <= port <= 65535:
        raise ValueError("无效端口")
    return f"http://{'[::1]' if u.hostname == '::1' else '127.0.0.1'}:{port}"


def read(onekey: Path) -> dict:
    try:
        data = json.loads((onekey / "comfy-auth.json").read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def client_auth(onekey: Path, url: str) -> tuple[str, str] | None:
    try:
        url = canonical_url(url)
    except ValueError:
        return None
    data = read(onekey)
    if (data.get("url") == url and data.get("type", "basic") == "basic"
            and isinstance(data.get("username"), str) and isinstance(data.get("password"), str)):
        return data["username"], data["password"]
    return None


def client_headers(onekey: Path, url: str) -> dict[str, str]:
    try:
        url = canonical_url(url)
    except ValueError:
        return {}
    data = read(onekey)
    token = data.get("token")
    if data.get("url") == url and data.get("type") == "bearer" and isinstance(token, str) and token:
        if any(c.isspace() or ord(c) < 33 or ord(c) > 126 for c in token):
            return {}
        return {"Authorization": "Bearer " + token}
    return {}


def status(onekey: Path, url: str) -> dict:
    auth = client_auth(onekey, url)
    bearer = bool(client_headers(onekey, url))
    return {"enabled": auth is not None or bearer, "type": "bearer" if bearer else "basic",
            "username": auth[0] if auth else "", "password_saved": auth is not None, "token_saved": bearer}


def save(onekey: Path, url: str, enabled: bool, username: str = "", password: str | None = None,
         auth_type: str = "basic", token: str | None = None) -> None:
    target = onekey / "comfy-auth.json"
    if not enabled:
        target.unlink(missing_ok=True)
        return
    url = canonical_url(url)
    if auth_type == "basic":
        if not isinstance(username, str) or ":" in username or any(c in username for c in "\r\n\0"):
            raise ValueError("Basic Auth 用户名不能含冒号或控制字符")
        if password is None:
            existing = client_auth(onekey, url)
            if not existing or existing[0] != username:
                raise ValueError("首次启用或更换地址 / 用户名时，请填写 Basic Auth 密码")
            password = existing[1]
        if not isinstance(password, str) or any(c in password for c in "\r\n\0"):
            raise ValueError("Basic Auth 密码不能含控制字符")
        data = {"url": url, "type": "basic", "username": username, "password": password}
    elif auth_type == "bearer":
        if token is None:
            existing = read(onekey)
            if not client_headers(onekey, url):
                raise ValueError("首次启用 Bearer 或更换地址 / 认证类型时，请填写 Token")
            token = existing["token"]
        if not isinstance(token, str):
            raise ValueError("Bearer Token 格式无效")
        token = token.strip()
        # 兼容用户粘贴完整请求头，但仅存储 token 本身。
        if token.lower().startswith("authorization:"):
            token = token.split(":", 1)[1].strip()
        if token.lower().startswith("bearer "):
            token = token[7:].strip()
        if not token or any(c.isspace() or ord(c) < 33 or ord(c) > 126 for c in token):
            raise ValueError("Bearer Token 不能为空或包含空白 / 控制字符")
        data = {"url": url, "type": "bearer", "token": token}
    else:
        raise ValueError("认证类型只支持 basic 或 bearer")
    onekey.mkdir(parents=True, exist_ok=True)
    tmp = onekey / f".comfy-auth-{uuid.uuid4().hex}.tmp"
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)
