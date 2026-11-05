"""程序、模型和数据放在项目内；Python 和依赖使用系统安装。"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

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

COMFY_VERSION = "v0.43.2"
COMFY_ZIP = f"https://github.com/comfyanonymous/ComfyUI/archive/refs/tags/{COMFY_VERSION}.zip"
PORTS = {"setup": 10020, "canvas": 10027, "worker": 8089, "comfy": 8188}

for d in (RUNTIME, MODELS, ONEKEY, OUTPUT):
    d.mkdir(parents=True, exist_ok=True)


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
