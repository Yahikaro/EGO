"""Bounded inspection of a user-selected local llama-server executable.

A CUDA build marker describes compilation only; it never verifies GPU execution.
No shell, discovery, download, or inference is performed here.
"""
from __future__ import annotations
import os
from pathlib import Path
import re
import struct
import subprocess
import threading

_OUTPUT_LIMIT = 128 * 1024


def _run(binary: Path, argument: str, timeout: float):
    allowed = {'PATH','SYSTEMROOT','WINDIR','TEMP','TMP','TMPDIR','USERPROFILE','HOME',
               'LOCALAPPDATA','APPDATA','CUDA_PATH','CUDA_VISIBLE_DEVICES','LD_LIBRARY_PATH'}
    process = subprocess.Popen([str(binary), argument], stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        env={k:v for k,v in os.environ.items() if k.upper() in allowed},
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    output = bytearray()
    exceeded = threading.Event()

    def read():
        try:
            while True:
                chunk = process.stdout.read(4096)
                if not chunk:
                    break
                if len(output) + len(chunk) > _OUTPUT_LIMIT:
                    exceeded.set()
                    process.kill()
                    break
                output.extend(chunk)
        except (OSError, ValueError):
            pass

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2)
        return None, 'RUNTIME_PROBE_TIMEOUT'
    finally:
        reader.join(timeout=1)
        if not reader.is_alive():
            process.stdout.close()
    if exceeded.is_set():
        return None, 'RUNTIME_PROBE_OUTPUT_LIMIT'
    if reader.is_alive():
        return None, 'RUNTIME_PROBE_TIMEOUT'
    if process.returncode:
        return None, 'RUNTIME_PROBE_FAILED'
    return output.decode('utf-8', errors='replace'), None


def probe_binary(path, *, timeout: float = 3.) -> dict:
    """Probe CLI syntax with at most two bounded processes (version and help)."""
    result = {'status':'BLOCKED', 'code':None, 'flags':[], 'version':None,
              'cuda_build':None, 'gpu_execution':'NOT_VERIFIED', 'options':{}}
    def fail(code, status='FAIL'):
        result.update(status=status, code=code)
        return result
    try:
        value = str(path).strip().strip('"')
        if '://' in value or '\0' in value or not value:
            return fail('RUNTIME_NOT_PROVIDED','BLOCKED')
        binary = Path(value).resolve()
        if not binary.is_file():
            return fail('RUNTIME_NOT_PROVIDED','BLOCKED')
        if not 0 < timeout <= 30:
            return fail('RUNTIME_PROBE_INVALID_TIMEOUT','BLOCKED')
        if os.name == 'nt':
            with binary.open('rb') as file:
                header = file.read(64)
                if len(header) != 64 or header[:2] != b'MZ':
                    return fail('RUNTIME_INCOMPATIBLE_BINARY','BLOCKED')
                offset = struct.unpack_from('<I',header,60)[0]
                if offset > min(binary.stat().st_size - 6, 16 * 1024 * 1024):
                    return fail('RUNTIME_INCOMPATIBLE_BINARY','BLOCKED')
                file.seek(offset)
                pe = file.read(6)
                if pe[:4] != b'PE\0\0' or pe[4:] != b'\x64\x86':
                    return fail('RUNTIME_INCOMPATIBLE_BINARY','BLOCKED')
        version, error = _run(binary, '--version', timeout)
        if error:
            return fail(error)
        match = re.search(r'(?i)\b(?:version|build)\s*[:=]?\s*([a-z0-9][a-z0-9._+-]{0,63})', version)
        if match:
            result['version'] = match.group(1)
        help_text, error = _run(binary, '--help', timeout)
        if error:
            return fail(error)
        if re.search(r'(?i)(unknown|unrecognized|invalid)\s+(option|argument)', help_text):
            return fail('RUNTIME_HELP_UNAVAILABLE')
        flags = sorted(set(re.findall(r'(?<![\w-])--[a-z][a-z0-9-]*', help_text)))
        if not flags or '--model' not in flags:
            return fail('RUNTIME_HELP_UNAVAILABLE')
        result['flags'] = flags
        # Parse declarations once. Wrapped examples must not overwrite arity,
        # and mere presence of a switch never authorizes invented values.
        for line in help_text.splitlines():
            if not re.match(r'^\s*(?:-[A-Za-z0-9]+,?\s+)?--[a-z]',line):
                continue
            for flag in re.findall(r'(?<![\w-])--[a-z][a-z0-9-]*',line):
                if flag in result['options']:
                    continue
                tail=line.split(flag,1)[1]
                takes=bool(re.match(r'\s+(?:[A-Z][A-Z0-9_]*\b|[<\[{])',tail))
                values=[]
                enum=re.search(r'\[([^]]+)\]',tail)
                if enum and {'on','off'}.issubset(set(enum.group(1).split('|'))):
                    values=[v for v in ('on','off','auto') if v in enum.group(1).split('|')]
                result['options'][flag]={'takes_value':takes or bool(values),'values':values}
        combined = version+'\n'+help_text
        if re.search(r'(?i)\b(?:GGML_)?CUDA\s*[:=]\s*(?:ON|1|TRUE)\b|compiled with CUDA',combined):
            result['cuda_build'] = True
        elif re.search(r'(?i)\b(?:GGML_)?CUDA\s*[:=]\s*(?:OFF|0|FALSE)\b|CPU.only build',combined):
            result['cuda_build'] = False
        return {**result, 'status':'PASS', 'code':None}
    except (OSError, ValueError, subprocess.SubprocessError):
        return fail('RUNTIME_PROBE_FAILED')


_PROBE_CACHE = {}
_PROBE_CACHE_LOCK = threading.Lock()


def probe_binary_cached(path, *, timeout: float = 3.) -> dict:
    """probe_binary for runtime starts: a successful result is reused while the
    binary file is unchanged (same path, size and modification time). Explicit
    "Check files & CLI" in the GUI still calls probe_binary directly."""
    try:
        binary = Path(str(path).strip().strip('"')).resolve()
        stat = binary.stat()
        key = (os.path.normcase(str(binary)), stat.st_size, stat.st_mtime_ns)
    except (OSError, ValueError):
        return probe_binary(path, timeout=timeout)
    with _PROBE_CACHE_LOCK:
        cached = _PROBE_CACHE.get(key)
    if cached is not None:
        return {**cached, 'options': dict(cached['options'])}
    result = probe_binary(path, timeout=timeout)
    if result.get('status') == 'PASS':
        with _PROBE_CACHE_LOCK:
            _PROBE_CACHE[key] = result
    return result
