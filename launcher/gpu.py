"""显卡、内存、磁盘自检（只读，不改任何系统设置）。"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path


def nvidia_gpus() -> tuple[list[dict], str]:
    """返回 (显卡列表, 出错说明)。用 nvidia-smi 读型号、显存、计算能力、驱动版本。"""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return [], "没找到 nvidia-smi：这台电脑可能没有 NVIDIA 显卡，或者没装显卡驱动。"
    try:
        out = subprocess.run(
            [exe, "--query-gpu=index,name,memory.total,compute_cap,driver_version,memory.used", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=20,
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
    try:
        return round(os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE") / 1024 ** 3, 1)
    except (OSError, ValueError, AttributeError):
        return 0.0


def disk_free_gb(path: Path) -> float:
    try:
        return round(shutil.disk_usage(path).free / 1024 ** 3, 1)
    except OSError:
        return 0.0


def is_network_path(path: Path) -> bool:
    """按最长匹配的 Linux 挂载点识别 NFS / SMB / SSHFS 等网络盘。"""
    network_fs = {"nfs", "nfs4", "cifs", "smb3", "smbfs", "fuse.sshfs", "9p", "ceph", "glusterfs"}
    resolved = str(path.resolve())
    best_mount, best_fs = "", ""
    try:
        for line in Path("/proc/mounts").read_text(encoding="utf-8").splitlines():
            fields = line.split()
            if len(fields) < 3:
                continue
            mount = re.sub(r"\\([0-7]{3})", lambda m: chr(int(m[1], 8)), fields[1])
            if (resolved == mount or resolved.startswith(mount.rstrip("/") + "/")) and len(mount) > len(best_mount):
                best_mount, best_fs = mount, fields[2]
        return best_fs in network_fs
    except OSError:
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
