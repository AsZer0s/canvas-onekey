"""一键包的模型清单和推荐规则。

文件名和大小都是 2026-11 在 HuggingFace 上核对过的；下载时还会再从 HF 读一次真实大小。
只收官方或公开的写实风格 LoRA，不收 NSFW / 无审查模型。
"""
from __future__ import annotations

import os

GB = 1024 ** 3

# id -> 文件信息。dest 是放在「模型」目录下的子路径（和 ComfyUI 的 models 目录结构一致）
FILES: dict[str, dict] = {
    # —— MiniMax H3（Comfy-Org 官方）——
    "h3_te_nvfp4": {"repo": "Comfy-Org/MiniMax-H3", "path": "text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors", "size": 15.69e9},
    "h3_te_int8": {"repo": "Comfy-Org/MiniMax-H3", "path": "text_encoders/qwen3vl_32b_minimax_h3_int8_convrot.safetensors", "size": 27.14e9},
    "h3_video_vae": {"repo": "Comfy-Org/MiniMax-H3", "path": "vae/minimax_h3_video_vae_fp16.safetensors", "size": 5.21e9},
    "h3_audio_vae": {"repo": "Comfy-Org/MiniMax-H3", "path": "vae/minimax_h3_audio_vae_fp32.safetensors", "size": 0.61e9},
    "h3_fl_pruned": {"repo": "Comfy-Org/MiniMax-H3", "path": "diffusion_models/minimax_h3_fl2va_pruned_int8_convrot.safetensors", "size": 20.97e9},
    "h3_fl_full": {"repo": "Comfy-Org/MiniMax-H3", "path": "diffusion_models/minimax_h3_fl2va_int8_convrot.safetensors", "size": 34.04e9},
    "h3_fl_turbo": {"repo": "Comfy-Org/MiniMax-H3", "path": "loras/minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors", "size": 1.96e9},
    "h3_ref_pruned": {"repo": "Comfy-Org/MiniMax-H3", "path": "diffusion_models/minimax_h3_ref2va_pruned_int8_convrot.safetensors", "size": 20.97e9},
    "h3_ref_full": {"repo": "Comfy-Org/MiniMax-H3", "path": "diffusion_models/minimax_h3_ref2va_int8_convrot.safetensors", "size": 34.04e9},
    "h3_ref_turbo": {"repo": "Comfy-Org/MiniMax-H3", "path": "loras/minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors", "size": 1.96e9},
    # —— MiniMax H3 GGUF 压缩版（12～23GB 显存用；社区量化，沿用 MiniMax H3 官方许可）——
    # 视频主模型用 Abiray 的剪枝 GGUF（作者写明给 ComfyUI-GGUF 用）。文本编码器仍用 Comfy-Org 官方版：
    # unsloth 的 GGUF 文本编码器是给 stable-diffusion.cpp 用的，ComfyUI-GGUF 读不了（实测报 incompatible with llama.cpp）
    "h3_fl_q3": {"repo": "Abiray/MiniMax-H3-Pruned-GGUF", "path": "MiniMax-H3-FL2VA-Pruned-Q3_K_M.gguf", "dest": "diffusion_models/MiniMax-H3-FL2VA-Pruned-Q3_K_M.gguf", "size": 8.90e9},
    "h3_fl_q4": {"repo": "Abiray/MiniMax-H3-Pruned-GGUF", "path": "MiniMax-H3-FL2VA-Pruned-Q4_K_M.gguf", "dest": "diffusion_models/MiniMax-H3-FL2VA-Pruned-Q4_K_M.gguf", "size": 11.56e9},
    "h3_fl_q5": {"repo": "Abiray/MiniMax-H3-Pruned-GGUF", "path": "MiniMax-H3-FL2VA-Pruned-Q5_K_M.gguf", "dest": "diffusion_models/MiniMax-H3-FL2VA-Pruned-Q5_K_M.gguf", "size": 14.07e9},
    "h3_ref_q3": {"repo": "Abiray/MiniMax-H3-Pruned-GGUF", "path": "MiniMax-H3-Ref2VA-Pruned-Q3_K_M.gguf", "dest": "diffusion_models/MiniMax-H3-Ref2VA-Pruned-Q3_K_M.gguf", "size": 8.90e9},
    "h3_ref_q4": {"repo": "Abiray/MiniMax-H3-Pruned-GGUF", "path": "MiniMax-H3-Ref2VA-Pruned-Q4_K_M.gguf", "dest": "diffusion_models/MiniMax-H3-Ref2VA-Pruned-Q4_K_M.gguf", "size": 11.56e9},
    "h3_ref_q5": {"repo": "Abiray/MiniMax-H3-Pruned-GGUF", "path": "MiniMax-H3-Ref2VA-Pruned-Q5_K_M.gguf", "dest": "diffusion_models/MiniMax-H3-Ref2VA-Pruned-Q5_K_M.gguf", "size": 14.07e9},
    # —— Qwen-Image 2.1（Comfy-Org 官方）——
    "qwen_turbo": {"repo": "Comfy-Org/Qwen-Image-2.1", "path": "diffusion_models/qwen_image_2.1_turbo_int8_convrot.safetensors", "size": 7.26e9},
    "qwen_base": {"repo": "Comfy-Org/Qwen-Image-2.1", "path": "diffusion_models/qwen_image_2.1_int8_convrot.safetensors", "size": 7.26e9},
    "qwen_te_int8": {"repo": "Comfy-Org/Qwen-Image-2.1", "path": "text_encoders/qwen3vl_8b_int8_convrot.safetensors", "size": 9.35e9},
    "qwen_te_w4a8": {"repo": "Comfy-Org/Qwen-Image-2.1", "path": "text_encoders/qwen3vl_8b_w4a8.safetensors", "size": 6.31e9},
    "qwen_vae": {"repo": "Comfy-Org/Qwen-Image-2.1", "path": "vae/qwen_image_2.1_vae_bf16.safetensors", "size": 0.68e9},
    # —— 写实风格 LoRA（社区作者，按其仓库条款使用）——
    "lora_canon": {"repo": "Danrisi/canon_qwen2.1", "path": "canon_qwen21.safetensors", "dest": "loras/qwen21_style/canon_qwen21.safetensors", "size": 0.08e9},
    "lora_film": {"repo": "Danrisi/filmstills_qwen2.1", "path": "filmstills_qwen21.safetensors", "dest": "loras/qwen21_style/filmstills_qwen21.safetensors", "size": 0.08e9},
    "lora_fix": {"repo": "e-n-v-y/Qwen-Image-2.1-Fix-v2.0", "path": "qwen2.1-detail-fix-2.0.safetensors", "dest": "loras/qwen21_style/qwen2.1-detail-fix-2.0.safetensors", "size": 0.05e9},
    # —— MOSS-Image（Comfy-Org 官方，SFT 版，不带任何附加 LoRA）——
    "moss_unet": {"repo": "Comfy-Org/MOSS-Image", "path": "diffusion_models/moss_image_sft_int8_convrot.safetensors", "size": 5.41e9},
    "moss_te_w4a8": {"repo": "Comfy-Org/MOSS-Image", "path": "text_encoders/qwen3.5_9b_w4a8_gptq.safetensors", "size": 7.34e9},
    "moss_te_int8": {"repo": "Comfy-Org/MOSS-Image", "path": "text_encoders/qwen3.5_9b_int8_convrot.safetensors", "size": 9.75e9},
    "moss_vae": {"repo": "Comfy-Org/MOSS-Image", "path": "vae/flux2-vae_small.safetensors", "size": 0.25e9},
}


