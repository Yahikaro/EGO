"""Ready-made, measured runtime presets for the target setup.

A preset only carries loading/sampling values. Paths, ports and timeouts always
come from the user's current profile, so applying a preset never points EGO at
another model file or listener. Measurements are from the user's machine and are
documentation, not guarantees for other hardware.
"""
from __future__ import annotations

from server.ego_core.config import CoreConfig

# Fields a preset may set; everything else (binary, model, ports, host,
# local_http, startup_timeout) stays as in the user's profile.
PRESET_FIELDS = ('context', 'gpu_layers', 'threads', 'batch_size', 'flash_attention',
                 'temperature', 'top_p', 'min_p', 'top_k', 'repeat_penalty', 'max_tokens')

_SAMPLING = {'temperature': 0.6, 'top_p': 0.9, 'min_p': 0.05, 'top_k': 40, 'repeat_penalty': 1.05}

PRESETS = (
    {
        'id': 'balanced-12gb',
        'name': ('Баланс (рекомендуется)', 'Balanced (recommended)'),
        'description': ('Обычные ролевые диалоги: контекст 8192, ответ до 512 токенов. Подходит для видеокарт с 12 ГБ.',
                        'Everyday roleplay: 8192 context, replies up to 512 tokens. Good for 12 GB VRAM.'),
        'values': {'context': 8192, 'gpu_layers': 99, 'threads': None, 'batch_size': None,
                   'flash_attention': True, 'max_tokens': 512, **_SAMPLING},
    },
    {
        'id': 'long-scenes-12gb',
        'name': ('Длинные сцены', 'Long scenes'),
        'description': ('Больше памяти о сцене и длинные ответы: контекст 16384, ответ до 1024 токенов. '
                        'Видеопамять почти полностью занята — нужна карта от 12 ГБ, закройте игры и тяжёлые программы.',
                        'More scene memory and long replies: 16384 context, up to 1024 tokens. '
                        'Uses nearly all VRAM — for 12 GB cards or more; close games and heavy apps.'),
        'values': {'context': 16384, 'gpu_layers': 99, 'threads': None, 'batch_size': None,
                   'flash_attention': True, 'max_tokens': 1024, **_SAMPLING},
    },
    {
        'id': 'short-12gb',
        'name': ('Короткие ответы', 'Short replies'),
        'description': ('Быстрый живой диалог: контекст 4096, ответ до 256 токенов, чуть больше разнообразия. Подходит для видеокарт с 10–12 ГБ.',
                        'Fast back-and-forth: 4096 context, replies up to 256 tokens, a bit more variety. Good for 10–12 GB VRAM.'),
        'values': {'context': 4096, 'gpu_layers': 99, 'threads': None, 'batch_size': None,
                   'flash_attention': True, 'max_tokens': 256,
                   **{**_SAMPLING, 'temperature': 0.7}},
    },
    {
        'id': 'vram-saver-12gb',
        'name': ('Экономия видеопамяти', 'VRAM saver'),
        'description': ('Часть слоёв модели на процессоре: освобождает ~2.6 ГБ видеопамяти для других '
                        'программ. Примерно втрое медленнее. Для видеокарт с 8–10 ГБ.',
                        'Some model layers on the CPU: frees ~2.6 GB VRAM for other apps. '
                        'About three times slower. For 8–10 GB VRAM.'),
        'values': {'context': 4096, 'gpu_layers': 28, 'threads': None, 'batch_size': None,
                   'flash_attention': True, 'max_tokens': 384, **_SAMPLING},
    },
)

# Filled from runtime/benchmarks/presets.py runs on the target machine
# (RTX 3060 12 GB, driver 621.01, llama.cpp b11724 CUDA 12.4, Model D (8B) Q8_0).
# 2026-11-04: each run fills the context to ~90 % with roleplay history, then
# streams a reply. VRAM = whole GPU minus the idle desktop baseline (~0.9-1.1 GB).
MEASUREMENTS = {
    'balanced-12gb':    {'load_seconds': 5.3, 'vram_model_mib': 9086, 'vram_peak_mib': 9970,
                              'decode_tokens_per_second': 34.1, 'prefill_tokens': 7429, 'prefill_seconds': 4.0},
    'long-scenes-12gb': {'load_seconds': 5.7, 'vram_model_mib': 9927, 'vram_peak_mib': 10956,
                              'decode_tokens_per_second': 30.9, 'prefill_tokens': 14737, 'prefill_seconds': 8.4},
    'short-12gb':       {'load_seconds': 5.3, 'vram_model_mib': 8357, 'vram_peak_mib': 9361,
                              'decode_tokens_per_second': 35.5, 'prefill_tokens': 3572, 'prefill_seconds': 1.9},
    'vram-saver-12gb':  {'load_seconds': 10.0, 'vram_model_mib': 7323, 'vram_peak_mib': 8294,
                              'decode_tokens_per_second': 10.5, 'prefill_tokens': 3572, 'prefill_seconds': 3.1},
}

DEFAULT_PRESET_ID = 'balanced-12gb'


def preset(preset_id):
    for item in PRESETS:
        if item['id'] == preset_id:
            return item
    raise ValueError('PRESET_NOT_FOUND')


SAMPLING_FIELDS = ('temperature', 'top_p', 'min_p', 'top_k', 'repeat_penalty', 'max_tokens')


def chat_sampling(preset_id):
    """Values a per-chat preset changes for each request. Loading options (context,
    GPU layers, flash attention, model file) belong to the loaded runtime and stay
    as in the server profile: switching them per chat would reload the model."""
    values = preset(preset_id)['values']
    return {key: values[key] for key in SAMPLING_FIELDS}


def apply_preset(values, preset_id):
    """Return validated profile values: the user's paths/ports + the preset's tuning."""
    if not isinstance(values, dict):
        raise ValueError('PROFILE_INVALID')
    merged = {**CoreConfig().model_dump(), **values, **preset(preset_id)['values']}
    return CoreConfig.model_validate(merged).model_dump()


# 2026-11-06: ready-made model presets shown first in the AI models list.
# A model preset names a GGUF file (found by name on this PC, or chosen by the user)
# and carries tested settings. Models are never downloaded automatically.
MODEL_PRESETS = (
    {
        'id': 'model-12b',
        'name': ('Модель 12B — рекомендуется', '12B model — recommended'),
        'description': ('Хороший русский и формат EGO, почти без повторов. Нужно ~10 ГБ видеопамяти (Q5_K_M).',
                        'Good Russian and EGO format, almost no repetition. Needs ~10 GB VRAM (Q5_K_M).'),
        'files': ('*12b*.gguf',),
        'download': None,
        'values': {'context': 16384, 'gpu_layers': 99, 'threads': None, 'batch_size': None,
                   'flash_attention': True, 'max_tokens': 1024, **_SAMPLING},
    },
    {
        'id': 'model-8b',
        'name': ('Модель 8B — лёгкая', '8B model — light'),
        'description': ('Быстрая и лёгкая (~9 ГБ видеопамяти в Q8_0), но может уходить в английский.',
                        'Fast and light (~9 GB VRAM in Q8_0), but may slip into English.'),
        'files': ('*8b*.gguf',),
        'download': None,
        'values': {'context': 16384, 'gpu_layers': 99, 'threads': None, 'batch_size': None,
                   'flash_attention': True, 'max_tokens': 1024, **_SAMPLING},
    },
)


def model_preset(preset_id):
    for item in MODEL_PRESETS:
        if item['id'] == preset_id:
            return item
    raise ValueError('PRESET_NOT_FOUND')
