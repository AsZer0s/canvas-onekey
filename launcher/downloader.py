"""断点续传下载。下载中的文件叫 xxx.part，下完核对大小再改名；中途关掉下次接着下。"""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import httpx

CHUNK = 4 * 1024 * 1024
UA = {"User-Agent": "infinite-canvas-onekey/1.0"}


class Progress:
    def __init__(self):
        self.lock = threading.Lock()
        self.items: dict[str, dict] = {}

    def set(self, key, **kw):
        with self.lock:
            self.items.setdefault(key, {}).update(kw)

    def snapshot(self):
        with self.lock:
            return {k: dict(v) for k, v in self.items.items()}


def remote_size(client: httpx.Client, url: str) -> int | None:
    try:
        r = client.head(url, follow_redirects=True, timeout=30)
        if r.status_code < 400:
            # HF 的 LFS 文件在 x-linked-size 里给真实大小
            for h in ("x-linked-size", "content-length"):
                if r.headers.get(h, "").isdigit():
                    return int(r.headers[h])
    except httpx.HTTPError:
        pass
    return None


def download(url: str, target: Path, progress: Progress, key: str, expect: int | None = None,
             stop: threading.Event | None = None, retries: int = 8) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + ".part")
    with httpx.Client(headers=UA, timeout=httpx.Timeout(60, read=120), trust_env=True) as client:
        size = remote_size(client, url) or expect
        if target.exists() and (size is None or target.stat().st_size == size):
            progress.set(key, state="done", done=target.stat().st_size, total=target.stat().st_size, speed=0)
            return
        attempt = 0
        while True:
            have = part.stat().st_size if part.exists() else 0
            if size and have == size:
                break
            if size and have > size:  # 远端文件变了，从头下
                part.unlink()
                have = 0
            progress.set(key, state="downloading", done=have, total=size or 0)
            headers = {"Range": f"bytes={have}-"} if have else {}
            try:
                with client.stream("GET", url, headers=headers, follow_redirects=True) as r:
                    if r.status_code == 416:
                        break
                    if r.status_code >= 400:
                        raise httpx.HTTPStatusError(f"HTTP {r.status_code}", request=r.request, response=r)
                    if have and r.status_code != 206:  # 服务器不支持续传，只能重下
                        have = 0
                    if not size:
                        cl = r.headers.get("content-length")
                        size = int(cl) + have if cl and cl.isdigit() else None
                    t0, last_done = time.monotonic(), have
                    with open(part, "ab" if have else "wb") as f:
                        for chunk in r.iter_bytes(CHUNK):
                            if stop is not None and stop.is_set():
                                progress.set(key, state="paused", speed=0)
                                return
                            f.write(chunk)
                            have += len(chunk)
                            now = time.monotonic()
                            if now - t0 >= 1:
                                progress.set(key, done=have, total=size or 0, speed=(have - last_done) / (now - t0))
                                t0, last_done = now, have
            except (httpx.HTTPError, OSError) as e:
                attempt += 1
                if attempt > retries:
                    progress.set(key, state="failed", error=f"下载失败：{e}", speed=0)
                    raise
                progress.set(key, state="retrying", error=f"网络中断，{min(60, 5 * attempt)} 秒后续传（第 {attempt} 次）", speed=0)
                time.sleep(min(60, 5 * attempt))
                continue
            if size is None or (part.exists() and part.stat().st_size >= size):
                break
            attempt += 1  # 连接提前断开但没报错：接着续传
            if attempt > retries:
                progress.set(key, state="failed", error="连接多次提前断开", speed=0)
                raise RuntimeError(f"{target.name} 下载不完整")
        got = part.stat().st_size
        if size and got != size:
            progress.set(key, state="failed", error=f"大小不对：{got} / {size}")
            raise RuntimeError(f"{target.name} 下载不完整")
        os.replace(part, target)
        progress.set(key, state="done", done=got, total=got, speed=0)
