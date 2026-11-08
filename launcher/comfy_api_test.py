"""Read-only, uncached API diagnostics using saved ComfyUI address/authentication."""
from concurrent.futures import ThreadPoolExecutor
import time

import httpx

import paths

ENDPOINTS = (("健康接口", "/system_stats", 10), ("节点清单", "/object_info", 60),
             ("任务队列", "/queue", 10), ("历史查询", "/history/canvas-onekey-api-test", 10))


def check_endpoint(url: str, label: str, endpoint: str, timeout: int) -> dict:
    started = time.monotonic()
    result = {"name": label, "path": endpoint, "ok": False, "status_code": None, "detail": ""}
    try:
        with httpx.Client(timeout=httpx.Timeout(timeout, connect=3), trust_env=False, follow_redirects=False,
                          auth=paths.auth_for(url), headers=paths.auth_headers_for(url)) as client:
            response = client.get(url + endpoint)
        result["status_code"] = response.status_code
        if not response.is_success:
            result["detail"] = ("认证失败或无权限，请检查认证类型和凭据" if response.status_code in (401, 403) else
                                "收到重定向，请检查本机原生 API 地址；不会跟随跳转" if response.is_redirect else
                                "接口不存在，请检查端口是否指向 ComfyUI" if response.status_code == 404 else
                                "服务返回错误，请检查 ComfyUI / 代理日志")
        else:
            data = response.json()
            valid = isinstance(data, dict)
            if endpoint == "/system_stats":
                valid = valid and isinstance(data.get("system"), dict)
            elif endpoint == "/object_info":
                valid = valid and "KSampler" in data and all(isinstance(v, dict) for v in data.values())
            elif endpoint == "/queue":
                valid = valid and all(isinstance(data.get(k), list) for k in ("queue_running", "queue_pending"))
            result["ok"] = valid
            if not valid:
                result["detail"] = "JSON 结构不符合 ComfyUI 接口，可能连接到了代理 / Portal"
            elif endpoint == "/object_info":
                result["detail"] = f"可读取 {len(data)} 个节点"
            elif endpoint == "/queue":
                result["detail"] = f"执行中 {len(data['queue_running'])}，排队中 {len(data['queue_pending'])}"
            else:
                result["detail"] = "接口响应正常"
    except httpx.TimeoutException:
        result["detail"] = f"请求超时（连接上限 3 秒，读取上限 {timeout} 秒）；服务可能繁忙或正在加载模型"
    except httpx.ConnectError:
        result["detail"] = "无法建立连接，请检查端口监听、Supervisor 状态及是否发生重启"
    except httpx.HTTPError as e:
        result["detail"] = f"通信异常（{type(e).__name__}），请检查服务 / 代理日志"
    except ValueError:
        result["detail"] = "返回内容不是 JSON，可能是网页或认证代理响应"
    # Never return response bodies, headers, credentials, prompts or model lists.
    result["elapsed_ms"] = round((time.monotonic() - started) * 1000)
    return result


def run() -> dict:
    url = paths.comfy_url()
    auth = paths.comfy_auth.status(paths.ONEKEY, url)
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(check_endpoint, url, *entry) for entry in ENDPOINTS]
        checks = [future.result() for future in futures]
    return {"url": url, "auth_type": auth["type"] if auth["enabled"] else "none",
            "ok": all(check["ok"] for check in checks), "checks": checks,
            "elapsed_ms": round((time.monotonic() - started) * 1000),
            "note": "仅测试已保存配置的只读 API，不提交 / 中断任务，不验证上传、生成或下载；单次可达不保证后续无断连。"}
