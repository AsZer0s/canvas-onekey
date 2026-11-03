"""一键包的画布入口：上游画布 + /local 本机模型接入层，只监听本机 127.0.0.1。

由启动器调用：python onekey/serve_local.py           画布（10027）
              python onekey/serve_local.py worker    任务服务（8089）
"""
from __future__ import annotations

import ipaddress
import json
import os
import sys
from pathlib import Path

CANVAS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(CANVAS))
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.chdir(CANVAS)
os.environ.setdefault("DOUBAO_HOST", "127.0.0.1")
os.environ.setdefault("DOUBAO_PORT", "10027")

import uvicorn  # noqa: E402

# 画布里显示的本地模型（只放已经装好的）
VIDEO_RATIOS = ["16:9", "9:16", "1:1", "4:3", "3:4", "21:9"]
CHANNELS = {
    "qwen": {"kind": "image", "model": "qwen-image21-local", "name": "Qwen 2.1 图片｜本机 · 最多4图参考编辑",
             "max_images": 4, "ratios": ["自动", "16:9", "9:16", "1:1", "4:3", "3:4"], "is_default": True},
    "moss": {"kind": "image", "model": "moss-image-local", "name": "MOSS-Image｜本机 · 文生图",
             "max_images": 0, "ratios": ["自动", "16:9", "9:16", "1:1", "4:3", "3:4"]},
    "fl2va": {"kind": "video", "model": "h3-fl-int8-local", "name": "H3 首帧/文生视频（FL2VA）｜本机",
              "max_images": 2, "duration_min": 4, "duration_max": 15, "ratios": VIDEO_RATIOS,
              "resolutions": ["均衡20步", "细化30步", "快速8步 Turbo（768档）"], "is_default": True},
    "ref2va": {"kind": "video", "model": "h3-ref-int8-local", "name": "H3 参考图生视频（Ref2VA）｜本机",
               "max_images": 7, "duration_min": 4, "duration_max": 15, "ratios": VIDEO_RATIOS,
               "resolutions": ["均衡20步", "细化30步", "快速4步 Turbo（544档）"]},
}


def seed_channels() -> None:
    from backend import app_users, myapi_store, service_settings
    from backend.user_scope import set_current_user_id

    user = app_users.local_operator()
    set_current_user_id(user["id"])
    cfg = service_settings.load()
    cfg.update(points_enabled=False, max_threads=1, upscale_enabled=False)
    service_settings.save(cfg)
    onekey = Path(os.environ["ONEKEY_DIR"])
    try:
        inst = json.loads((onekey / "installed.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        inst = {}
    have = {"qwen": bool(inst.get("qwen")), "moss": bool(inst.get("moss")),
            "fl2va": bool((inst.get("h3") or {}).get("fl2va")), "ref2va": bool((inst.get("h3") or {}).get("ref2va"))}
    token = (onekey / "adapter.token").read_text(encoding="utf-8").strip()
    port = os.environ["DOUBAO_PORT"]
    common = {"base_url": f"http://127.0.0.1:{port}/local", "api_key": token, "api_style": "generic",
              "max_videos": 0, "max_audios": 0, "send_duration": True, "send_size": True, "is_default": False}
    for key, row in CHANNELS.items():
        mid = "myapi_" + row["model"].replace("-", "_")
        if have[key]:
            myapi_store.upsert(row["kind"], {**common, **row, "id": mid})
        else:
            myapi_store.delete(mid)


class LoopbackOnly:
    """只让本机访问：一键包不开局域网，避免别人连进来用你的显卡。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            host = (scope.get("client") or ("0.0.0.0", 0))[0]
            try:
                ok = ipaddress.ip_address(host).is_loopback
            except ValueError:
                ok = False
            if not ok:
                from starlette.responses import PlainTextResponse
                return await PlainTextResponse("只允许本机访问", status_code=403)(scope, receive, send)
        await self.app(scope, receive, send)


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "worker":
        from backend.config import WORKER_PORT
        from backend.job_worker.app import worker_app
        from backend.log_setup import setup_file_logging

        setup_file_logging(name="worker")
        uvicorn.run(LoopbackOnly(worker_app), host="127.0.0.1", port=WORKER_PORT, log_level="info")
        return
    from backend.main import app as canvas
    from bridge import local, recover_interrupted

    recover_interrupted()
    seed_channels()
    canvas.mount("/local", local)
    uvicorn.run(LoopbackOnly(canvas), host="127.0.0.1", port=int(os.environ["DOUBAO_PORT"]), log_level="info")


if __name__ == "__main__":
    main()
