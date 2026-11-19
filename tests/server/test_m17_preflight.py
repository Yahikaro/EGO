"""Stage 4d: startup configuration check with readable findings."""
import socket

import pytest

from runtime.adapters.ports import choose_loopback_port
from server.ego_core import control, preflight
from server.ego_core.config import CoreConfig, save_config


def _install(tmp_path, **values):
    (tmp_path / 'data').mkdir()
    api, runtime = choose_loopback_port(), choose_loopback_port()
    while runtime == api:
        runtime = choose_loopback_port()
    save_config(tmp_path / 'data/config.json', CoreConfig(api_port=api, runtime_port=runtime, **values))
    return api, runtime


def _codes(items):
    return [i['code'] for i in items]


def test_no_model_is_a_warning_not_an_error(tmp_path):
    _install(tmp_path)
    report = preflight.run(tmp_path, check_gpu=False)
    assert report['ok'] and 'MODEL_NOT_PROVIDED' in _codes(report['warnings'])
    warning = next(w for w in report['warnings'] if w['code'] == 'MODEL_NOT_PROVIDED')
    assert warning['message'] and warning['hint'] and warning['field'] == 'model'


def test_busy_api_port_blocks_start_before_any_process_is_spawned(tmp_path, monkeypatch):
    api, _ = _install(tmp_path)
    monkeypatch.setattr(control.KeyVault, 'unlock', lambda self: None)
    spawned = []
    monkeypatch.setattr(control.subprocess, 'Popen', lambda *a, **k: spawned.append(a))
    with socket.socket() as holder:
        holder.bind(('127.0.0.1', api)); holder.listen()
        report = preflight.run(tmp_path, check_gpu=False)
        assert not report['ok'] and _codes(report['errors']) == ['API_PORT_IN_USE']
        with pytest.raises(control.ControlFailure, match='API_PORT_IN_USE') as caught:
            control.start(tmp_path)
    assert spawned == []
    assert caught.value.diagnostics['preflight']['errors'][0]['port'] == api
    # This installation's own running Core is not a conflict.
    with socket.socket() as holder:
        holder.bind(('127.0.0.1', api)); holder.listen()
        assert preflight.run(tmp_path, core_running=True, check_gpu=False)['ok']


def test_broken_config_is_an_error(tmp_path):
    (tmp_path / 'data').mkdir()
    (tmp_path / 'data/config.json').write_text('{"context": "много"}', encoding='utf-8')
    report = preflight.run(tmp_path, check_gpu=False)
    assert not report['ok'] and _codes(report['errors']) == ['CONFIG_INVALID']


def test_files_vram_and_model_port_are_checked(tmp_path):
    binary = tmp_path / 'llama-server.exe'; binary.write_bytes(b'not an exe')
    model = tmp_path / 'model.gguf'; model.write_bytes(b'GGUF' + b'\0' * (2 * 1024 * 1024))
    _, runtime = _install(tmp_path, binary=str(binary), model=str(model), context=32768)
    with socket.socket() as holder:
        holder.bind(('127.0.0.1', runtime)); holder.listen()
        report = preflight.run(tmp_path, gpu_total_mib=1024)          # pretend a 1 GB card
    codes = _codes(report['warnings'])
    assert report['ok'] and 'RUNTIME_PORT_IN_USE' in codes and 'VRAM_LIKELY_INSUFFICIENT' in codes
    import os
    if os.name == 'nt':
        assert 'RUNTIME_INCOMPATIBLE_BINARY' in codes
    model.write_bytes(b'XXXX')
    assert 'INVALID_GGUF' in _codes(preflight.run(tmp_path, check_gpu=False)['warnings'])


def test_check_command_reports_findings(tmp_path, capsys):
    _install(tmp_path)
    assert control.main(['check', '--root', str(tmp_path)]) == 2      # WARN: no model configured
    out = capsys.readouterr().out
    assert '"WARN"' in out and 'MODEL_NOT_PROVIDED' in out
