"""显卡、内存、磁盘自检（只读，不改任何系统设置）。"""
from __future__ import annotations

import ctypes
import shutil
import subprocess
from pathlib import Path


def nvidia_gpus() -> tuple[list[dict], str]:
    """返回 (显卡列表, 出错说明)。用 nvidia-smi 读型号、显存、计算能力、驱动版本。"""
    exe = shutil.which("nvidia-smi") or r"C:\Windows\System32\nvidia-smi.exe"
    if not Path(exe).exists() and not shutil.which("nvidia-smi"):
        return [], "没找到 nvidia-smi：这台电脑可能没有 NVIDIA 显卡，或者没装显卡驱动。"
    try:
        out = subprocess.run(
            [exe, "--query-gpu=index,name,memory.total,compute_cap,driver_version,memory.used", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as e:  # noqa: BLE001
        return [], f"运行 nvidia-smi 失败：{e}"
    if out.returncode != 0:
        return [], "nvidia-smi 报错：" + (out.stderr or out.stdout).strip()[:300]
    gpus = []
    for line in out.stdout.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 5:
            continue
        try:
            gpus.append({"index": int(parts[0]), "name": parts[1], "vram_gb": round(int(parts[2]) / 1024, 1),
                         "cc": float(parts[3]), "driver": parts[4],
                         "used_gb": round(int(parts[5]) / 1024, 1) if len(parts) > 5 and parts[5].isdigit() else None})
        except ValueError:
            continue
    return gpus, "" if gpus else "nvidia-smi 没有返回显卡信息。"


def ram_gb() -> float:
    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("sullAvailExtendedVirtual", ctypes.c_ulonglong)]
    try:
        m = MEMORYSTATUSEX()
        m.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
        return round(m.ullTotalPhys / 1024 ** 3, 1)
    except Exception:  # noqa: BLE001
        return 0.0


def disk_free_gb(path: Path) -> float:
    try:
        return round(shutil.disk_usage(path).free / 1024 ** 3, 1)
    except OSError:
        return 0.0


def is_network_path(path: Path) -> bool:
    """一键包在 NAS / 网络共享上（含 pushd 临时映射的盘符）时返回 True。"""
    s = str(path)
    if s.startswith("\\\\"):
        return True
    try:
        return ctypes.windll.kernel32.GetDriveTypeW(s[:3]) == 4  # DRIVE_REMOTE
    except Exception:  # noqa: BLE001
        return False


def torch_index(driver: str) -> tuple[str | None, str]:
    """按驱动版本选 PyTorch 的 CUDA 版本。返回 (索引名, 说明)。"""
    try:
        major = int(str(driver).split(".")[0])
    except ValueError:
        return "cu128", "读不到驱动版本，先按 CUDA 12.8 装。"
    if major >= 580:
        return "cu130", ""
    if major >= 570:
        return "cu128", "驱动较旧（低于 580），PyTorch 用 CUDA 12.8 版。"
    return None, f"显卡驱动版本 {driver} 太旧，请先到 NVIDIA 官网更新到 570 以上，再重新打开一键包。"


def pick_gpu(gpus: list[dict]) -> dict | None:
    return max(gpus, key=lambda g: g["vram_gb"]) if gpus else None
