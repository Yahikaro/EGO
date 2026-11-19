"""Measure every runtime preset on the real model (no Core, no user data).

Usage (Core and other llama-server instances stopped):
  .venv\\Scripts\\python.exe -m runtime.benchmarks.presets --binary <llama-server.exe> --model <model.gguf>

For each preset: load time, VRAM before/peak (nvidia-smi, whole GPU), a context
fill to ~90 % of the window (proves the KV cache really fits), decode speed and
finish_reason of a long roleplay reply. Prints JSON; never logs prompts/replies.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import threading
import time
from pathlib import Path

from runtime.adapters.llama_cpp import LlamaRuntime, RuntimeConfig
from runtime.adapters.ports import choose_loopback_port
from server.ego_core.presets import PRESETS, apply_preset
from server.ego_core.roleplay_prompt import compile_messages

CHARACTER = ('Лира', 'Взрослая женщина 27 лет, хранительница древнего архива. Ироничная, тёплая, любит долгие истории.')
FILLER = ('*Я листаю старую рукопись и читаю вслух.* В этой главе говорится о путешествии через горы, '
          'о караванах, о звёздах над пустыней и о людях, которые хранили память о забытых городах. ')


def vram_used():
    out = subprocess.run(['nvidia-smi', '--query-gpu=memory.used,memory.total', '--format=csv,noheader,nounits'],
                         capture_output=True, text=True, timeout=10).stdout.strip().splitlines()[0]
    used, total = (int(x) for x in out.split(','))
    return used, total


class VramSampler(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.peak, self._halt = 0, threading.Event()

    def run(self):
        while not self._halt.is_set():
            try: self.peak = max(self.peak, vram_used()[0])
            except Exception: pass
            self._halt.wait(.25)

    def stop(self):
        self._halt.set(); self.join(2)
        return self.peak


def history_for(runtime, window, reserve):
    """Alternate user/assistant turns until the exact budget is ~90 % used."""
    counter = runtime.count_tokens
    rows, used = [], 0
    target = int((window - reserve) * 0.9)
    while used < target:
        for role in ('user', 'assistant'):
            rows.append({'role': role, 'content': FILLER * 3, 'status': 'completed'})
            used += counter(FILLER * 3) + 8
    rows.append({'role': 'user', 'status': 'completed',
                 'content': 'Расскажи подробно и не торопясь всю историю этой рукописи, с деталями и диалогами.'})
    return rows


def measure(binary, model, item):
    values = apply_preset({'binary': str(binary), 'model': str(model)}, item['id'])
    port = choose_loopback_port()
    config = RuntimeConfig(Path(values['binary']), Path(values['model']), port, values['context'],
                           values['gpu_layers'], 180., threads=values['threads'], batch_size=values['batch_size'],
                           flash_attention=values['flash_attention'], temperature=values['temperature'],
                           top_p=values['top_p'], min_p=values['min_p'], top_k=values['top_k'],
                           repeat_penalty=values['repeat_penalty'], max_tokens=values['max_tokens'])
    runtime = LlamaRuntime(config)
    baseline, total = vram_used()
    sampler = VramSampler(); sampler.start()
    result = {'id': item['id'], 'context': values['context'], 'gpu_layers': values['gpu_layers'],
              'max_tokens': values['max_tokens'], 'vram_baseline_mib': baseline, 'vram_total_mib': total}
    try:
        started = time.monotonic()
        runtime.start()
        result['load_seconds'] = round(time.monotonic() - started, 2)
        reserve = min(max(32, values['max_tokens']), values['context'] - 256)
        stored = history_for(runtime, values['context'], reserve)
        messages = compile_messages(stored, character_name=CHARACTER[0], character_description=CHARACTER[1],
                                         context_window=values['context'], response_reserve=reserve,
                                         token_counter=runtime.count_tokens)
        final = None
        chars = 0
        t0 = time.monotonic()
        for event in runtime.stream_chat(messages):
            chars += len(event.content)
            if event.stop: final = event
        result['wall_seconds'] = round(time.monotonic() - t0, 2)
        timings = (final.timings or {}) if final else {}
        result.update(prompt_tokens=timings.get('prompt_n'), completion_tokens=timings.get('predicted_n'),
                      decode_tokens_per_second=round(timings['predicted_per_second'], 2) if timings.get('predicted_per_second') else None,
                      prefill_seconds=round(timings['prompt_ms'] / 1000, 2) if timings.get('prompt_ms') else None,
                      finish_reason=final.finish_reason if final else None, reply_characters=chars,
                      status='PASS' if final else 'FAIL')
    except Exception as exc:
        result.update(status='FAIL', code=getattr(exc, 'code', type(exc).__name__))
    finally:
        runtime.shutdown()
        result['vram_peak_mib'] = sampler.stop()
        result['vram_model_mib'] = result['vram_peak_mib'] - baseline
        time.sleep(2)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', type=Path, required=True)
    parser.add_argument('--model', type=Path, required=True)
    parser.add_argument('--only', action='append', help='Preset id(s) to measure')
    args = parser.parse_args()
    results = [measure(args.binary, args.model, item) for item in PRESETS if not args.only or item['id'] in args.only]
    print(json.dumps(results, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
