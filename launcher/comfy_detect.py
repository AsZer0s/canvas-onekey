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
    """从可读的 /proc 命令行发现 main.py 和实际端口（包含 Supervisor 子进程）。"""
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
            args = (entry / "cmdline").read_bytes().decode("utf-8", errors="replace").strip("\0").split("\0")
            script = next((a for a in args[1:] if Path(a).name == "main.py"), None)
            if not script and any(a == "-m" and args[i + 1:i + 2] == ["main"] for i, a in enumerate(args)):
                script = "main.py"
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
    """独立检查轻量健康接口和可能很慢的节点清单；保留部分成功及失败原因。"""
    result = {"api_ready": False, "nodes_ready": False, "stats": {}, "nodes": {}, "diagnostics": []}
    with httpx.Client(timeout=httpx.Timeout(10, connect=3), trust_env=False, follow_redirects=False,
                      auth=paths.auth_for(url)) as client:
        for endpoint, key, read_timeout in (("/system_stats", "stats", 10), ("/object_info", "nodes", 60)):
            try:
                response = client.get(url + endpoint, timeout=httpx.Timeout(read_timeout, connect=3))
                if not response.is_success:
                    hint = ("需要认证或凭据错误，请配置 ComfyUI Basic Auth，或使用可访问的本机原生 API 地址" if response.status_code in (401, 403) else
                            "发生重定向，请填写本机原生 API 地址（不会自动跟随跳转）" if response.is_redirect else
                            "接口不存在，请检查是否填写了 portal / 代理端口而非 ComfyUI 原生端口" if response.status_code == 404 else
                            "请查看 Supervisor 中 ComfyUI 的启动日志")
                    result["diagnostics"].append(f"{endpoint}: HTTP {response.status_code}；{hint}")
                    continue
                data = response.json()
                valid = (isinstance(data, dict) and (isinstance(data.get("system"), dict) if key == "stats" else
                         "KSampler" in data and all(isinstance(v, dict) for v in data.values())))
                if not valid:
                    result["diagnostics"].append(f"{endpoint}: 返回的 JSON 不是预期的 ComfyUI 数据，请检查 API 地址")
                    continue
                result[key] = data
                result["api_ready" if key == "stats" else "nodes_ready"] = True
            except httpx.TimeoutException:
                result["diagnostics"].append(f"{endpoint}: 请求超时（读取上限 {read_timeout} 秒），服务可能仍在加载插件")
            except httpx.ConnectError:
                result["diagnostics"].append(f"{endpoint}: 连接失败，请检查监听地址、端口和 Supervisor 日志")
                # 连接都无法建立时不必再等一次相同地址。
                break
            except httpx.HTTPError as e:
                result["diagnostics"].append(f"{endpoint}: HTTP 通信失败（{type(e).__name__}）")
            except ValueError:
                result["diagnostics"].append(f"{endpoint}: 返回内容不是 JSON，可能连接到了网页 / 代理而非 ComfyUI API")
    return result


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
        runtime[url] = result
        proc = next((p for p in processes if p["url"] == url), {})
        if result.get("api_ready") or result.get("nodes_ready") or result.get("stats") or result.get("nodes") or proc:
            instances.append({"url": url, "directory": proc.get("directory", ""), "pid": proc.get("pid"),
                              "process_running": bool(proc),
                              "api_ready": result.get("api_ready", bool(result.get("stats"))),
                              "nodes_ready": result.get("nodes_ready", bool(result.get("nodes")))})
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
    current = runtime.get(chosen_url, {}) if not mismatch else {}
    api_ready = current.get("api_ready", bool(current.get("stats"))) or current.get("nodes_ready", bool(current.get("nodes")))
    occupied = port_open(chosen_url)
    value = {"directories": [str(p) for p in directories], "instances": instances,
             "directory": str(chosen_dir), "url": chosen_url, "running": bool(selected) and not mismatch,
             "process_running": bool(selected and selected["pid"]) and not mismatch,
             "api_ready": api_ready, "nodes_ready": current.get("nodes_ready", bool(current.get("nodes"))),
             "occupied": occupied,
             "diagnostics": current.get("diagnostics", []),
             "existing": valid_install(chosen_dir), "mismatch": mismatch,
             "directory_known": bool(not selected or selected["directory"] or explicit),
             "runtime": current}
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
    missing_nodes = [n for n in required if n not in nodes] if result.get("nodes_ready", bool(nodes)) else []
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
                   occupied=result.get("occupied", port_open(result["url"])))
    return public
