"""一键包启动器：自检显卡 / 已有 ComfyUI → 补齐依赖 → 启动画布。

管理页 http://127.0.0.1:10020 ，画布 http://127.0.0.1:10027 。都只在本机能打开。
"""
from __future__ import annotations

import argparse
import atexit
import sys
import threading
import time
import webbrowser
from contextlib import asynccontextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import uvicorn  # noqa: E402
from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.responses import FileResponse  # noqa: E402

import catalog  # noqa: E402
import comfy_detect  # noqa: E402
import gpu as gpucheck  # noqa: E402
import installer  # noqa: E402
import paths  # noqa: E402
import refs  # noqa: E402
import services  # noqa: E402


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        yield
    finally:
        # uvicorn 收到 SIGTERM 时也要停止全部子服务，不能只依赖 atexit。
        services.stop_all()


app = FastAPI(title="画布一键包", docs_url=None, redoc_url=None, lifespan=lifespan)
WEB = Path(__file__).resolve().parent / "web"
_check: dict = {}


def system_check(force: bool = False) -> dict:
    if _check and not force:
        return _check
    gpus, err = gpucheck.nvidia_gpus()
    g = gpucheck.pick_gpu(gpus)
    ram = gpucheck.ram_gb()
    torch_idx, torch_note = gpucheck.torch_index(g["driver"]) if g else (None, "")
    ascii_path = str(paths.ROOT).isascii()
    _check.clear()
    _check.update(
        gpus=gpus, gpu=g, error=err, ram_gb=ram, disk_free_gb=gpucheck.disk_free_gb(paths.ROOT),
        family=catalog.gpu_family(g["cc"]) if g else None, torch=torch_idx, torch_note=torch_note,
        root=str(paths.ROOT), ascii_path=ascii_path, network=gpucheck.is_network_path(paths.ROOT),
    )
    return _check


def pack_view() -> list[dict]:
    c = system_check()
    st = paths.load(paths.STATE, {})
    have = set(st.get("packs", []))
    out = []
    for p in catalog.packs_for(c["gpu"], c["ram_gb"]):
        missing = [f for f in p["files"] if not installer.file_done(f)]
        out.append({**p, "installed": p["id"] in have and not missing,
                    "missing": {f: catalog.FILES[f]["size"] for f in missing},
                    "size_gb": round(catalog.total_size(p["files"]) / 1e9, 1),
                    "missing_gb": round(catalog.total_size(missing) / 1e9, 1)})
    return out


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")


@app.get("/api/check")
def api_check(force: bool = False):
    c = system_check(force)
    detected = comfy_detect.discover(force)
    return {**c, "packs": pack_view(), "comfy_ready": installer.comfy_ready(),
            "comfy_files_only": detected["existing"] and detected["directory_known"]
                                and (detected["running"] or detected.get("occupied", False)),
            "comfy": {k: v for k, v in detected.items() if k != "runtime"},
            "services_running": services.health()["comfyui"]["running"],
            "settings": paths.load(paths.SETTINGS, {}), "state": paths.load(paths.STATE, {})}


@app.get("/api/network")
def api_network():
    return installer.check_network()


@app.post("/api/settings")
def api_settings(body: dict):
    if installer.job["running"]:
        raise HTTPException(409, "正在安装或导入，暂时不能修改设置")
    s = paths.load(paths.SETTINGS, {})
    if "comfy_dir" in body or "comfy_url" in body:
        if any(p.poll() is None for p in services.procs.values()):
            raise HTTPException(409, "请先停止本启动器管理的服务，再更换 ComfyUI 设置")
        directory = str(body.get("comfy_dir", s.get("comfy_dir", ""))).strip()
        if directory and not comfy_detect.valid_install(Path(directory).expanduser()):
            raise HTTPException(400, "目录中没有 ComfyUI main.py / folder_paths.py")
        try:
            url = str(body.get("comfy_url", s.get("comfy_url", ""))).strip()
            s["comfy_url"] = paths.validate_comfy_url(url) if url else ""
        except ValueError as e:
            raise HTTPException(400, str(e))
        s["comfy_dir"] = str(Path(directory).expanduser().resolve()) if directory else ""
    if "mirror" in body:
        s["mirror"] = bool(body["mirror"])
        flag = paths.ONEKEY / "mirror.flag"  # 给启动画布.sh 看：装画布依赖时也走清华镜像
        flag.write_text("1") if s["mirror"] else flag.unlink(missing_ok=True)
    if "import_dirs" in body:
        s["import_dirs"] = [str(x).strip() for x in body["import_dirs"] if str(x).strip()][:5]
    paths.save(paths.SETTINGS, s)
    return s


