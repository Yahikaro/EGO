import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace


def test_benchmark_missing_model_is_blocked_without_spawn():
    result = subprocess.run([sys.executable, '-m', 'runtime.benchmarks.run'],
                            capture_output=True, text=True)
    assert result.returncode == 2
    report = json.loads(result.stdout)
    assert report['status'] == 'BLOCKED'
    assert report['reason'] == 'MODEL_NOT_PROVIDED'
    assert report['measurements'] == []


def test_failed_load_still_has_sampled_resource_evidence(tmp_path, monkeypatch):
    import time
    from runtime.benchmarks import run
    from runtime.adapters.llama_cpp import LlamaRuntime, RuntimeFailure
    model = tmp_path / 'synthetic.gguf'
    model.write_bytes(b'GGUF')

    class LoadingRuntime(LlamaRuntime):
        def start(self):
            self.process = subprocess.Popen([getattr(sys,'_base_executable',sys.executable),'-c',
                'import time; a=bytearray(32*1024*1024); print("ready",flush=True); time.sleep(10)'],
                stdout=subprocess.PIPE, text=True)
            assert self.process.stdout.readline().strip() == 'ready'
            self.process.stdout.close()
            time.sleep(.4)
            raise RuntimeFailure('SYNTHETIC_LOAD_FAILURE')

    # This managed host exposes virtual Popen PIDs that psutil cannot read.
    # Control only the OS-memory-read boundary; lifecycle uses a real child.
    monkeypatch.setattr(run.psutil, 'Process', lambda pid: SimpleNamespace(
        memory_info=lambda: SimpleNamespace(rss=40 * 1024 * 1024)))
    monkeypatch.setattr(run, 'LlamaRuntime', LoadingRuntime)
    report = run.benchmark(Path(sys.executable), model)
    assert report['status'] == 'FAIL'
    assert report['reason'] == 'SYNTHETIC_LOAD_FAILURE'
    assert report['failed_phase_resources']['sampled_peak_rss_bytes'] > 30 * 1024 * 1024
    assert 'loading' in report['failed_phase_resources']['measurement_scope']


def test_hash_read_failure_returns_structured_report(tmp_path, monkeypatch):
    from runtime.benchmarks import run
    model = tmp_path / 'unreadable.gguf'
    model.write_bytes(b'GGUF')

    def unreadable(path):
        raise PermissionError('synthetic I/O boundary')

    monkeypatch.setattr(run, 'sha256', unreadable)
    report = run.benchmark(Path(sys.executable), model)
    assert report['status'] == 'FAIL'
    assert report['reason'] == 'PermissionError'


def test_fixture_only_benchmark_samples_loading_and_restart_peaks(tmp_path, monkeypatch):
    """Disposable Python workers, not GGUF inference/performance evidence."""
    from pathlib import Path
    from runtime.benchmarks import run
    from runtime.adapters.llama_cpp import LlamaRuntime
    model = tmp_path / 'synthetic.gguf'
    model.write_bytes(b'GGUF')
    fixture = Path(__file__).resolve().parents[2] / 'tests/fixtures/fake_llama.py'

    load_size = [0]

    class FixtureRuntime(LlamaRuntime):
        loads = 0

        def _binary_command(self):
            self.loads += 1
            load_size[0] = self.loads * 32 * 1024 * 1024
            return [getattr(sys,'_base_executable',sys.executable),str(fixture), '--load-mib', str(32 * self.loads)]

    # Deterministic OS-memory observation verifies all restart phases even
    # where the managed host's virtual child PID is absent from /proc.
    monkeypatch.setattr(run.psutil, 'Process', lambda pid: SimpleNamespace(
        memory_info=lambda: SimpleNamespace(rss=load_size[0])))
    monkeypatch.setattr(run, 'LlamaRuntime', FixtureRuntime)
    report = run.benchmark(Path(sys.executable), model)
    assert report['status'] == 'PASS', report.get('reason')
    assert len(report['measurements']) == 2
    for row in report['measurements']:
        # The third disposable load is96MiB. These controlled observations
        # test sampler lifetime, never actual GPU or inference performance.
        assert row['sampled_peak_rss_bytes'] > 90 * 1024 * 1024
        assert row['cancel_worker_exited']


def test_benchmark_exports_original_bind_errno_and_winerror(tmp_path, monkeypatch):
    import errno
    from runtime.benchmarks import run
    from runtime.adapters.llama_cpp import LlamaRuntime, RuntimeFailure
    original = OSError(errno.EACCES, 'synthetic Windows socket denial')
    original.winerror = 10013
    model = tmp_path / 'synthetic.gguf'
    model.write_bytes(b'GGUF')

    class DeniedRuntime(LlamaRuntime):
        def start(self):
            raise RuntimeFailure('PORT_BIND_FAILED', os_error=original, operation='bind') from original

    monkeypatch.setattr(run, 'LlamaRuntime', DeniedRuntime)
    report = run.benchmark(Path(sys.executable), model)
    assert report['status'] == 'FAIL'
    assert report['reason'] == 'PORT_BIND_FAILED'
    assert report['os_error'] == {'operation': 'bind', 'errno': errno.EACCES, 'winerror': 10013}
