"""把本机的一个参考图文件夹导入成画布里的一个画板。

图片通过画布自己的 /api/upload 上传，再新建一个画板（只新增，不改已有画板）。
在画布「选择素材」弹窗左上角切到这个画板，就能按子文件夹浏览、搜索、添加。
"""
from __future__ import annotations

import json
import mimetypes
import random
import string
import time
import uuid
from pathlib import Path

import httpx

import installer
import paths

EXTS = {".png", ".jpg", ".jpeg", ".webp"}
MAX_FILES = 3000
MAX_BYTES = 50 * 1024 * 1024
RATIOS = {"1:1": 1, "16:9": 16 / 9, "9:16": 9 / 16, "4:3": 4 / 3, "3:4": 3 / 4}


def canvas() -> str:
    return f"http://127.0.0.1:{paths.PORTS['canvas']}"


def scan(folder: str) -> list[Path]:
    root = Path(folder.strip().strip('"'))
    if not root.is_dir():
        raise ValueError(f"找不到这个文件夹：{folder}")
    files = sorted(p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in EXTS and p.stat().st_size <= MAX_BYTES)
    if not files:
        raise ValueError("这个文件夹里没有 PNG / JPG / WebP 图片")
    if len(files) > MAX_FILES:
        raise ValueError(f"图片太多（{len(files)} 张），一次最多导入 {MAX_FILES} 张；请分成几个小文件夹分别导入")
    return files


def nearest_ratio(w, h) -> str:
    if not w or not h:
        return "1:1"
    r = w / h
    return min(RATIOS, key=lambda k: abs(RATIOS[k] - r))


def import_folder(folder: str, name: str) -> None:
    root = Path(folder.strip().strip('"'))
    files = scan(folder)
    name = (name or root.name or "参考图库").strip()[:40]
    with httpx.Client(timeout=120, trust_env=False) as c:
        try:
            boards = c.get(canvas() + "/api/canvas").json()["boards"]
        except (httpx.HTTPError, KeyError, ValueError):
            raise RuntimeError("画布没在运行：先在 ③ 点「启动画布」，等状态灯变绿再导入")
        rnd = random.Random()
        ids = {b["id"] for b in boards}
        bid = "ref" + "".join(rnd.choice(string.ascii_lowercase + string.digits) for _ in range(8))
        while bid in ids:
            bid = "ref" + "".join(rnd.choice(string.ascii_lowercase + string.digits) for _ in range(8))
        installer.job["stage"] = f"导入参考图（共 {len(files)} 张）"
        installer.log(f"导入参考图：{root} → 新画板「{name}」，{len(files)} 张")
        nodes, cols, col, row_y, last_group = [], 15, 0, 0, None
        key = "参考图导入"
        for i, f in enumerate(files):
            if installer.job["stop"].is_set():
                raise RuntimeError("已停止导入，还没有建画板；重新点「导入」会从头来")
            rel = f.relative_to(root)
            group = rel.parts[0] if len(rel.parts) > 1 else name
            if group != last_group:
                if last_group is not None:
                    row_y += 800
                last_group, col = group, 0
            elif col >= cols:
                col, row_y = 0, row_y + 400
            mime = mimetypes.guess_type(f.name)[0] or "image/png"
            r = c.post(canvas() + "/api/upload", data={"board_id": bid},
                       files={"file": (uuid.uuid4().hex + f.suffix.lower(), f.read_bytes(), mime)})
            if r.status_code >= 400:
                installer.log(f"  跳过（上传失败）：{rel}")
                continue
            u = r.json()
            nodes.append({
                "id": "".join(rnd.choice(string.ascii_lowercase + string.digits) for _ in range(8)),
                "type": "ref-image", "x": col * 280, "y": row_y, "prompt": "", "resultText": None,
                "ratio": nearest_ratio(u.get("width"), u.get("height")), "model": "myapi_qwen_image21_local",
                "duration": None, "resolution": None, "style": None, "channel": "myapi", "accountId": "",
                "taskId": None, "status": "completed", "videoUrl": None, "imageUrl": u["url"], "audioUrl": None,
                "coverUrl": None, "thumbUrl": u.get("thumb_url"), "mediaWidth": u.get("width"),
                "mediaHeight": u.get("height"), "error": None, "softRefIds": [],
                # 「前缀｜名字」：前缀就是素材弹窗里的小文件夹
                "customName": f"{group}｜{'/'.join(rel.with_suffix('').parts[1:]) or f.stem}"[:80],
            })
            col += 1
            installer.progress.set(key, state="downloading", done=i + 1, total=len(files), speed=0)
        board = {"id": bid, "name": name, "nodes": nodes, "connections": [], "createdAt": int(time.time() * 1000),
                 "pinned": False, "viewport": {"x": 40, "y": 40, "scale": 0.25}}
        r = c.patch(canvas() + "/api/canvas", content=json.dumps({"boards": [board]}, ensure_ascii=False).encode("utf-8"),
                    headers={"Content-Type": "application/json"})
        if r.status_code >= 400:
            raise RuntimeError("建画板失败：" + r.text[:300])
    installer.progress.set(key, state="done", done=len(files), total=len(files), speed=0)
    installer.log(f"导入完成：画板「{name}」{len(nodes)} 张。刷新画布后，在「选择素材」左上角切到这个画板")


def pick_folder() -> str:
    """在这台电脑上弹出「选择文件夹」对话框（管理页只在本机打开，所以对话框就出现在用户面前）。"""
    import tkinter
    from tkinter import filedialog

    tk = tkinter.Tk()
    tk.withdraw()
    tk.attributes("-topmost", True)
    try:
        return filedialog.askdirectory(title="选择参考图文件夹") or ""
    finally:
        tk.destroy()
