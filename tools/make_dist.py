"""打分发包：只打程序和说明，不打 runtime/ 模型/ data/ 输出/（这些是每台电脑自己生成的，data/ 里还有你的画板）。

用法：python tools/make_dist.py   → 生成 ../画布一键包_分发/画布一键包.zip
"""
from __future__ import annotations

import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT.parent / "画布一键包_分发"
SKIP_TOP = {"runtime", "模型", "data", "输出"}
SKIP_ANY = {"__pycache__", ".git", ".pytest_cache"}


def main() -> None:
    OUT.mkdir(exist_ok=True)
    target = OUT / "画布一键包.zip"
    n = 0
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for p in sorted(ROOT.rglob("*")):
            rel = p.relative_to(ROOT)
            if not p.is_file() or rel.parts[0] in SKIP_TOP or SKIP_ANY & set(rel.parts) or p.suffix == ".pyc":
                continue
            z.write(p, Path("画布一键包") / rel)
            n += 1
    print(f"{target}  {n} 个文件  {target.stat().st_size / 1e6:.1f}MB")


if __name__ == "__main__":
    main()
