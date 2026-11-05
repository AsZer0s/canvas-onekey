#!/usr/bin/env bash
set -euo pipefail

cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
if [[ "$(uname -s)" != Linux ]]; then
    echo "此启动脚本仅支持 Linux。" >&2
    exit 1
fi

PYTHON="${PYTHON:-python3}"
if ! command -v "$PYTHON" >/dev/null 2>&1; then
    echo "找不到系统 Python，请先安装 Python 3.12+ 和 python3-pip。" >&2
    exit 1
fi
"$PYTHON" -c 'import sys; sys.exit("需要 Python 3.12 或更新版本（推荐 3.12）。") if sys.version_info < (3, 12) else None'
if ! "$PYTHON" -m pip --version >/dev/null 2>&1; then
    echo "当前 Python 没有 pip，请通过发行版的包管理器安装（例如 sudo apt install python3-pip）。" >&2
    exit 1
fi

export PYTHONUTF8=1
export PIP_CACHE_DIR="$PWD/runtime/pip-cache"
if [[ -f data/onekey/mirror.flag ]]; then
    export PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
fi
echo "使用系统 Python / pip 安装画布依赖（不使用 uv 或虚拟环境）……"
if ! "$PYTHON" -m pip install -r canvas/requirements.txt; then
    echo "依赖安装失败。若提示 externally-managed-environment，请查看 README.md 的系统 pip 说明；脚本不会自动绕过系统保护。" >&2
    exit 1
fi
exec "$PYTHON" launcher/main.py "$@"