@app.get("/api/comfy/audit")
def api_comfy_audit(packs: str = "", force: bool = False):
    c = system_check()
    ids = [p for p in packs.split(",") if p] if packs else paths.load(paths.STATE, {}).get("packs", [])
    return comfy_detect.audit(ids, c["gpu"], c["ram_gb"], force)


def _install(pack_ids: list[str]) -> None:
    c = system_check()
    detected = comfy_detect.select()
    if not c["torch"]:
        raise RuntimeError(c["torch_note"] or "显卡驱动不满足要求")
    occupied = detected.get("occupied", False)
    files_only = detected["existing"] and detected["directory_known"] and (detected["running"] or occupied)
    if occupied and not files_only:
        raise RuntimeError("API 尚未验证且端口有监听；请先填写有效的 ComfyUI 安装目录，才可补齐磁盘文件")
    if files_only:
        installer.log("复用已有 ComfyUI 目录，仅补齐所选功能的插件 / 模型 / 工作流；不重复启动或修改其 PyTorch")
        for message in detected.get("diagnostics", []):
            installer.log("API 检测提示：" + message)
    elif not installer.comfy_ready():
        installer.install_comfy(c["torch"])
    if detected["running"] and (not detected["existing"] or not detected["directory_known"]):
        raise RuntimeError("已发现运行中的 ComfyUI，但无法确定安装目录；请填写 ComfyUI 目录以补装插件、模型和工作流")
    # 已登记的功能也要一并复检，补齐被误删的模型 / 插件 / 工作流。
    repair_ids = list(dict.fromkeys(pack_ids + paths.load(paths.STATE, {}).get("packs", [])))
    enabled = {p["id"] for p in catalog.packs_for(c["gpu"], c["ram_gb"]) if p["enabled"]}
    repair_ids = [pid for pid in repair_ids if pid in enabled]
    if repair_ids:
        installer.install_packs(repair_ids, c["gpu"], c["ram_gb"], paths.load(paths.SETTINGS, {}).get("import_dirs", []))
    else:
        installer.write_installed(c["gpu"], c["ram_gb"])
    audit = comfy_detect.audit(repair_ids, c["gpu"], c["ram_gb"], force=True)
    if audit["core_missing"]:
        installer.log("当前 ComfyUI 缺少内置节点：" + "、".join(audit["core_missing"]) + "；请自行升级 ComfyUI 后重启，不会覆盖原安装")
    if not audit.get("nodes_ready", False):
        installer.log("磁盘文件补齐完成；节点 API 尚未验证，不能据此判定缺少节点。请检查 ComfyUI 原生端口和 Supervisor 日志，再重新检测")
        for message in audit.get("diagnostics", []):
            installer.log("API 检测提示：" + message)
    elif audit["running"] and (audit["missing_nodes"] or any(not m["visible"] for m in audit["models"])):
        installer.log("补装完成；请手动重启已有 ComfyUI，再点「重新检测」确认节点和模型已加载")
    if any(services.health()[k]["running"] for k in ("canvas",)):
        services.restart_canvas()


@app.post("/api/install")
def api_install(body: dict):
    c = system_check()
    if not c["gpu"]:
        raise HTTPException(400, c["error"] or "没有检测到 NVIDIA 显卡")
    ids = [str(x) for x in body.get("packs", [])]
    allowed = {p["id"] for p in pack_view() if p["enabled"]}
    bad = [x for x in ids if x not in allowed]
    if bad:
        raise HTTPException(400, "这张显卡不能装：" + "、".join(bad))
    repair_ids = set(ids) | set(paths.load(paths.STATE, {}).get("packs", []))
    need = catalog.total_size(list({f for p in pack_view() if p["id"] in repair_ids and p["enabled"]
                                    for f in p["files"] if not installer.file_done(f)})) / 1024 ** 3
    detected = comfy_detect.discover()
    files_only = detected["existing"] and detected["directory_known"] and (detected["running"] or detected.get("occupied", False))
    need += 0 if installer.comfy_ready() or files_only else 8
    if need > c["disk_free_gb"] - 5:
        raise HTTPException(400, f"磁盘空间不够：还要约 {need:.0f}GB，这个盘只剩 {c['disk_free_gb']:.0f}GB")
    if not installer.start_job(_install, ids):
        raise HTTPException(409, "已经在安装了")
    return {"ok": True}


@app.post("/api/refs/pick")
def api_refs_pick():
    try:
        return {"folder": refs.pick_folder()}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"打不开选择文件夹的窗口：{e}；请直接把文件夹路径粘贴到输入框")


