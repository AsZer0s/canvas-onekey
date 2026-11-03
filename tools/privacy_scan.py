"""打包前的隐私扫描：在要分发的文件里找局域网 IP、服务器路径、用户名、邮箱、密钥等。

只扫会被打进分发包的文件（不扫第一次运行时才生成的 runtime/ 模型/ data/ 输出/）。
用法：python tools/privacy_scan.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {"runtime", "模型", "data", "输出", "dist", "__pycache__", ".git"}
SKIP_FILES = {"privacy_scan.py"}
BINARY = {".exe", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".ico", ".woff", ".woff2", ".ttf", ".mp3", ".mp4", ".zip", ".pyc"}

RULES = [
    ("局域网/内网 IP", re.compile(r"\b(?:192\.168|10\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}\b")),
    ("Tailscale IP", re.compile(r"\b100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3}\b")),
    ("服务器 IP 尾号", re.compile(r"\b100\.(?:34|4)\b(?![\d.])")),
    ("服务器路径", re.compile(r"/mnt/data8t|/home/lz|qingzi-canvas|comfyui-h3-motion|h3-daily", re.I)),
    ("SSH", re.compile(r"\bssh\s+\S+@|id_ed25519|id_rsa", re.I)),
    ("用户名/主机名", re.compile(r"\blz@|\b9950x\b|chutes-miner|laozhang", re.I)),
    ("本机路径", re.compile(r"[A-Z]:\\\\?(?:cc|Users)\\\\?", re.I)),
    ("邮箱", re.compile(r"[\w.+-]+@[\w-]+\.(?:com|net|org|cn|io|ai)\b", re.I)),
    ("钱包/私钥", re.compile(r"\b0x[0-9a-fA-F]{40,64}\b|wallet|mnemonic|private[_ ]?key", re.I)),
    ("令牌/密钥", re.compile(r"\b(?:sk-[A-Za-z0-9]{20,}|hf_[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16})\b")),
    ("NSFW/无审查", re.compile(r"nsfw|uncensored|hmnsfw|无审查", re.I)),
]

# 上游源码里本来就有、和用户无关的内容（逐条说明原因，不是一刀切放行）
ALLOW = [
    (re.compile(r"canvas/.*"), "邮箱", re.compile(r"example\.com|@users\.noreply\.github\.com")),
    (re.compile(r"canvas/(backend|frontend|scripts|docs)/.*"), "局域网/内网 IP", re.compile(r"192\.168\.\d+\.\d+|10\.0\.0\.\d+|172\.16\.\d+\.\d+")),
    (re.compile(r"使用说明\.md|launcher/web/index\.html|launcher/catalog\.py|tools/.*"), "NSFW/无审查", re.compile(r".")),
    (re.compile(r"使用说明\.md"), "本机路径", re.compile(r".")),
    # 上游 canvas.js 自带的剪贴板格式名（上游项目名 QingZi），不是服务器路径
    (re.compile(r"canvas/frontend/js/canvas\.js"), "服务器路径", re.compile(r"qingzi-canvas-nodes")),
    # 上游分享包导出时用来剔除密钥字段的变量名，本身不是密钥
    (re.compile(r"canvas/backend/share_archive\.py"), "钱包/私钥", re.compile(r"PRIVATE_KEYS")),
]


def allowed(rel: str, rule: str, text: str) -> bool:
    return any(p.fullmatch(rel) and r == rule and t.search(text) for p, r, t in ALLOW)


def main() -> int:
    hits = []
    scanned = 0
    for path in ROOT.rglob("*"):
        if not path.is_file() or path.suffix.lower() in BINARY or path.name in SKIP_FILES:
            continue
        rel = path.relative_to(ROOT).as_posix()
        if rel.split("/", 1)[0] in SKIP_DIRS or any(part in SKIP_DIRS for part in path.relative_to(ROOT).parts):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        scanned += 1
        for lineno, line in enumerate(text.splitlines(), 1):
            for name, rx in RULES:
                for m in rx.finditer(line):
                    if not allowed(rel, name, line):
                        hits.append((name, rel, lineno, line.strip()[:160]))
    print(f"扫描了 {scanned} 个文本文件（不含 runtime/ 模型/ data/ 输出/）")
    by = {}
    for h in hits:
        by.setdefault(h[0], []).append(h)
    for name, _ in RULES:
        rows = by.get(name, [])
        print(f"\n[{name}] {len(rows)} 处")
        for _, rel, lineno, line in rows[:40]:
            print(f"  {rel}:{lineno}  {line}")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
