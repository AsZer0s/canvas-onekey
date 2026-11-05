"""只读发现本机 ComfyUI、运行时节点和模型；不启动或终止用户进程。"""
from __future__ import annotations

import os
import socket
import time
from pathlib import Path

import httpx
import yaml

import catalog
import paths

_cache: dict = {}


def port_open(url: str) -> bool:
    from urllib.parse import urlsplit
    u = urlsplit(url)
    try:
        with socket.create_connection((u.hostname, u.port or 80), timeout=0.5):
            return True
    except OSError:
        return False


def valid_install(root: Path) -> bool:
    return (root / "main.py").is_file() and (root / "folder_paths.py").is_file()


def process_installs() -> list[dict]:
    """从同一用户可读的 /proc 命令行发现 main.py 和实际监听端口。"""
    found = []
    proc = Path("/proc")
    try:
        entries = list(proc.iterdir())
    except OSError:
        return found
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            if hasattr(os, "getuid") and entry.stat().st_uid != os.getuid():
                continue
            args = (entry / "cmdline").read_bytes().decode("utf-8", errors="replace").strip("\0").split("\0")
            script = next((a for a in args[1:] if Path(a).name == "main.py"), None)
            if not script:
                continue
            cwd = (entry / "cwd").resolve(strict=True)
            root = (cwd / script).resolve().parent
            if not valid_install(root):
                continue
            port = 8188
            for i, arg in enumerate(args):
                if arg == "--port" and i + 1 < len(args):
                    port = int(args[i + 1])
                elif arg.startswith("--port="):
                    port = int(arg.split("=", 1)[1])
            if not 1 <= port <= 65535:
                continue
            found.append({"directory": str(root), "url": f"http://127.0.0.1:{port}", "pid": int(entry.name)})
        except (OSError, ValueError, RuntimeError):
            continue
    return found


def common_installs() -> list[Path]:
    home = Path.home()
    candidates = [paths.comfy_dir(), paths.COMFY_DIR]
    for parent in (paths.ROOT, paths.ROOT.parent, home, home / "AI", home / "ai", home / "apps",
                   Path("/opt"), Path("/srv"), Path("/workspace"), Path("/root")):
        candidates.extend(parent / name for name in ("ComfyUI", "comfyui"))
    # 只查有限的常见目录，不遍历整块硬盘或 NAS。
    return list(dict.fromkeys(p.expanduser().resolve() for p in candidates if valid_install(p)))


def probe(url: str) -> dict:
    try:
        with httpx.Client(timeout=2, trust_env=False, follow_redirects=False) as client:
            stats = client.get(url + "/system_stats")
            stats.raise_for_status()
            if not isinstance(stats.json(), dict) or "system" not in stats.json():
                return {}
            info = client.get(url + "/object_info")
            info.raise_for_status()
            nodes = info.json()
            if not isinstance(nodes, dict) or "KSampler" not in nodes or any(not isinstance(v, dict) for v in nodes.values()):
                return {}
            return {"stats": stats.json(), "nodes": nodes}
    except (httpx.HTTPError, ValueError):
        return {}


def discover(force: bool = False) -> dict:
    key = (str(paths.comfy_dir()), paths.comfy_url())
    if not force and _cache.get("key") == key and time.monotonic() - _cache.get("time", 0) < 10:
        return _cache["value"]
    processes = process_installs()
    directories = list(dict.fromkeys([Path(p["directory"]) for p in processes] + common_installs()))
    urls = list(dict.fromkeys([paths.comfy_url()] + [p["url"] for p in processes]))
    instances = []
    runtime = {}
    for url in urls[:16]:
        result = probe(url)
        if not result:
            continue
        proc = next((p for p in processes if p["url"] == url), {})
        instances.append({"url": url, "directory": proc.get("directory", ""), "pid": proc.get("pid")})
        runtime[url] = result
    settings = paths.load(paths.SETTINGS, {})
    explicit = bool(settings.get("comfy_dir") or os.environ.get("COMFYUI_DIR"))
    explicit_url = bool(settings.get("comfy_url") or os.environ.get("COMFYUI_URL"))
    chosen_dir = paths.comfy_dir()
    chosen_url = paths.comfy_url()
    selected = next((i for i in instances if i["url"] == chosen_url), None)
    if not explicit_url and not selected:
        selected = next((i for i in instances if not explicit or i["directory"] == str(chosen_dir)), None)
    if selected:
        chosen_url = selected["url"]
        if selected["directory"] and not explicit:
            chosen_dir = Path(selected["directory"])
        elif not selected["directory"] and not explicit:
            # 无法把 API 与磁盘目录对应时，不猜测某个已安装目录就是该实例。
            chosen_dir = paths.COMFY_DIR
    elif not explicit and directories:
        chosen_dir = directories[0]
    # 指定目录与已知运行实例不一致时不接错实例。
    mismatch = bool(selected and explicit and selected["directory"] and selected["directory"] != str(chosen_dir))
    value = {"directories": [str(p) for p in directories], "instances": instances,
             "directory": str(chosen_dir), "url": chosen_url, "running": bool(selected) and not mismatch,
             "existing": valid_install(chosen_dir), "mismatch": mismatch,
             "directory_known": bool(not selected or selected["directory"] or explicit),
             "runtime": runtime.get(chosen_url, {}) if not mismatch else {}}
    _cache.update(key=key, time=time.monotonic(), value=value)
    return value


