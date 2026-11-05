"""安装 ComfyUI 和下载模型。全部在后台线程里跑，进度给网页轮询。"""
from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
import zipfile
from pathlib import Path

import httpx

import catalog
import comfy_detect
import paths
import workflows
from downloader import Progress, download

progress = Progress()
log_lines: list[str] = []
job = {"running": False, "error": "", "stage": "", "stop": threading.Event()}


def log(msg: str) -> None:
    line = time.strftime("%H:%M:%S ") + msg
    log_lines.append(line)
    del log_lines[:-400]
    try:
        with open(paths.ONEKEY / "install.log", "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def hf_base() -> str:
    return "https://hf-mirror.com" if paths.load(paths.SETTINGS, {}).get("mirror") else "https://huggingface.co"


def file_url(fid: str) -> str:
    f = catalog.FILES[fid]
    return f"{hf_base()}/{f['repo']}/resolve/main/{f['path']}"


def run(cmd: list[str], what: str) -> None:
    log(what)
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=paths.pip_env(), text=True,
                         encoding="utf-8", errors="replace")
    for line in p.stdout:
        line = line.rstrip()
        if line:
            log("  " + line[:300])
    if p.wait() != 0:
        raise RuntimeError(f"{what}失败（退出码 {p.returncode}），详见日志")


# ———————— ComfyUI ————————

def comfy_ready() -> bool:
    detected = comfy_detect.discover()
    if detected["running"]:
        return True
    st = paths.load(paths.STATE, {})
    comfy = st.get("comfy", {})
    return (bool(comfy.get("ok")) and comfy.get("python") == str(paths.COMFY_PY)
            and comfy.get("directory", str(paths.COMFY_DIR)) == str(paths.comfy_dir())
            and paths.COMFY_PY.exists() and (paths.comfy_dir() / "main.py").exists())


def python_has(code: str) -> bool:
    try:
        return subprocess.run([str(paths.COMFY_PY), "-c", code], env=paths.pip_env(),
                              capture_output=True, timeout=30).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def install_comfy(torch_idx: str) -> None:
    root = paths.comfy_dir()
    if comfy_detect.discover()["running"]:
        log("复用正在运行的 ComfyUI，不重复安装其 Python / PyTorch")
        return
    job["stage"] = "下载 ComfyUI"
    if not (root / "main.py").exists():
        if root != paths.COMFY_DIR:
            raise RuntimeError("指定的 ComfyUI 目录无效，不会覆盖该目录；请修正路径")
        log(f"下载 ComfyUI {paths.COMFY_VERSION}")
        zpath = paths.RUNTIME / f"ComfyUI-{paths.COMFY_VERSION}.zip"
        download(paths.COMFY_ZIP, zpath, progress, "ComfyUI 程序")
        tmp = paths.RUNTIME / "ComfyUI.tmp"
        shutil.rmtree(tmp, ignore_errors=True)
        with zipfile.ZipFile(zpath) as z:
            z.extractall(tmp)
        inner = next(tmp.iterdir())
        os.replace(inner, root)
        shutil.rmtree(tmp, ignore_errors=True)
        zpath.unlink(missing_ok=True)
    pip = [str(paths.COMFY_PY), "-m", "pip", "install"]
    job["stage"] = "安装 PyTorch（系统 Python）"
    if python_has("import torch, torchvision, torchaudio; assert torch.version.cuda is not None"):
        log("系统 Python 已有 CUDA PyTorch，保留现有版本")
    else:
        run([*pip, "torch", "torchvision", "torchaudio",
             "--index-url", f"https://download.pytorch.org/whl/{torch_idx}"], f"安装 PyTorch（{torch_idx}，约 3GB，要等几分钟）")
    job["stage"] = "安装 ComfyUI 依赖"
    run([*pip, "-r", str(root / "requirements.txt")],
        "安装 ComfyUI 依赖")
    write_extra_paths()
    st = paths.load(paths.STATE, {})
    st["comfy"] = {"ok": True, "version": paths.COMFY_VERSION, "torch": torch_idx,
                   "python": str(paths.COMFY_PY), "directory": str(root)}
    paths.save(paths.STATE, st)
    log("ComfyUI 装好了")


