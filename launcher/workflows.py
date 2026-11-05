"""从画布实际使用的构图器生成 API 工作流，避免维护两份节点清单。"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import catalog
import paths


def templates(installed: dict) -> dict[str, dict]:
    source = Path(__file__).resolve().parents[1] / "canvas" / "onekey" / "graphs.py"
    spec = importlib.util.spec_from_file_location("onekey_graphs", source)
    graphs = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(graphs)
    result = {}
    if installed.get("qwen"):
        for preset in graphs.QWEN_PRESETS:
            try:
                result[f"qwen-{preset}"] = graphs.qwen(installed, "示例提示词", 1, 1328, 1328, [], preset, "canvas-onekey")
            except ValueError:
                continue
        # 带参考图版本也要检测 LoadImage 节点。
        result["qwen-reference"] = graphs.qwen(installed, "示例提示词", 1, 1328, 1328, ["example.png"], "turbo", "canvas-onekey")
    if installed.get("moss"):
        result["moss"] = graphs.moss(installed, "示例提示词", 1, 1024, 1024, "canvas-onekey")
    for variant in ("fl2va", "ref2va"):
        if not installed.get("h3", {}).get(variant):
            continue
        for preset in ("balanced20", "turbo"):
            try:
                result[f"h3-{variant}-{preset}"] = graphs.h3(
                    installed, variant, "示例提示词", ["example.png"], 5, "16:9", 1, preset, "canvas-onekey")
            except ValueError:
                continue
    return result


def expected_install(pack_ids: list[str], gpu: dict | None, ram: float) -> dict:
    """生成选中功能所需的节点/工作流清单，即使模型尚未下载。"""
    packs = {p["id"]: p for p in catalog.packs_for(gpu, ram)}
    selected = {pid: packs[pid]["files"] for pid in pack_ids if pid in packs and packs[pid]["files"]}
    files = {fid for group in selected.values() for fid in group}
    name = catalog.name_of
    inst = {}
    if "qwen" in selected:
        te = next(f for f in selected["qwen"] if f.startswith("qwen_te"))
        inst["qwen"] = {"turbo": name("qwen_turbo"), "base": name("qwen_base") if "qwen_base" in files else None,
                        "te": name(te), "vae": name("qwen_vae"),
                        **{key: name("lora_" + key) if "lora_" + key in files else None for key in ("canon", "film", "fix")}}
    if "moss" in selected:
        te = next(f for f in selected["moss"] if f.startswith("moss_te"))
        inst["moss"] = {"unet": name("moss_unet"), "te": name(te), "vae": name("moss_vae")}
    for pid, variant in (("h3_fl", "fl2va"), ("h3_ref", "ref2va")):
        if pid not in selected:
            continue
        group = selected[pid]
        unet = next(f for f in group if f.endswith(("_pruned", "_full", "_q3", "_q4", "_q5")))
        te = next(f for f in group if f.startswith("h3_te_"))
        turbo = next((f for f in group if f.endswith("_turbo")), None)
        h3 = inst.setdefault("h3", {})
        h3.update(te=name(te), te_gguf=catalog.dest_of(te).endswith(".gguf"),
                  video_vae=name("h3_video_vae"), audio_vae=name("h3_audio_vae"))
        h3[variant] = {"unet": name(unet), "gguf": catalog.dest_of(unet).endswith(".gguf"),
                       "turbo": name(turbo) if turbo else None}
    return inst


def required_nodes(installed: dict) -> list[str]:
    return sorted({node["class_type"] for graph in templates(installed).values() for node in graph.values()})


def directory(root: Path) -> Path:
    return root / "user" / "default" / "workflows" / "canvas-onekey"


def export(installed: dict, root: Path) -> list[str]:
    """只创建本项目命名空间下缺失的 API 模板，不覆盖用户编辑的工作流。"""
    target = directory(root)
    target.mkdir(parents=True, exist_ok=True)
    written = []
    import json
    for name, graph in templates(installed).items():
        path = target / f"{name}.api.json"
        if path.exists():
            continue
        with path.open("x", encoding="utf-8") as f:
            json.dump(graph, f, ensure_ascii=False, indent=2)
        written.append(str(path))
    readme = target / "README.txt"
    if not readme.exists():
        readme.write_text("这是 ComfyUI /prompt API 工作流模板，不是前端拖入式 UI 工作流。\n"
                          "画布直接调用同一个构图器，不依赖这些示例文件。参考图示例需替换 example.png。\n"
                          "已有文件不会覆盖。修复旧模板请先自行备份并移走旧文件，再重新安装。\n", encoding="utf-8")
    return written
