"""Explicit local paths and endpoints; no network discovery or secret config."""
from __future__ import annotations
import json
import os
from pathlib import Path
import stat
import tempfile
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from runtime.adapters.llama_cpp import RuntimeConfig


def linked(path: Path):
    try:
        return path.is_symlink() or (path.exists() and bool(getattr(path.lstat(), 'st_file_attributes', 0) & 0x400))
    except OSError:
        return True


class CoreConfig(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)
    host: Literal['127.0.0.1'] = '127.0.0.1'
    local_http: bool = True  # Loopback-only. Never bind HTTP to external interfaces.
    api_port: int = Field(default=18443, ge=1024, le=65535)
    runtime_port: int = Field(default=18081, ge=1024, le=65535)
    binary: str | None = Field(default=None, min_length=1, max_length=4096)
    model: str | None = Field(default=None, min_length=1, max_length=4096)
    # Defaults are a compatibility contract for old profiles (see test_m161);
    # tuned values live in RECOMMENDED_RTX3060 below.
    context: int = Field(default=2048, ge=256, le=32768)
    gpu_layers: int = Field(default=99, ge=0, le=999)
    startup_timeout: float = Field(default=120., gt=0, le=600)

    threads: int | None = Field(default=None, ge=1, le=1024)
    batch_size: int | None = Field(default=None, ge=1, le=65536)
    flash_attention: bool | None = None
    temperature: float = Field(default=0., ge=0, le=10, allow_inf_nan=False)
    top_p: float = Field(default=1., gt=0, le=1, allow_inf_nan=False)
    min_p: float = Field(default=0., ge=0, le=1, allow_inf_nan=False)
    top_k: int = Field(default=40, ge=0, le=100000)
    repeat_penalty: float = Field(default=1., gt=0, le=100, allow_inf_nan=False)
    max_tokens: int = Field(default=128, ge=1, le=4096)

    @model_validator(mode='after')
    def check_paths(self):
        if self.api_port == self.runtime_port:
            raise ValueError('DISTINCT_PORTS_REQUIRED')
        if bool(self.binary) != bool(self.model):
            raise ValueError('BINARY_AND_MODEL_REQUIRED')
        for name in ('binary', 'model'):
            value = getattr(self, name)
            if value is not None and ('://' in value or '\0' in value):
                raise ValueError('LOCAL_FILE_REQUIRED')
        return self

    def validate_files(self):
        """Validate external files only for explicit checks/apply/load, not status."""
        if self.binary is None or not Path(self.binary).is_file():
            raise ValueError('RUNTIME_NOT_PROVIDED')
        if self.model is None or not Path(self.model).is_file():
            raise ValueError('MODEL_NOT_PROVIDED')
        with Path(self.model).open('rb') as file:
            if file.read(4) != b'GGUF':
                raise ValueError('INVALID_GGUF')
        return self

    def runtime_config(self):
        if self.binary is None:
            return None
        return RuntimeConfig(Path(self.binary), Path(self.model), self.runtime_port,
                             self.context, self.gpu_layers, self.startup_timeout,
                             threads=self.threads, batch_size=self.batch_size,
                             flash_attention=self.flash_attention, temperature=self.temperature,
                             top_p=self.top_p, min_p=self.min_p, top_k=self.top_k,
                             repeat_penalty=self.repeat_penalty, max_tokens=self.max_tokens)


# Measured 2026-11-03 on RTX 3060 12 GB, llama.cpp b11724 CUDA 12.4, an
# 8B Q8_0 model (Llama 3.1 arch): load 9 s, ~10.6 GB VRAM incl. desktop, 34-38 tok/s.
# KV cache is ~128 KiB/token in f16, so 8192 ctx adds ~1 GB. Sampling follows
# the GGUF's general.sampling hints (temp 0.6, top_p 0.9). Apply via profile.
RECOMMENDED_RTX3060 = {
    'context': 8192, 'gpu_layers': 99, 'flash_attention': True,
    'temperature': 0.6, 'top_p': 0.9, 'min_p': 0.05, 'top_k': 40,
    'repeat_penalty': 1.05, 'max_tokens': 512,
}


def load_config(path: Path):
    if linked(path) or linked(path.parent):
        raise ValueError('CONFIG_PATH_UNSAFE')
    if not path.exists():
        return CoreConfig()
    if not path.is_file() or path.stat().st_size > 16384:
        raise ValueError('CONFIG_INVALID')
    return CoreConfig.model_validate_json(path.read_bytes())


def save_config(path: Path, config: CoreConfig):
    if not isinstance(config, CoreConfig):
        raise ValueError('CONFIG_INVALID')
    if linked(path) or linked(path.parent):
        raise ValueError('CONFIG_PATH_UNSAFE')
    config = CoreConfig.model_validate(config.model_dump())
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix='.config-', suffix='.tmp', delete=False) as f:
            temporary = Path(f.name)
            os.chmod(f.name, stat.S_IRUSR | stat.S_IWUSR)
            json.dump(config.model_dump(), f, ensure_ascii=True, indent=2)
            f.flush()
            os.fsync(f.fileno())
        load_config(temporary)
        if linked(path) or linked(path.parent):
            raise ValueError('CONFIG_PATH_UNSAFE')
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
