"""Startup configuration check (stage 4d): find problems before Core starts.

Each finding has a code from the error catalog, a readable message and hint, and
the setting it concerns. Severity:
  error   — Core cannot work: start is refused with this message;
  warning — Core starts (it is also the tool to fix the setup), but generation will
            not work or may be slow until the warning is resolved.
Read-only: never writes data, never touches a running Core.
"""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
from pathlib import Path

from server.ego_core.config import load_config
from server.ego_core.errors import describe

MIN_FREE_DISK_MB = 500
KV_MIB_PER_1K_CONTEXT = 128          # Llama-3 8B, f16 KV cache: ~128 KiB per token.
VRAM_HEADROOM_MIB = 700              # Compute buffers + desktop.


def _finding(severity, code, field=None, **details):
    message, hint = describe(code)
    return {'severity': severity, 'code': code, 'message': message, 'hint': hint, 'field': field,
            **{k: v for k, v in details.items() if v is not None}}


def port_free(port):
    """True if this process could bind the loopback port right now."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        if os.name == 'nt':
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            probe.bind(('127.0.0.1', port))
            return True
        except OSError:
            return False


def _is_pe_x64(path):
    try:
        with path.open('rb') as f:
            head = f.read(64)
            if len(head) < 64 or head[:2] != b'MZ':
                return False
            offset = int.from_bytes(head[60:64], 'little')
            f.seek(offset)
            pe = f.read(6)
            return pe[:4] == b'PE\0\0' and pe[4:] == b'\x64\x86'
    except OSError:
        return False


def _gpu_total_mib():
    try:
        out = subprocess.run(['nvidia-smi', '--query-gpu=memory.total', '--format=csv,noheader,nounits'],
                             capture_output=True, text=True, timeout=5,
                             creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        return int(out.stdout.strip().splitlines()[0]) if out.returncode == 0 else None
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return None


def run(root, *, core_running=False, gpu_total_mib=None, check_gpu=True):
    """Return {'ok', 'errors', 'warnings'}. core_running: this installation's Core holds its
    lease, so its own ports are expected to be busy and are not reported."""
    root = Path(root)
    errors, warnings = [], []
    data = root / 'data'
    try:
        config = load_config(data / 'config.json')
    except (ValueError, OSError):
        errors.append(_finding('error', 'CONFIG_INVALID', 'config.json'))
        return {'ok': False, 'errors': errors, 'warnings': warnings}

    if data.exists() and not (data / 'keys.protected').exists() and any((data / n).exists() for n in ('main.db', 'profile.db')):
        errors.append(_finding('error', 'CORE_LOCKED', 'data'))

    if not core_running:
        if not port_free(config.api_port):
            errors.append(_finding('error', 'API_PORT_IN_USE', 'api_port', port=config.api_port))
        if config.binary and not port_free(config.runtime_port):
            warnings.append(_finding('warning', 'RUNTIME_PORT_IN_USE', 'runtime_port', port=config.runtime_port))

    if not config.binary or not config.model:
        warnings.append(_finding('warning', 'MODEL_NOT_PROVIDED', 'model'))
    else:
        binary, model = Path(config.binary), Path(config.model)
        if not binary.is_file():
            warnings.append(_finding('warning', 'RUNTIME_NOT_PROVIDED', 'binary'))
        elif os.name == 'nt' and not _is_pe_x64(binary):
            warnings.append(_finding('warning', 'RUNTIME_INCOMPATIBLE_BINARY', 'binary'))
        if not model.is_file():
            warnings.append(_finding('warning', 'MODEL_NOT_PROVIDED', 'model'))
        else:
            try:
                with model.open('rb') as f:
                    if f.read(4) != b'GGUF':
                        warnings.append(_finding('warning', 'INVALID_GGUF', 'model'))
                size_mib = model.stat().st_size // (1024 * 1024)
            except OSError:
                size_mib = None
            total = gpu_total_mib if gpu_total_mib is not None else (_gpu_total_mib() if check_gpu else None)
            if size_mib and total and config.gpu_layers >= 99:
                need = size_mib + KV_MIB_PER_1K_CONTEXT * config.context // 1024 + VRAM_HEADROOM_MIB
                if need > total:
                    warnings.append(_finding('warning', 'VRAM_LIKELY_INSUFFICIENT', 'context',
                                             needed_mib=need, total_mib=total))

    try:
        free_mb = shutil.disk_usage(root).free // (1024 * 1024)
        if free_mb < MIN_FREE_DISK_MB:
            warnings.append(_finding('warning', 'LOW_DISK_SPACE', 'disk', free_mb=free_mb))
    except OSError:
        pass

    if not any((c / 'index.html').is_file() for c in (root / 'client', root.parent / 'EGO-Client')):
        warnings.append(_finding('warning', 'CLIENT_NOT_INSTALLED', 'client'))
    return {'ok': not errors, 'errors': errors, 'warnings': warnings}
