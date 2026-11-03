"""把画布的请求拼成 ComfyUI 工作流（只用 ComfyUI 官方自带节点）。

文件名来自启动器写的 installed.json，所以换显卡、换档位都不用改这里。
"""
from __future__ import annotations

import math

QWEN_MAX_IMAGES = 4
QWEN_SIZES = {'1:1': (1328, 1328), '16:9': (1664, 928), '9:16': (928, 1664), '4:3': (1472, 1104),
              '3:4': (1104, 1472), '21:9': (2016, 864)}

# 「默认」是 8 步 Turbo；40 步档用标准版底模，参数沿用 Danrisi 原工作流（50 步改 40 步，res_2m 换 res_multistep）
QWEN_PRESETS = {
    'turbo': {'steps': 8, 'cfg': 1.0, 'sampler': 'euler', 'scheduler': 'simple', 'loras': (), 'prefix': ''},
    'fine': {'steps': 40, 'cfg': 3.0, 'sampler': 'res_multistep', 'scheduler': 'beta', 'loras': (), 'prefix': ''},
    'film': {'steps': 40, 'cfg': 3.0, 'sampler': 'res_multistep', 'scheduler': 'beta',
             'loras': (('film', 0.7), ('fix', 0.8)), 'prefix': 'film still, 35mm film still. '},
    'canon': {'steps': 40, 'cfg': 3.0, 'sampler': 'res_multistep', 'scheduler': 'beta',
              'loras': (('canon', 0.7), ('fix', 0.8)), 'prefix': 'c2n0n, dslr photo, candid photo. '},
}
QWEN_LABELS = {'精细 40步（标准版）': 'fine', '电影剧照 40步': 'film', '单反写实 40步': 'canon'}
LORA_NAMES = {'film': '电影剧照', 'canon': '单反写实', 'fix': '细节修复'}


def qwen_preset(value) -> str:
    if value in QWEN_PRESETS:
        return value
    return QWEN_LABELS.get(value, 'turbo')


def qwen(installed: dict, prompt: str, seed: int, width: int, height: int, images: list[str], preset: str, prefix: str) -> dict:
    q = installed.get('qwen')
    if not q:
        raise ValueError('还没安装 Qwen 2.1 图片模型：回到一键包管理页（http://127.0.0.1:10020）勾选安装')
    key = qwen_preset(preset)
    cfg = QWEN_PRESETS[key]
    unet = q['turbo'] if key == 'turbo' else q.get('base')
    if not unet:
        raise ValueError('这个档位要「Qwen 精细 40 步」标准版底模，还没安装；请在一键包管理页安装，或改用「默认」档')
    for name, _ in cfg['loras']:
        if not q.get(name):
            raise ValueError(f'还没安装「{LORA_NAMES[name]}」LoRA；请在一键包管理页勾选安装，或改用别的档位')
    if len(images) > QWEN_MAX_IMAGES:
        raise ValueError('Qwen 2.1 最多4张参考图')
    g = {
        '1': {'class_type': 'UNETLoader', 'inputs': {'unet_name': unet, 'weight_dtype': 'default'}},
        '2': {'class_type': 'CLIPLoader', 'inputs': {'clip_name': q['te'], 'type': 'qwen_image', 'device': 'default'}},
        '3': {'class_type': 'VAELoader', 'inputs': {'vae_name': q['vae']}},
        '4': {'class_type': 'TextEncodeQwenImage21', 'inputs': {'clip': ['2', 0], 'prompt': cfg['prefix'] + prompt,
                                                                'negative_prompt': '', 'resolution': 1024, 'vae': ['3', 0]}},
        '5': {'class_type': 'EmptyLatentImage', 'inputs': {'width': width, 'height': height, 'batch_size': 1}},
        '6': {'class_type': 'KSampler', 'inputs': {'model': ['1', 0], 'positive': ['4', 0], 'negative': ['4', 1],
                                                   'latent_image': ['5', 0], 'seed': seed, 'steps': cfg['steps'],
                                                   'cfg': cfg['cfg'], 'sampler_name': cfg['sampler'],
                                                   'scheduler': cfg['scheduler'], 'denoise': 1.0}},
        '7': {'class_type': 'VAEDecode', 'inputs': {'samples': ['6', 0], 'vae': ['3', 0]}},
        '8': {'class_type': 'SaveImage', 'inputs': {'images': ['7', 0], 'filename_prefix': prefix}},
    }
    model = ['1', 0]
    for i, (name, strength) in enumerate(cfg['loras']):
        g[f'lora{i}'] = {'class_type': 'LoraLoaderModelOnly', 'inputs': {'model': model, 'lora_name': q[name], 'strength_model': strength}}
        model = [f'lora{i}', 0]
    g['6']['inputs']['model'] = model
    for i, path in enumerate(images, 1):
        g[f'img{i}'] = {'class_type': 'LoadImage', 'inputs': {'image': path}}
        g['4']['inputs'][f'images.image_{i}'] = [f'img{i}', 0]
    return g