GGUF_NODE = "https://github.com/city96/ComfyUI-GGUF/archive/6ea2651e7df66d7585f6ffee804b20e92fb38b8a.zip"


def ensure_gguf_node() -> None:
    """GGUF 压缩版 H3 要 ComfyUI-GGUF 插件（city96，固定版本）。只在装了 GGUF 档时才装。"""
    detected = comfy_detect.discover()
    if "UnetLoaderGGUF" in detected["runtime"].get("nodes", {}):
        log("运行中的 ComfyUI 已加载 GGUF 插件，跳过安装")
        return
    root = paths.comfy_dir()
    if not (root / "main.py").exists():
        raise RuntimeError("找不到 ComfyUI 安装目录，无法补装 GGUF 插件；请先设置目录")
    target = root / "custom_nodes" / "ComfyUI-GGUF"
    if not (target / "nodes.py").exists():
        if target.exists():
            raise RuntimeError("已有 ComfyUI-GGUF 目录不完整，请自行检查；不会覆盖现有插件")
        log("安装 ComfyUI-GGUF 插件（GGUF 压缩版 H3 要用）")
        zpath = paths.RUNTIME / "ComfyUI-GGUF.zip"
        download(GGUF_NODE, zpath, progress, "ComfyUI-GGUF 插件")
        tmp = paths.RUNTIME / "ComfyUI-GGUF.tmp"
        shutil.rmtree(tmp, ignore_errors=True)
        with zipfile.ZipFile(zpath) as z:
            z.extractall(tmp)
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(next(tmp.iterdir()), target)
        shutil.rmtree(tmp, ignore_errors=True)
        zpath.unlink(missing_ok=True)
    if not python_has("import gguf, sentencepiece, google.protobuf; from importlib.metadata import version; "
                      "assert tuple(map(int, version('gguf').split('.')[:2])) >= (0, 13)"):
        run([str(paths.COMFY_PY), "-m", "pip", "install", "gguf>=0.13.0", "sentencepiece", "protobuf"],
            "安装 GGUF 依赖")


def write_extra_paths() -> None:
    m = paths.MODELS.as_posix()
    # unet 也指到 diffusion_models：ComfyUI-GGUF 从 unet 目录里找 .gguf
    text = (f"onekey:\n    base_path: {m}\n    diffusion_models: diffusion_models\n    unet: diffusion_models\n"
            "    text_encoders: text_encoders\n    vae: vae\n    loras: loras\n    clip: text_encoders\n")
    (paths.RUNTIME / "extra_model_paths.yaml").write_text(text, encoding="utf-8")


# ———————— 模型 ————————

def model_path(fid: str) -> Path:
    return paths.MODELS / catalog.dest_of(fid)


def file_done(fid: str) -> bool:
    p = model_path(fid)
    if p.exists() and abs(p.stat().st_size - catalog.FILES[fid]["size"]) < max(0.02 * catalog.FILES[fid]["size"], 5e6):
        return True
    detected = comfy_detect.discover()
    return bool(comfy_detect.api_model(fid, detected["runtime"].get("nodes", {}))
                or comfy_detect.local_model(fid, Path(detected["directory"])))


def model_name(fid: str) -> str:
    detected = comfy_detect.discover()
    api_name = comfy_detect.api_model(fid, detected["runtime"].get("nodes", {}))
    if api_name:
        return api_name
    root = Path(detected["directory"])
    local = comfy_detect.local_model(fid, root)
    kind = catalog.dest_of(fid).split("/", 1)[0]
    if local:
        for category, folder in comfy_detect.model_roots(root):
            base = folder if category == kind else folder / kind if not category else None
            if base and local.is_relative_to(base):
                return str(local.relative_to(base))
            if not category and kind == "text_encoders" and local.is_relative_to(folder / "clip"):
                return str(local.relative_to(folder / "clip"))
    return catalog.name_of(fid)


