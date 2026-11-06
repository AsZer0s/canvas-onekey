"""画布 ↔ 本机 ComfyUI 的接入层（挂在画布的 /local 下）。

画布把本地模型当成一个 MyApi 渠道：图片走 /local/v1/images/generations，视频走 /local/v1/videos。
这里把请求拼成 ComfyUI 工作流，交给本机唯一的 ComfyUI，排队一个一个跑（单张显卡），
图片和视频来回切换时先让 ComfyUI 卸载上一个模型，避免两个大模型同时占显存。
只监听本机，必须带内部令牌；不读取任意网址或磁盘文件。
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import math
import os
import re
import secrets
import sqlite3
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

import httpx
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from PIL import Image

import graphs
import comfy_auth

DATA = Path(os.environ["ONEKEY_DIR"])
MEDIA = DATA / "media"
MEDIA.mkdir(parents=True, exist_ok=True)
INSTALLED = DATA / "installed.json"
DB = DATA / "jobs.sqlite3"
CANVAS_PORT = int(os.environ.get("DOUBAO_PORT", "10027"))
TOKEN_FILE = DATA / "adapter.token"
if not TOKEN_FILE.exists():
    TOKEN_FILE.write_text(secrets.token_urlsafe(40), encoding="utf-8")
TOKEN = TOKEN_FILE.read_text(encoding="utf-8").strip()

# 画布里的模型名 → 类型
MODELS = {'qwen-image21-local': 'image', 'moss-image-local': 'moss', 'h3-fl-int8-local': 'fl2va', 'h3-ref-int8-local': 'ref2va'}
FAMILY = {'image': 'image', 'moss': 'moss', 'fl2va': 'video', 'ref2va': 'video'}
RATIOS = ('1:1', '16:9', '9:16', '4:3', '3:4', '21:9')

local = FastAPI(title='本机模型接入层', docs_url=None, redoc_url=None)
GPU = asyncio.Lock()
TASKS: set = set()
last_family = {'v': None}


@local.middleware('http')
async def authenticate(request, call_next):
    # 挂在画布 /local 下时，有的 Starlette 版本 path 里带 /local 前缀，有的不带，两种都要拦
    path = request.scope.get('path', '')
    path = path[len(request.scope.get('root_path', '')):] if path.startswith(request.scope.get('root_path', '') or '\0') else path
    if (path.startswith('/v1/') or path.startswith('/local/v1/')) and not secrets.compare_digest(request.headers.get('Authorization', ''), 'Bearer ' + TOKEN):
        return JSONResponse({'error': {'message': '只接受本机画布任务服务的内部令牌'}}, status_code=401)
    return await call_next(request)


with sqlite3.connect(DB) as c:
    c.execute('CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, request_id TEXT UNIQUE, hash TEXT, kind TEXT, body TEXT)')


def recover_interrupted():
    with sqlite3.connect(DB) as c:
        c.execute("UPDATE jobs SET body=json_set(body,'$.status','failed','$.error','程序重启过，这个任务没有跑完；请重新点生成') "
                  "WHERE json_extract(body,'$.status') IN ('queued','in_progress')")


def fail(message, code=400):
    raise HTTPException(code, message)


def installed() -> dict:
    try:
        return json.loads(INSTALLED.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def comfy() -> str:
    return installed().get('comfy') or 'http://127.0.0.1:8188'


async def call(path, method='GET', timeout=30, **kw):
    try:
        async with httpx.AsyncClient(timeout=timeout, trust_env=False, auth=comfy_auth.client_auth(DATA, comfy()),
                                     headers=comfy_auth.client_headers(DATA, comfy()),
                                     follow_redirects=False) as c:
            r = await c.request(method, comfy() + path, **kw)
    except httpx.HTTPError:
        fail('连不上本机 ComfyUI：请确认一键包的启动窗口还开着，或在管理页点「重新启动」', 502)
    if not r.is_success:
        fail('ComfyUI 拒绝了请求：' + r.text[:1200], 502)
    return r.json() if r.content.strip() else {}  # /free 成功时不返回内容


def row(jid):
    with sqlite3.connect(DB) as c:
        r = c.execute('SELECT body FROM jobs WHERE id=?', (jid,)).fetchone()
    if not r:
        fail('任务不存在', 404)
    return json.loads(r[0])


def update(jid, **values):
    b = row(jid)
    b.update(values)
    with sqlite3.connect(DB) as c:
        c.execute('UPDATE jobs SET body=? WHERE id=?', (json.dumps(b, ensure_ascii=False), jid))
    return b


def public(b):
    return {k: v for k, v in b.items() if k != 'request'}


def dims(b):
    ratio = str(b.get('aspect_ratio') or b.get('ratio') or '')
    if ratio in graphs.QWEN_SIZES:
        return graphs.QWEN_SIZES[ratio]
    size = str(b.get('size') or '')
    if re.fullmatch(r'\d{2,4}x\d{2,4}', size):
        w, h = map(int, size.split('x'))
        w, h = max(256, min(2048, w // 16 * 16)), max(256, min(2048, h // 16 * 16))
        if w * h <= 2048 * 1024:
            return w, h
    return graphs.QWEN_SIZES['1:1']


def validate(b, kind):
    if not isinstance(b, dict):
        fail('参数必须为对象')
    want = MODELS.get(b.get('model'))
    if want is None or (kind == 'image') != (want in ('image', 'moss')):
        fail('未知或不匹配的本地模型')
    prompt = b.get('prompt', '')
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 16000:
        fail('请填写 1～16000 字的提示词')
    if b.get('videos') or b.get('audios'):
        fail('本机模型只接文字和图片，不接视频或音频参考')
    images = b.get('images', [])
    if not isinstance(images, list) or any(not isinstance(x, str) or len(x) > 28 * 1024 * 1024 for x in images):
        fail('参考图格式错误')
    if want == 'moss' and images:
        fail('MOSS-Image 只能文字生图，不接参考图；需要保留人物或场景请切回 Qwen 2.1')
    if want == 'image' and len(images) > graphs.QWEN_MAX_IMAGES:
        fail('Qwen 2.1 最多4张参考图')
    if kind == 'video':
        seconds = float(b.get('seconds', b.get('duration', 5)))
        ratio = str(b.get('aspect_ratio') or b.get('ratio') or '16:9')
        if ratio not in RATIOS:
            fail('视频画幅不支持')
        try:
            graphs.h3_config(want, b.get('quality_preset', 'balanced20'))
        except ValueError as e:
            fail(str(e))
        if not math.isfinite(seconds) or not 4 <= seconds <= 15:
            fail('时长只能是 4～15 秒')
    return want


async def media_bytes(value: str) -> bytes:
    if value.startswith('data:image/') and ';base64,' in value:
        try:
            raw = base64.b64decode(value.split(',', 1)[1], validate=True)
        except ValueError:
            fail('图片 Base64 错误')
    else:
        u = urlparse(value)
        if u.scheme != 'http' or u.hostname not in ('127.0.0.1', 'localhost') or u.port != CANVAS_PORT \
                or not re.fullmatch(r'/local/media/[a-f0-9]{32}\.png', u.path):
            fail('只接受画布上传的图片；不读取任意网址或磁盘文件')
        raw = (MEDIA / u.path.rsplit('/', 1)[1]).read_bytes()
    if len(raw) > 20 * 1024 ** 2:
        fail('参考图最大 20MB')
    try:
        image = Image.open(io.BytesIO(raw))
        image.load()
        image = image.convert('RGB')
        image.thumbnail((2048, 2048))
        out = io.BytesIO()
        image.save(out, format='PNG')
        return out.getvalue()
    except Exception:  # noqa: BLE001
        fail('无法读取参考图片')


async def upload_comfy(value: str) -> str:
    raw = await media_bytes(value)
    async with httpx.AsyncClient(timeout=60, trust_env=False, auth=comfy_auth.client_auth(DATA, comfy()),
                                 headers=comfy_auth.client_headers(DATA, comfy()),
                                 follow_redirects=False) as c:
        r = await c.post(comfy() + '/upload/image', files={'image': ('canvas-' + uuid.uuid4().hex + '.png', raw, 'image/png')},
                         data={'subfolder': 'canvas', 'overwrite': 'false'})
        r.raise_for_status()
        meta = r.json()
    return '/'.join(x for x in (meta.get('subfolder'), meta['name']) if x)


def make_graph(jid, b, mode, uploaded):
    seed = int(b.get('seed', int(jid[:15], 16)))
    inst = installed()
    prefix = f'canvas/{jid}/' + ('image' if mode in ('image', 'moss') else 'video')
    if mode == 'image':
        w, h = dims(b)
        return graphs.qwen(inst, b['prompt'], seed, w, h, uploaded, b.get('quality_preset', 'turbo'), prefix)
    if mode == 'moss':
        w, h = dims(b)
        return graphs.moss(inst, b['prompt'], seed, w, h, prefix)
    return graphs.h3(inst, mode, b['prompt'], uploaded, float(b.get('seconds', b.get('duration', 5))),
                     str(b.get('aspect_ratio') or b.get('ratio') or '16:9'), seed, b.get('quality_preset', 'balanced20'), prefix)


async def execute(jid, b, kind):
    try:
        mode = validate(b, kind)
        async with GPU:
            update(jid, status='in_progress')
            fam = FAMILY[mode]
            if last_family['v'] and last_family['v'] != fam:
                # 图片 ↔ 视频切换：先卸载上一个模型，单张显卡放不下两个
                await call('/free', 'POST', json={'unload_models': True, 'free_memory': True})
            last_family['v'] = fam
            uploaded = [await upload_comfy(x) for x in b.get('images', [])]
            try:
                graph = make_graph(jid, b, mode, uploaded)
            except ValueError as e:
                fail(str(e))
            result = await call('/prompt', 'POST', json={'prompt': graph, 'client_id': 'canvas-' + jid})
            pid = result['prompt_id']
            update(jid, prompt_id=pid)
            deadline = time.monotonic() + 3 * 3600
            while time.monotonic() < deadline:
                item = (await call('/history/' + pid)).get(pid)
                if item:
                    st = item.get('status', {})
                    if st.get('status_str') == 'error':
                        msgs = st.get('messages', [])
                        raise RuntimeError('; '.join(str(v.get('exception_message', v)) for k, v in msgs if k == 'execution_error')[:1600]
                                           or 'ComfyUI 运行出错')
                    entries = []
                    for out in item.get('outputs', {}).values():
                        for key in ('images', 'videos', 'gifs'):
                            entries.extend(out.get(key, []))
                    exts = ('.png', '.jpg', '.webp') if kind == 'image' else ('.mp4', '.webm')
                    entries = [e for e in entries if e.get('filename', '').lower().endswith(exts)]
                    if not entries:
                        raise RuntimeError('ComfyUI 跑完了但没有返回图片或视频')
                    e = entries[0]
                    name = jid + ('.png' if kind == 'image' else '.mp4')
                    async with httpx.AsyncClient(timeout=300, trust_env=False, auth=comfy_auth.client_auth(DATA, comfy()),
                                                 headers=comfy_auth.client_headers(DATA, comfy()),
                                                 follow_redirects=False) as c:
                        r = await c.get(comfy() + '/view', params={k: e[k] for k in ('filename', 'subfolder', 'type') if k in e})
                        r.raise_for_status()
                        (MEDIA / name).write_bytes(r.content)
                    url = f'http://127.0.0.1:{CANVAS_PORT}/local/media/{name}'
                    update(jid, status='completed', url=url, video_url=url if kind == 'video' else None,
                           data=[{'url': url}] if kind == 'image' else None, completed_at=time.time())
                    return
                await asyncio.sleep(2)
            raise TimeoutError('等了 3 小时还没出结果；请在 ComfyUI 里看看是不是卡住了')
    except Exception as e:  # noqa: BLE001
        update(jid, status='failed', error=str(getattr(e, 'detail', None) or e)[:2000])


async def create(b, kind):
    validate(b, kind)
    rid = str(b.get('client_task_id') or uuid.uuid4().hex)[:160]
    digest = hashlib.sha256(json.dumps(b, sort_keys=True).encode()).hexdigest()
    with sqlite3.connect(DB) as c:
        old = c.execute('SELECT hash, body FROM jobs WHERE request_id=?', (rid,)).fetchone()
        if old:
            if old[0] != digest:
                fail('同一个任务编号的输入变了，拒绝重复提交', 409)
            return public(json.loads(old[1]))
        jid = uuid.uuid4().hex
        body = {'id': jid, 'status': 'queued', 'created': int(time.time()), 'model': b['model']}
        c.execute('INSERT INTO jobs VALUES(?,?,?,?,?)', (jid, rid, digest, kind, json.dumps(body)))
    t = asyncio.create_task(execute(jid, b, kind))
    TASKS.add(t)
    t.add_done_callback(TASKS.discard)
    return body


@local.get('/status')
async def status():
    with sqlite3.connect(DB) as c:
        active = c.execute("SELECT count(*) FROM jobs WHERE json_extract(body,'$.status') IN ('queued','in_progress')").fetchone()[0]
    return {'installed': installed(), 'active_jobs': active}


@local.get('/v1/models')
def models():
    return {'object': 'list', 'data': [{'id': m, 'object': 'model', 'owned_by': 'local'} for m in MODELS]}


@local.post('/v1/media/uploads')
async def upload(file: UploadFile = File(...)):
    raw = await file.read(20 * 1024 ** 2 + 1)
    raw = await media_bytes('data:image/png;base64,' + base64.b64encode(raw).decode())
    name = uuid.uuid4().hex + '.png'
    (MEDIA / name).write_bytes(raw)
    return {'url': f'http://127.0.0.1:{CANVAS_PORT}/local/media/{name}'}


@local.post('/v1/images/generations')
async def image(request: Request):
    return await create(await request.json(), 'image')


@local.post('/v1/videos')
async def video(request: Request):
    return await create(await request.json(), 'video')


@local.get('/v1/videos/{jid}')
@local.get('/v1/images/generations/{jid}')
def query(jid: str):
    return public(row(jid))


@local.get('/media/{name}')
def media(name: str):
    if not re.fullmatch(r'[a-f0-9]{32}\.(png|mp4)', name):
        fail('媒体不存在', 404)
    path = MEDIA / name
    if not path.is_file():
        fail('媒体不存在', 404)
    return FileResponse(path)