def select() -> dict:
    """在用户点击安装/启动时固定检测结果，避免后续请求切换到另一个实例。"""
    result = discover(force=True)
    if result["mismatch"]:
        raise ValueError("配置的目录和运行中的 ComfyUI 不是同一个实例，请修正目录或地址")
    if result["running"] and not result["directory_known"]:
        raise ValueError("已发现运行中的 ComfyUI，但无法识别磁盘目录，请填写实际安装目录")
    settings = paths.load(paths.SETTINGS, {})
    settings.update(comfy_dir=result["directory"], comfy_url=result["url"])
    paths.save(paths.SETTINGS, settings)
    return result


def model_roots(root: Path) -> list[tuple[str, Path]]:
    """读取已有 models/ 和 extra_model_paths*.yaml，不改写用户配置。"""
    roots = [("", root / "models"), ("", paths.MODELS)]
    for config in [root / "extra_model_paths.yaml", paths.RUNTIME / "extra_model_paths.yaml"]:
        try:
            data = yaml.safe_load(config.read_text(encoding="utf-8")) or {}
            for group in data.values():
                if not isinstance(group, dict):
                    continue
                base = Path(str(group.get("base_path") or root)).expanduser()
                if not base.is_absolute():
                    base = config.parent / base
                for kind, values in group.items():
                    if kind in {"base_path", "is_default"} or not isinstance(values, str):
                        continue
                    kind = {"clip": "text_encoders", "unet": "diffusion_models"}.get(kind, kind)
                    roots.extend((kind, base / v.strip()) for v in values.splitlines() if v.strip())
        except (OSError, ValueError, yaml.YAMLError, AttributeError):
            continue
    return list(dict.fromkeys(roots))


def local_model(fid: str, root: Path) -> Path | None:
    kind, relative = catalog.dest_of(fid).split("/", 1)
    for category, folder in model_roots(root):
        candidates = ([folder / relative, folder / Path(relative).name] if category == kind else
                      [folder / kind / relative, folder / kind / Path(relative).name] if not category else [])
        if not category and kind == "text_encoders":
            candidates.append(folder / "clip" / Path(relative).name)
        for candidate in candidates:
            try:
                size = candidate.stat().st_size
                expected = catalog.FILES[fid]["size"]
                if candidate.is_file() and abs(size - expected) < max(expected * 0.02, 5e6):
                    return candidate
            except OSError:
                continue
    return None


def api_model_names(nodes: dict, fid: str) -> list[str]:
    kind = catalog.dest_of(fid).split("/", 1)[0]
    loaders = {"diffusion_models": [("UNETLoader", "unet_name"), ("UnetLoaderGGUF", "unet_name")],
               "text_encoders": [("CLIPLoader", "clip_name"), ("CLIPLoaderGGUF", "clip_name")],
               "vae": [("VAELoader", "vae_name")], "loras": [("LoraLoaderModelOnly", "lora_name")]}
    names = []
    for node, field in loaders.get(kind, []):
        for section in ("required", "optional"):
            value = nodes.get(node, {}).get("input", {}).get(section, {}).get(field, [])
            if value and isinstance(value[0], list):
                names.extend(n for n in value[0] if isinstance(n, str))
    return names


def api_model(fid: str, nodes: dict) -> str | None:
    relative = catalog.name_of(fid).replace("\\", "/")
    names = api_model_names(nodes, fid)
    exact = next((n for n in names if n.replace("\\", "/") == relative), None)
    if exact:
        return exact
    matches = [n for n in names if n.replace("\\", "/").rsplit("/", 1)[-1] == relative.rsplit("/", 1)[-1]]
    return matches[0] if len(matches) == 1 else None


def audit(pack_ids: list[str], gpu: dict | None, ram: float, force: bool = False) -> dict:
    import workflows
    result = discover(force)
    root = Path(result["directory"])
    nodes = result["runtime"].get("nodes", {})
    packs = {p["id"]: p for p in catalog.packs_for(gpu, ram)}
    files = list(dict.fromkeys(f for pid in pack_ids if pid in packs for f in packs[pid]["files"]))
    expected = workflows.expected_install(pack_ids, gpu, ram)
    required = workflows.required_nodes(expected)
    missing_nodes = [n for n in required if n not in nodes] if result["running"] else []
    models = [{"id": fid, "name": catalog.name_of(fid), "present": bool(api_model(fid, nodes) or local_model(fid, root)),
               "visible": bool(api_model(fid, nodes)), "verified": "filename_only" if api_model(fid, nodes) else "local_size"}
              for fid in files]
    needs_gguf = any(catalog.dest_of(f).endswith(".gguf") for f in files)
    plugin = {"name": "ComfyUI-GGUF", "required": needs_gguf,
              "installed": (root / "custom_nodes" / "ComfyUI-GGUF" / "nodes.py").is_file(),
              "loaded": "UnetLoaderGGUF" in nodes}
    templates = [{"name": name, "present": (workflows.directory(root) / f"{name}.api.json").is_file()}
                 for name in workflows.templates(expected)]
    public = {k: v for k, v in result.items() if k != "runtime"}
    public.update(models=models, plugin=plugin, workflows=templates, required_nodes=required,
                  missing_nodes=missing_nodes,
                  core_missing=[n for n in missing_nodes if n not in {"UnetLoaderGGUF", "CLIPLoaderGGUF"}],
                  occupied=not result["running"] and port_open(result["url"]))
    return public
