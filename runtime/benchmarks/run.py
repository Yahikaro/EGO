"""Opt-in synthetic benchmark. No downloads; no fixture substituted for a model."""
import argparse
import hashlib
import json
import subprocess
import threading
import time
from pathlib import Path

import httpx
import psutil

from runtime.adapters.llama_cpp import LlamaRuntime, RuntimeConfig, RuntimeFailure
from runtime.adapters.ports import choose_loopback_port
from scripts.check_environment import inventory


def sha256(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


class Sampler:
    def __init__(self, process_getter):
        self.process_getter = process_getter
        self.stop = threading.Event()
        self.peak_rss_bytes = None
        self.rss_samples = 0
        self.peak_gpu_device_used_mib = None
        self.thread = threading.Thread(target=self.run)

    def run(self):
        while not self.stop.is_set():
            try:
                process = self.process_getter()
                if process is not None:
                    self.peak_rss_bytes = max(self.peak_rss_bytes or 0, psutil.Process(process.pid).memory_info().rss)
                    self.rss_samples += 1
            except psutil.Error:
                pass
            try:
                r = subprocess.run(['nvidia-smi', '--query-gpu=memory.used', '--format=csv,noheader,nounits'],
                                   capture_output=True, text=True, timeout=2)
                if r.returncode == 0:
                    used = sum(int(x.strip()) for x in r.stdout.splitlines())
                    self.peak_gpu_device_used_mib = max(self.peak_gpu_device_used_mib or 0, used)
            except (OSError, ValueError, subprocess.TimeoutExpired):
                pass
            self.stop.wait(.1)

    def finish(self):
        self.stop.set()
        self.thread.join(3)
        return {'sampled_peak_rss_bytes': self.peak_rss_bytes,
                'rss_samples': self.rss_samples,
                'rss_status': 'AVAILABLE' if self.rss_samples else 'UNAVAILABLE',
                'sampled_peak_gpu_all_devices_used_mib': self.peak_gpu_device_used_mib,
                'gpu_scope': 'whole devices including other apps; not process allocation',
                'sampling': '100ms plus bounded nvidia-smi latency; not exact hardware peak',
                'measurement_scope': 'initial loading, generation, cancellation and all reloads'}


def free_port():
    return choose_loopback_port()


def measure(runtime, prompt):
    start = time.perf_counter()
    first = None
    output = []
    final = None
    for event in runtime.stream(prompt, max_tokens=128):
        if event.content:
            first = first or time.perf_counter()
            output.append(event.content)
        if event.stop:
            final = event
    elapsed = time.perf_counter() - start
    if first is None or final is None:
        raise RuntimeFailure('EMPTY_BENCHMARK_OUTPUT')
    return {'ttft_seconds': first - start, 'elapsed_seconds': elapsed,
            'output_sha256': hashlib.sha256(''.join(output).encode()).hexdigest(),
            'tokens_predicted': final.tokens_predicted,
            'runtime_timings': final.timings}


def benchmark(binary, model):
    report = {'status': 'BLOCKED', 'environment': inventory(), 'measurements': []}
    if model is None or not model.is_file():
        report['reason'] = 'MODEL_NOT_PROVIDED'
        return report
    if binary is None or not binary.is_file():
        report['reason'] = 'RUNTIME_NOT_PROVIDED'
        return report
    try:
        report['manifest'] = {'model_sha256': sha256(model), 'model_size_bytes': model.stat().st_size,
                              'binary_sha256': sha256(binary), 'model_compatibility': 'UNCONFIRMED_UNTIL_RUN',
                              'weights_not_downloaded_by_this_tool': True,
                              'prompt_dataset': 'ego-m0-synthetic-v1', 'contexts': [2048, 4096]}
        for context in [2048, 4096]:
            runtime = LlamaRuntime(RuntimeConfig(binary, model, free_port(), context=context))
            sampler = Sampler(lambda: runtime.process)
            sampler.thread.start()
            try:
                started = time.perf_counter()
                runtime.start()
                load_seconds = time.perf_counter() - started
                # Grow only synthetic prompt material to half the context; use
                # real tokenizer to record measured input length, not word count.
                prompt = 'Continue this fictional scene in English. A traveler enters a quiet village. '
                with httpx.Client(trust_env=False, timeout=10) as client:
                    while True:
                        response = client.post(runtime.url + '/tokenize', json={'content': prompt})
                        response.raise_for_status()
                        count = len(response.json()['tokens'])
                        if count >= context // 2:
                            break
                        prompt += 'The village has a bridge, a garden and an old library. ' * 8
                if count + 128 >= context:
                    raise RuntimeFailure('BENCHMARK_PROMPT_OVERFLOW')
                row = {'context': context, 'input_tokens': count,
                       'initial_worker_load_seconds': load_seconds,
                       'cold_disk_cache': 'NOT_CONTROLLED',
                       'first': measure(runtime, prompt), 'repeated': measure(runtime, prompt)}
                iterator = runtime.stream(prompt, max_tokens=128)
                next(iterator)
                worker = runtime.process
                cancel_start = time.perf_counter()
                runtime.cancel()
                iterator.close()
                row['cancel_seconds'] = time.perf_counter() - cancel_start
                row['cancel_worker_exited'] = worker.poll() is not None
                reload_start = time.perf_counter()
                runtime.start()
                row['restart_after_cancel_load_seconds'] = time.perf_counter() - reload_start
                row['after_cancel'] = measure(runtime, prompt)
                runtime.process.kill()
                runtime.process.wait(timeout=5)
                runtime.start()
                row['after_crash'] = measure(runtime, prompt)
                row.update(sampler.finish())
                sampler = None
                report['measurements'].append(row)
            except (RuntimeFailure, OSError, ValueError, KeyError, httpx.HTTPError, subprocess.TimeoutExpired):
                report['failed_phase_resources'] = sampler.finish()
                sampler = None
                raise
            finally:
                if sampler is not None:
                    sampler.finish()
                runtime.shutdown()
        report['status'] = 'PASS'
    except (RuntimeFailure, OSError, ValueError, KeyError, httpx.HTTPError, subprocess.TimeoutExpired) as exc:
        report.update(status='FAIL', reason=exc.code if isinstance(exc, RuntimeFailure) else type(exc).__name__)
        if isinstance(exc, RuntimeFailure) and exc.os_error is not None:
            report['os_error'] = {'operation': exc.operation, 'errno': exc.errno, 'winerror': exc.winerror}
            if getattr(exc, 'diagnostics', None):
                report['runtime_diagnostics'] = exc.diagnostics
        elif isinstance(exc, OSError):
            report['os_error'] = {'operation': 'benchmark', 'errno': exc.errno,
                                  'winerror': getattr(exc, 'winerror', None)}
    return report


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--binary', type=Path)
    p.add_argument('--model', type=Path)
    p.add_argument('--output', type=Path)
    args = p.parse_args()
    report = benchmark(args.binary, args.model)
    encoded = json.dumps(report, indent=2)
    if args.output:
        args.output.write_text(encoded + '\n', encoding='utf-8')
    print(encoded)
    return 0 if report['status'] == 'PASS' else 2


if __name__ == '__main__':
    raise SystemExit(main())
