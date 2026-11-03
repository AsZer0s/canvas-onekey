"""一键包里所有目录都放在包自己的文件夹下，不往系统盘和用户目录写东西。"""
from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BIN = ROOT / "bin"
UV = BIN / "uv.exe"
RUNTIME = ROOT / "runtime"
COMFY_DIR = RUNTIME / "ComfyUI"
COMFY_VENV = RUNTIME / "comfy-venv"
COMFY_PY = COMFY_VENV / "Scripts" / "python.exe"
MODELS = ROOT / "模型"
DATA = ROOT / "data"
ONEKEY = DATA / "onekey"
OUTPUT = ROOT / "输出"
CANVAS = ROOT / "canvas"
STATE = ONEKEY / "state.json"
INSTALLED = ONEKEY / "installed.json"
SETTINGS = ONEKEY / "settings.json"

COMFY_VERSION = "v0.43.2"
COMFY_ZIP = f"https://github.com/comfyanonymous/ComfyUI/archive/refs/tags/{COMFY_VERSION}.zip"
PORTS = {"setup": 10020, "canvas": 10027, "worker": 8089, "comfy": 8188}

for d in (RUNTIME, MODELS, ONEKEY, OUTPUT):
    d.mkdir(parents=True, exist_ok=True)


def uv_env() -> dict:
    """uv 的缓存和下载的 Python 都放进包里。"""
    env = dict(os.environ)
    env.update(UV_CACHE_DIR=str(RUNTIME / "uv-cache"), UV_PYTHON_INSTALL_DIR=str(RUNTIME / "python"),
               UV_LINK_MODE="copy", PYTHONUTF8="1")
    s = load(SETTINGS, {})
    if s.get("mirror"):
        env["UV_INDEX_URL"] = "https://pypi.tuna.tsinghua.edu.cn/simple"
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
