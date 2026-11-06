"""Deploy a conditional runtime plugin without editing existing ComfyUI source."""
from pathlib import Path

SOURCE = Path(__file__).resolve().parent / "comfy_plugins" / "canvas_onekey_memfd" / "__init__.py"


def plugin_file(root: Path) -> Path:
    return root / "custom_nodes" / "canvas_onekey_memfd" / "__init__.py"


def status(root: Path) -> dict:
    try:
        source = (root / "comfy" / "anonymous_file.py").read_text(encoding="utf-8")
    except OSError:
        source = ""
    return {"installed": plugin_file(root).is_file(), "legacy_call": "os.memfd_create(" in source}


def ensure(root: Path) -> bool:
    if not (root / "main.py").is_file():
        raise RuntimeError("无法安装 memfd 兼容插件：ComfyUI 目录无效")
    target = plugin_file(root)
    content = SOURCE.read_bytes()
    if target.exists():
        if target.read_bytes() != content:
            raise RuntimeError(f"memfd 兼容插件已有不同内容，不会覆盖，请先检查并备份：{target}")
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("xb") as f:
        f.write(content)
    return True