def dest_of(file_id: str) -> str:
    f = FILES[file_id]
    return f.get("dest") or f["path"]


def name_of(file_id: str) -> str:
    """ComfyUI 里引用的文件名（相对于 diffusion_models / loras 等子目录）。"""
    # Windows 上 ComfyUI 列子目录里的文件用反斜杠（qwen21_style\canon_qwen21.safetensors），要原样对上
    return dest_of(file_id).split("/", 1)[1].replace("/", os.sep)


def url_of(file_id: str) -> str:
    f = FILES[file_id]
    return f"https://huggingface.co/{f['repo']}/resolve/main/{f['path']}"


def gpu_family(cc: float) -> str:
    if cc >= 12.0:
        return "blackwell"
    if cc >= 8.9:
        return "ada"
    if cc >= 8.0:
        return "ampere"
    return "old"


def packs_for(gpu: dict | None, ram_gb: float) -> list[dict]:
    """按显卡给出可选功能包。每个包：id、名字、说明、文件、是否推荐、是否可选。"""
    # nvidia-smi 报的显存比标称略少（5090 标称 32GB，读出来 31.8GB），按标称档位比较时留 0.6GB 余量
    vram = (gpu or {}).get("vram_gb", 0) + 0.6
    fam = gpu_family((gpu or {}).get("cc", 0))
    blackwell = fam == "blackwell"
    small = vram < 16
    packs: list[dict] = []

    def add(pid, name, desc, files, recommended, enabled=True, warn=""):
        packs.append({"id": pid, "name": name, "desc": desc, "files": files,
                      "recommended": bool(recommended and enabled), "enabled": enabled, "warn": warn})

    if not gpu:
        return packs
    old_warn = "这张卡架构较老（20 系或更早），int8 量化模型没有验证过，可能跑不起来。" if fam == "old" else ""

    # Qwen 图片：12GB 起
    qwen_te = "qwen_te_w4a8" if small else "qwen_te_int8"
    add("qwen", "Qwen 2.1 图片（文生图 / 多图参考编辑）",
        "画布默认的生图模型，8 步 Turbo，一张约 1 分钟以内。" + ("显存小于 16GB，文本编码器用 W4A8 省显存版。" if small else ""),
        ["qwen_turbo", qwen_te, "qwen_vae"], vram >= 12, vram >= 12,
        old_warn or ("" if vram >= 12 else "显存小于 12GB，跑不动 Qwen 2.1。"))
    add("qwen_fine", "Qwen 精细 40 步（标准版底模）",
        "40 步慢一些，但更听光线和视线的描述，皮肤更自然。「单反写实」「电影剧照」也要它。",
        ["qwen_base"], vram >= 16, vram >= 12)
    add("canon", "单反写实 40 步",
        "Danrisi canon LoRA 0.7 + 细节修复 LoRA 0.8：最自然的写实照片感，适合白天戏和定妆。",
        ["lora_canon", "lora_fix", "qwen_base"], vram >= 16, vram >= 12)
    add("film", "电影剧照 40 步",
        "Danrisi filmstills LoRA 0.7 + 细节修复 LoRA 0.8：偏冷、胶片电影感，适合夜戏和氛围镜头。",
        ["lora_film", "lora_fix", "qwen_base"], vram >= 16, vram >= 12)
    add("moss", "MOSS-Image 文生图（官方 SFT 版）",
        "另一种画风的文生图模型，只能文字生图，不接参考图。",
        ["moss_unet", "moss_te_w4a8" if blackwell else "moss_te_int8", "moss_vae"], False, vram >= 16,
        "" if vram >= 16 else "显存小于 16GB，不建议装 MOSS。")

    # H3 视频：24GB 起
    h3_te = "h3_te_nvfp4" if blackwell else "h3_te_int8"
    h3_common = [h3_te, "h3_video_vae", "h3_audio_vae"]
    ram_warn = "" if ram_gb >= 64 else f"内存只有 {ram_gb:.0f}GB，H3 的文本编码器很大，建议 64GB 以上内存，否则可能很慢或报内存不足。"
    te_note = "50 系显卡用 NVFP4 文本编码器（15.7GB）。" if blackwell else "这张卡不是 50 系，不能用 NVFP4，文本编码器用 int8 版（27GB，更吃内存）。"
    full = vram >= 48
    if vram < 12:
        add("h3_fl", "H3 首帧/文生视频（FL2VA）", "显存小于 12GB，连压缩版 H3 也跑不动。", [], False, False,
            "至少要 12GB 显存。")
    elif vram < 24:
        # 12～23GB：视频主模型用 GGUF 压缩版；文本编码器用官方版（每条只跑一次，跑完就挪出显存）
        q = "q3" if vram < 16 else "q4" if vram < 20 else "q5"
        qname = {"q3": "Q3_K_M（8.9GB）", "q4": "Q4_K_M（11.6GB）", "q5": "Q5_K_M（14.1GB）"}[q]
        gguf_common = h3_common
        gguf_warn = ("GGUF 压缩版：能跑，但比 24GB 以上显卡用的 int8 版慢很多，动作和人脸也会差一些。"
                     "先用「快速 Turbo」档、5 秒试；生成前关掉微信、剪映、浏览器等占显存的程序。"
                     + ("" if ram_gb >= (32 if blackwell else 48) else f"内存只有 {ram_gb:.0f}GB，建议 {32 if blackwell else 48}GB 以上（文本编码器要先放进内存），否则容易报内存不足。"))
        add("h3_fl", f"H3 首帧/文生视频（FL2VA）·GGUF 压缩版 {q.upper()}",
            f"给 12～23GB 显存用的压缩版：视频主模型 {qname}。" + te_note + "含 Turbo 8 步加速 LoRA。会自动装 ComfyUI-GGUF 插件。",
            [f"h3_fl_{q}", "h3_fl_turbo", *gguf_common], vram >= 16, True, gguf_warn)
        add("h3_ref", f"H3 参考图生视频（Ref2VA）·GGUF 压缩版 {q.upper()}",
            "连 1～7 张参考图生成镜头。和 FL2VA 共用文本编码器和 VAE。含 Turbo 4 步 LoRA。",
            [f"h3_ref_{q}", "h3_ref_turbo", *gguf_common], False, True, gguf_warn)
    else:
        fl = "h3_fl_full" if full else "h3_fl_pruned"
        warn24 = "" if vram >= 32 else "24GB 显存是下限：建议先用「快速8步 Turbo」、5 秒，可能显存不足。"
        add("h3_fl", "H3 首帧/文生视频（FL2VA）" + ("·完整 int8" if full else "·剪枝 int8"),
            ("完整 int8 版（34GB），画质最好。" if full else "剪枝 int8 版（21GB），单张 5090 验证过能出片。") + te_note + "含 Turbo 8 步加速 LoRA。",
            [fl, "h3_fl_turbo", *h3_common], True, True, " ".join(x for x in (warn24, ram_warn, old_warn) if x))
        ref = "h3_ref_full" if full else "h3_ref_pruned"
        add("h3_ref", "H3 参考图生视频（Ref2VA）" + ("·完整 int8" if full else "·剪枝 int8"),
            "连 1～7 张人物 / 场景参考图生成镜头。和 FL2VA 共用文本编码器和 VAE。含 Turbo 4 步 LoRA。",
            [ref, "h3_ref_turbo", *h3_common], vram >= 32, True, " ".join(x for x in (warn24, ram_warn) if x))
    return packs


def total_size(file_ids) -> float:
    return sum(FILES[f]["size"] for f in set(file_ids))
