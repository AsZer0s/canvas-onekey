"""启动 / 停止 ComfyUI、画布、任务服务三个后台进程（都只监听 127.0.0.1）。"""
from __future__ import annotations

import os
import subprocess
import sys
import time

import httpx

import paths

procs: dict[str, subprocess.Popen] = {}
LOGS = paths.ONEKEY / "logs"
LOGS.mkdir(parents=True, exist_ok=True)
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _spawn(name: str, cmd: list[str], cwd, env: dict) -> None:
    old = procs.get(name)
    if old and old.poll() is None:
        return
    log = open(LOGS / f"{name}.log", "a", encoding="utf-8", errors="replace")
    log.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} 启动 {name} =====\n")
    log.flush()
    procs[name] = subprocess.Popen(cmd, cwd=str(cwd), env=env, stdout=log, stderr=subprocess.STDOUT, creationflags=NO_WINDOW)


def canvas_env() -> dict:
    env = dict(os.environ)
    env.update(DOUBAO_DATA_DIR=str(paths.DATA), DOUBAO_HOST="127.0.0.1", DOUBAO_PORT=str(paths.PORTS["canvas"]),
               DOUBAO_WORKER_PORT=str(paths.PORTS["worker"]), ONEKEY_DIR=str(paths.ONEKEY), PYTHONUTF8="1")
    return env


def start_all(gpu_index: int | None) -> None:
    env = dict(os.environ)
    env.update(PYTHONUTF8="1", CUDA_DEVICE_ORDER="PCI_BUS_ID")
    if gpu_index is not None:
        env["CUDA_VISIBLE_DEVICES"] = str(gpu_index)
    (paths.RUNTIME / "comfy-input").mkdir(exist_ok=True)
    extra = []
    # settings.json 里的 reserve_vram：给别的程序留出显存（GB），也能用来在大显卡上模拟小显卡
    reserve = paths.load(paths.SETTINGS, {}).get("reserve_vram")
    if reserve:
        extra = ["--reserve-vram", str(float(reserve))]
    _spawn("comfyui", [str(paths.COMFY_PY), "main.py", "--listen", "127.0.0.1", "--port", str(paths.PORTS["comfy"]),
                       "--extra-model-paths-config", str(paths.RUNTIME / "extra_model_paths.yaml"),
                       "--output-directory", str(paths.OUTPUT / "comfyui"),
                       "--input-directory", str(paths.RUNTIME / "comfy-input"),
                       "--disable-auto-launch", *extra], paths.COMFY_DIR, env)
    py = sys.executable
    _spawn("worker", [py, "onekey/serve_local.py", "worker"], paths.CANVAS, canvas_env())
    _spawn("canvas", [py, "onekey/serve_local.py"], paths.CANVAS, canvas_env())


def restart_canvas() -> None:
    """装了新模型后让画布重新登记模型列表（不动 ComfyUI）。"""
    stop("canvas")
    _spawn("canvas", [sys.executable, "onekey/serve_local.py"], paths.CANVAS, canvas_env())


def stop(name: str) -> None:
    p = procs.pop(name, None)
    if p and p.poll() is None:
        subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True, creationflags=NO_WINDOW)


def stop_all() -> None:
    for name in list(procs):
        stop(name)


def health() -> dict:
    out = {}
    for name, url in (("comfyui", f"http://127.0.0.1:{paths.PORTS['comfy']}/system_stats"),
                      ("canvas", f"http://127.0.0.1:{paths.PORTS['canvas']}/"),
                      ("worker", f"http://127.0.0.1:{paths.PORTS['worker']}/api/health")):
        p = procs.get(name)
        running = bool(p and p.poll() is None)
        ok = False
        if running:
            try:
                ok = httpx.get(url, timeout=2, trust_env=False).status_code < 500
            except httpx.HTTPError:
                ok = False
        out[name] = {"running": running, "ok": ok}
    return out


def tail(name: str, n: int = 60) -> str:
    try:
        with open(LOGS / f"{name}.log", encoding="utf-8", errors="replace") as f:
            return "".join(f.readlines()[-n:])
    except OSError:
        return ""