def moss(installed: dict, prompt: str, seed: int, width: int, height: int, prefix: str) -> dict:
    m = installed.get('moss')
    if not m:
        raise ValueError('还没安装 MOSS-Image：回到一键包管理页勾选安装')
    width, height = width // 16 * 16, height // 16 * 16
    return {
        '1': {'class_type': 'UNETLoader', 'inputs': {'unet_name': m['unet'], 'weight_dtype': 'default'}},
        '2': {'class_type': 'CLIPLoader', 'inputs': {'clip_name': m['te'], 'type': 'moss_image', 'device': 'default'}},
        '3': {'class_type': 'VAELoader', 'inputs': {'vae_name': m['vae']}},
        '6': {'class_type': 'SkipLayerGuidanceDiTSimple', 'inputs': {'model': ['1', 0], 'double_layers': '', 'single_layers': '22',
                                                                     'start_percent': 0.0, 'end_percent': 0.5}},
        '7': {'class_type': 'CLIPTextEncode', 'inputs': {'clip': ['2', 0], 'text': prompt}},
        '8': {'class_type': 'CLIPTextEncode', 'inputs': {'clip': ['2', 0], 'text': ''}},
        '9': {'class_type': 'EmptyFlux2LatentImage', 'inputs': {'width': width, 'height': height, 'batch_size': 1}},
        '10': {'class_type': 'KSampler', 'inputs': {'model': ['6', 0], 'positive': ['7', 0], 'negative': ['8', 0],
                                                    'latent_image': ['9', 0], 'seed': seed, 'steps': 30, 'cfg': 3.0,
                                                    'sampler_name': 'euler', 'scheduler': 'sgm_uniform', 'denoise': 1.0}},
        '11': {'class_type': 'VAEDecode', 'inputs': {'samples': ['10', 0], 'vae': ['3', 0]}},
        '12': {'class_type': 'SaveImage', 'inputs': {'images': ['11', 0], 'filename_prefix': prefix}},
    }


H3_PRESETS = {'细化30步': 'normal30', '均衡20步': 'balanced20', '快速8步 Turbo（768档）': 'turbo', '快速4步 Turbo（544档）': 'turbo'}


def h3_config(variant: str, preset) -> dict:
    key = H3_PRESETS.get(preset, preset) or 'balanced20'
    if key in (None, '', '自动', '自由'):
        key = 'balanced20'
    if key not in ('normal30', 'balanced20', 'turbo'):
        raise ValueError('H3 档位无效，请刷新画布重新选择')
    fast = key == 'turbo'
    return {'preset': key,
            'steps': (8 if variant == 'fl2va' else 4) if fast else 20 if key == 'balanced20' else 30,
            'turbo': fast, 'short_edge': 544 if fast and variant == 'ref2va' else 768,
            'shift_video': 6.0 if fast and variant == 'fl2va' else 12.0, 'shift_audio': 3.0}


