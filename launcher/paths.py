"""程序、模型和数据放在项目内；Python 和依赖使用系统安装。"""
from __future__ import annotations

import json
import importlib.util
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
RUNTIME = ROOT / "runtime"
COMFY_DIR = RUNTIME / "ComfyUI"
COMFY_PY = Path(sys.executable)
MODELS = ROOT / "模型"
DATA = ROOT / "data"
ONEKEY = DATA / "onekey"
OUTPUT = ROOT / "输出"
CANVAS = ROOT / "canvas"
STATE = ONEKEY / "state.json"
INSTALLED = ONEKEY / "installed.json"
SETTINGS = ONEKEY / "settings.json"

_auth_spec = importlib.util.spec_from_file_location(
    "onekey_comfy_auth", Path(__spec__.origin if __spec__ else __file__).resolve().parents[1]
    / "canvas" / "onekey" / "comfy_auth.py")
comfy_auth = importlib.util.module_from_spec(_auth_spec)
_auth_spec.loader.exec_module(comfy_auth)


def auth_for(url: str):
    return comfy_auth.client_auth(ONEKEY, url)


def auth_headers_for(url: str) -> dict[str, str]:
    return comfy_auth.client_headers(ONEKEY, url)

COMFY_VERSION = "v0.43.2"
COMFY_ZIP = f"https://github.com/comfyanonymous/ComfyUI/archive/refs/tags/{COMFY_VERSION}.zip"
PORTS = {"setup": 10020, "canvas": 10027, "worker": 8089, "comfy": 8188}

for d in (RUNTIME, MODELS, ONEKEY, OUTPUT):
    d.mkdir(parents=True, exist_ok=True)


def comfy_dir() -> Path:
    value = load(SETTINGS, {}).get("comfy_dir") or os.environ.get("COMFYUI_DIR")
    return Path(value).expanduser().resolve() if value else COMFY_DIR


def comfy_url() -> str:
    value = load(SETTINGS, {}).get("comfy_url") or os.environ.get("COMFYUI_URL") or f"http://127.0.0.1:{PORTS['comfy']}"
    return validate_comfy_url(value)


def validate_comfy_url(value: str) -> str:
    """只接入本机 HTTP 实例，不允许凭据、路径、跳转或任意远程服务。"""
    u = urlsplit(value)
    if (u.scheme != "http" or u.hostname not in {"localhost", "127.0.0.1", "::1"}
            or u.username or u.password or u.path not in {"", "/"} or u.query or u.fragment):
        raise ValueError("ComfyUI 地址必须是本机 HTTP 地址，例如 http://127.0.0.1:8188")
    port = u.port or 80
    if u.port is not None and not 1 <= u.port <= 65535:
        raise ValueError("ComfyUI 端口必须在 1～65535 之间")
    host = "[::1]" if u.hostname == "::1" else "127.0.0.1"
    return f"http://{host}:{port}"


def pip_env() -> dict:
    """用系统 pip 安装，缓存留在项目内，保留用户自己的 pip 配置。"""
    env = dict(os.environ)
    env.update(PIP_CACHE_DIR=str(RUNTIME / "pip-cache"), PYTHONUTF8="1")
    s = load(SETTINGS, {})
    if s.get("mirror"):
        env["PIP_INDEX_URL"] = "https://pypi.tuna.tsinghua.edu.cn/simple"
    return env


def load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def save(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)