@app.post("/api/refs/import")
def api_refs_import(body: dict):
    folder = str(body.get("folder") or "")
    try:
        n = len(refs.scan(folder))
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not services.health()["canvas"]["ok"]:
        raise HTTPException(400, "画布没在运行：先在 ③ 点「启动画布」，等状态灯变绿再导入")
    if not installer.start_job(refs.import_folder, folder, str(body.get("name") or "")):
        raise HTTPException(409, "正在安装或导入，请等它结束")
    return {"ok": True, "count": n}


@app.post("/api/pause")
def api_pause():
    installer.job["stop"].set()
    return {"ok": True}


@app.get("/api/progress")
def api_progress():
    j = installer.job
    return {"running": j["running"], "stage": j["stage"], "error": j["error"],
            "files": installer.progress.snapshot(), "log": installer.log_lines[-80:]}


@app.post("/api/start")
def api_start():
    c = system_check()
    if not installer.comfy_ready():
        raise HTTPException(400, "还没装好 ComfyUI，先点「开始安装」")
    try:
        detected = comfy_detect.select()
        if (detected["running"] or detected.get("occupied")) and not detected.get("nodes_ready", False):
            details = "；".join(detected.get("diagnostics", []))
            raise ValueError("已有实例的节点 API 尚未就绪；不会另开 ComfyUI。请检查原生 API 地址及 Supervisor 日志。" + details)
        if detected["running"] and not detected["directory_known"]:
            raise ValueError("请先填写运行中 ComfyUI 的实际安装目录，避免误写工作流到其他安装")
        audit = comfy_detect.audit(paths.load(paths.STATE, {}).get("packs", []), c["gpu"], c["ram_gb"])
        if audit["running"] and (audit["missing_nodes"] or any(not m["visible"] for m in audit["models"])):
            raise ValueError("ComfyUI 缺少所需节点或尚未加载模型，请先补齐依赖并重启 ComfyUI，再重新检测")
        installer.write_installed(c["gpu"], c["ram_gb"])
        services.start_all(c["gpu"]["index"] if c["gpu"] else None)
    except (ValueError, RuntimeError, OSError) as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@app.post("/api/stop")
def api_stop():
    services.stop_all()
    return {"ok": True}


@app.get("/api/services")
def api_services():
    return {"health": services.health(), "canvas_url": f"http://127.0.0.1:{paths.PORTS['canvas']}/"}


@app.get("/api/log/{name}")
def api_log(name: str):
    if name not in ("comfyui", "canvas", "worker"):
        raise HTTPException(404, "没有这个日志")
    return {"text": services.tail(name, 120)}


def _auto_start(open_browser: bool = False) -> None:
    """自动启动已装好的服务；仅显式指定时打开浏览器。"""
    c = system_check()
    st = paths.load(paths.STATE, {})
    if installer.comfy_ready() and st.get("packs") and c["gpu"]:
        try:
            api_start()
        except HTTPException as e:
            installer.log("自动启动未完成：" + str(e.detail))
            return
        for _ in range(240):
            h = services.health()
            if h["canvas"]["ok"]:
                if open_browser:
                    webbrowser.open(f"http://127.0.0.1:{paths.PORTS['canvas']}/")
                return
            time.sleep(1)
    elif open_browser:
        webbrowser.open(f"http://127.0.0.1:{paths.PORTS['setup']}/")


def _already_running() -> bool:
    import socket
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", paths.PORTS["setup"])) == 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="画布一键包（默认不打开浏览器）")
    parser.add_argument("--browser", action="store_true", help="启动时自动打开浏览器")
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = parse_args()
    if _already_running():
        print(f"一键包已经在运行了，管理页：http://127.0.0.1:{paths.PORTS['setup']}/")
        if args.browser:
            webbrowser.open(f"http://127.0.0.1:{paths.PORTS['setup']}/")
        sys.exit(0)
    atexit.register(services.stop_all)
    print("=" * 60)
    print("  画布一键包已启动。请保持终端运行，按 Ctrl+C 停止全部服务。")
    print(f"  管理页（自检 / 下载模型）：http://127.0.0.1:{paths.PORTS['setup']}")
    print(f"  画布：                    http://127.0.0.1:{paths.PORTS['canvas']}")
    print("=" * 60)
    threading.Thread(target=_auto_start, args=(args.browser,), daemon=True).start()
    uvicorn.run(app, host="127.0.0.1", port=paths.PORTS["setup"], log_level="warning")