def h3(installed: dict, variant: str, prompt: str, images: list[str], seconds: float, ratio: str, seed: int,
       preset, prefix: str) -> dict:
    h = installed.get('h3') or {}
    v = h.get(variant)
    if not v:
        name = 'H3 首帧/文生视频（FL2VA）' if variant == 'fl2va' else 'H3 参考图生视频（Ref2VA）'
        raise ValueError(f'还没安装「{name}」：回到一键包管理页勾选安装')
    if len(images) > (2 if variant == 'fl2va' else 7):
        raise ValueError('参考图太多：FL2VA 最多 2 张（首帧、尾帧），Ref2VA 最多 7 张')
    if variant == 'ref2va' and not images:
        raise ValueError('Ref2VA 至少要连一张参考图；纯文字生成请用 FL2VA')
    if not math.isfinite(seconds) or not 4 <= seconds <= 15:
        raise ValueError('时长只能是 4～15 秒')
    if ratio not in ('16:9', '9:16', '1:1', '4:3', '3:4', '21:9'):
        raise ValueError('视频画幅不支持')
    cfg = h3_config(variant, preset)
    if cfg['turbo'] and not v.get('turbo'):
        raise ValueError('Turbo 加速 LoRA 没装好；请在一键包管理页重新安装 H3，或换「均衡20步」')
    a, b = map(int, ratio.split(':'))
    edge = cfg['short_edge']
    area = min(edge * (1344 if edge == 768 else 960), edge * edge * max(a / b, b / a))
    w = round(math.sqrt(area * a / b) / 32) * 32
    hgt = round(math.sqrt(area * b / a) / 32) * 32
    frames = min(range(107, 363, 17), key=lambda n: abs(n / 24 - seconds))
    # 12～23GB 显存装的是 GGUF 压缩版，要用 ComfyUI-GGUF 插件的加载节点
    model = ({'class_type': 'UnetLoaderGGUF', 'inputs': {'unet_name': v['unet']}} if v.get('gguf') else
             {'class_type': 'UNETLoader', 'inputs': {'unet_name': v['unet'], 'weight_dtype': 'default'}})
    clip = ({'class_type': 'CLIPLoaderGGUF', 'inputs': {'clip_name': h['te'], 'type': 'minimax'}} if h.get('te_gguf') else
            {'class_type': 'CLIPLoader', 'inputs': {'clip_name': h['te'], 'type': 'minimax', 'device': 'default'}})
    g = {
        'model': model,
        'clip': clip,
        'vae': {'class_type': 'VAELoader', 'inputs': {'vae_name': h['video_vae']}},
        'audio': {'class_type': 'VAELoader', 'inputs': {'vae_name': h['audio_vae']}},
        'condition': {'class_type': 'MiniMaxH3ImageToVideo' if variant == 'fl2va' else 'MiniMaxH3ReferenceToVideo',
                      'inputs': {'clip': ['clip', 0], 'vae': ['vae', 0], 'prompt': prompt,
                                 'width': w, 'height': hgt, 'length': frames}},
        'noise': {'class_type': 'RandomNoise', 'inputs': {'noise_seed': seed}},
        'guider': {'class_type': 'BasicGuider', 'inputs': {'model': ['model', 0], 'conditioning': ['condition', 0]}},
        'sampler': {'class_type': 'KSamplerSelect', 'inputs': {'sampler_name': 'euler'}},
        'sigmas': {'class_type': 'BasicScheduler', 'inputs': {'model': ['model', 0], 'scheduler': 'simple',
                                                              'steps': cfg['steps'], 'denoise': 1.0}},
        'sample': {'class_type': 'SamplerCustomAdvanced', 'inputs': {'noise': ['noise', 0], 'guider': ['guider', 0],
                                                                     'sampler': ['sampler', 0], 'sigmas': ['sigmas', 0],
                                                                     'latent_image': ['condition', 1]}},
        'decode': {'class_type': 'VAEDecode', 'inputs': {'samples': ['sample', 0], 'vae': ['vae', 0]}},
        'adecode': {'class_type': 'VAEDecodeAudio', 'inputs': {'samples': ['sample', 0], 'vae': ['audio', 0]}},
        'video': {'class_type': 'CreateVideo', 'inputs': {'images': ['decode', 0], 'audio': ['adecode', 0], 'fps': 24}},
        'out': {'class_type': 'SaveVideo', 'inputs': {'video': ['video', 0], 'filename_prefix': prefix,
                                                      'format': 'mp4', 'format.codec': 'h264'}},
    }
    if variant == 'ref2va':
        g['condition']['inputs'].update(audio_vae=['audio', 0], ref_image_size='match')
    if cfg['turbo']:
        g['turbo_lora'] = {'class_type': 'LoraLoaderModelOnly', 'inputs': {'model': ['model', 0], 'lora_name': v['turbo'],
                                                                         'strength_model': 1.0}}
        g['turbo_shift'] = {'class_type': 'MiniMaxH3SigmaShift', 'inputs': {'model': ['turbo_lora', 0],
                                                                          'shift_video': cfg['shift_video'],
                                                                          'shift_audio': cfg['shift_audio']}}
        for node in ('guider', 'sigmas'):
            g[node]['inputs']['model'] = ['turbo_shift', 0]
    for i, image in enumerate(images):
        g[f'ref{i}'] = {'class_type': 'LoadImage', 'inputs': {'image': image}}
        slot = ('first_frame', 'last_frame')[i] if variant == 'fl2va' else f'ref_images.ref_image_{i}'
        g['condition']['inputs'][slot] = [f'ref{i}', 0]
    return g