def expose_model(fid: str) -> None:
    """已有实例不读取本项目的 extra paths 时，用不覆盖的单文件链接补齐。"""
    src = model_path(fid)
    if not src.is_file():
        return
    dst = paths.comfy_dir() / "models" / catalog.dest_of(fid)
    if dst.exists():
        if abs(dst.stat().st_size - catalog.FILES[fid]["size"]) >= max(0.02 * catalog.FILES[fid]["size"], 5e6):
            raise RuntimeError(f"已有同名模型大小不符，不会覆盖：{dst}")
        return
    if dst.is_symlink():
        raise RuntimeError(f"已有失效模型链接，不会覆盖：{dst}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.symlink_to(src.resolve())


def import_existing(fid: str, folders: list[str]) -> bool:
    """在已有的 ComfyUI models 目录里找同名文件，找到就硬链接（同一个盘）或复制过来，省得重下。"""
    rel = catalog.dest_of(fid)
    base = rel.split("/", 1)[1]
    for folder in folders:
        root = Path(folder)
        for cand in (root / rel, root / rel.split("/", 1)[0] / Path(base).name, root / "clip" / Path(base).name):
            if cand.is_file() and abs(cand.stat().st_size - catalog.FILES[fid]["size"]) < max(0.02 * catalog.FILES[fid]["size"], 5e6):
                dst = model_path(fid)
                dst.parent.mkdir(parents=True, exist_ok=True)
                try:
                    os.link(cand, dst)
                    log(f"已从现有目录链接：{Path(base).name}")
                except OSError:
                    log(f"从现有目录复制：{Path(base).name}（{cand.stat().st_size / 1e9:.1f}GB）")
                    shutil.copyfile(cand, dst)
                progress.set(Path(base).name, state="done", done=dst.stat().st_size, total=dst.stat().st_size, speed=0)
                return True
    return False


def install_packs(pack_ids: list[str], gpu: dict, ram: float, import_dirs: list[str]) -> None:
    packs = {p["id"]: p for p in catalog.packs_for(gpu, ram)}
    files: list[str] = []
    for pid in pack_ids:
        p = packs.get(pid)
        if not p or not p["enabled"]:
            raise RuntimeError(f"这张显卡不能装：{pid}")
        files += [f for f in p["files"] if f not in files]
    job["stage"] = "下载模型"
    write_extra_paths()
    if any(catalog.dest_of(f).endswith(".gguf") for f in files):
        ensure_gguf_node()
    log(f"要下载 {len(files)} 个文件，共约 {catalog.total_size(files) / 1e9:.1f}GB")
    for fid in files:
        if job["stop"].is_set():
            raise RuntimeError("已暂停")
        name = Path(catalog.dest_of(fid)).name
        if file_done(fid):
            expose_model(fid)
            progress.set(name, state="done", done=catalog.FILES[fid]["size"], total=catalog.FILES[fid]["size"], speed=0)
            continue
        if import_dirs and import_existing(fid, import_dirs):
            expose_model(fid)
            continue
        log(f"下载 {name}（约 {catalog.FILES[fid]['size'] / 1e9:.2f}GB）")
        download(file_url(fid), model_path(fid), progress, name, stop=job["stop"])
        if job["stop"].is_set():
            raise RuntimeError("已暂停，下次点「开始安装」会接着下")
        expose_model(fid)
    st = paths.load(paths.STATE, {})
    st["packs"] = sorted(set(st.get("packs", [])) | set(pack_ids))
    st["gpu"] = gpu
    paths.save(paths.STATE, st)
    write_installed(gpu, ram)
    log("模型都下好了")


def write_installed(gpu: dict, ram: float) -> None:
    """告诉画布接入层：装了哪些模型、具体文件名是什么。只写确实已经下完的文件。"""
    st = paths.load(paths.STATE, {})
    have = set(st.get("packs", []))
    packs = {p["id"]: p for p in catalog.packs_for(gpu, ram)}
    n = model_name

    def pick(pid, prefix):
        return next((f for f in packs.get(pid, {}).get("files", []) if f.startswith(prefix) and file_done(f)), None)

    out: dict = {"comfy": paths.comfy_url()}
    if "qwen" in have and pick("qwen", "qwen_turbo"):
        base_ok = file_done("qwen_base")
        out["qwen"] = {"turbo": n("qwen_turbo"), "base": n("qwen_base") if base_ok else None,
                       "te": n(pick("qwen", "qwen_te")), "vae": n("qwen_vae"),
                       "canon": n("lora_canon") if "canon" in have and file_done("lora_canon") else None,
                       "film": n("lora_film") if "film" in have and file_done("lora_film") else None,
                       "fix": n("lora_fix") if file_done("lora_fix") else None}
    if "moss" in have and pick("moss", "moss_unet"):
        out["moss"] = {"unet": n("moss_unet"), "te": n(pick("moss", "moss_te")), "vae": n("moss_vae")}
    h3 = {}
    for pid, variant in (("h3_fl", "fl2va"), ("h3_ref", "ref2va")):
        if pid in have:
            files = packs.get(pid, {}).get("files", [])
            unet = next((f for f in files if f.endswith(("_pruned", "_full", "_q3", "_q4", "_q5")) and file_done(f)), None)
            turbo = next((f for f in files if f.endswith("_turbo") and file_done(f)), None)
            if unet:
                # .gguf 要用 ComfyUI-GGUF 的加载节点，接入层按这两个标记换节点
                h3[variant] = {"unet": n(unet), "turbo": n(turbo) if turbo else None,
                               "gguf": catalog.dest_of(unet).endswith(".gguf")}
                te = next(f for f in files if f.startswith("h3_te_"))
                h3["te"], h3["te_gguf"] = n(te), catalog.dest_of(te).endswith(".gguf")
                h3["video_vae"], h3["audio_vae"] = n("h3_video_vae"), n("h3_audio_vae")
    if h3.get("fl2va") or h3.get("ref2va"):
        out["h3"] = h3
    paths.save(paths.INSTALLED, out)
    if (paths.comfy_dir() / "main.py").exists():
        for filename in workflows.export(out, paths.comfy_dir()):
            log("补充 API 工作流：" + filename)


def start_job(fn, *args) -> bool:
    if job["running"]:
        return False
    job.update(running=True, error="", stage="准备")
    job["stop"].clear()

    def runner():
        try:
            fn(*args)
            job["stage"] = "完成"
        except Exception as e:  # noqa: BLE001
            job["error"] = str(e)
            log("出错：" + str(e))
        finally:
            job["running"] = False

    threading.Thread(target=runner, daemon=True).start()
    return True


def check_network() -> dict:
    """看看 HuggingFace 官方站能不能连上，连不上就建议用国内镜像。"""
    res = {}
    for name, url in (("huggingface", "https://huggingface.co/api/models/Comfy-Org/Qwen-Image-2.1"),
                      ("hf-mirror", "https://hf-mirror.com/api/models/Comfy-Org/Qwen-Image-2.1"),
                      ("github", "https://github.com/comfyanonymous/ComfyUI")):
        t = time.monotonic()
        try:
            r = httpx.get(url, timeout=8, follow_redirects=True)
            res[name] = {"ok": r.status_code < 400, "ms": int((time.monotonic() - t) * 1000)}
        except httpx.HTTPError:
            res[name] = {"ok": False, "ms": None}
    return res
