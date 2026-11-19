"""M1.6.1 RED test: backward-compatible Min-P config and Runtime propagation."""
from server.ego_core.config import CoreConfig


def test_min_p_defaults_to_disabled_and_reaches_runtime():
    cfg = CoreConfig(binary='C:/llama-server.exe', model='C:/model.gguf')
    assert cfg.min_p == 0.0
    assert cfg.runtime_config().min_p == 0.0


def test_min_p_is_configurable_without_changing_top_p():
    cfg = CoreConfig(binary='C:/llama-server.exe', model='C:/model.gguf', top_p=0.9, min_p=0.05)
    assert cfg.top_p == 0.9
    assert cfg.min_p == 0.05
    assert cfg.runtime_config().min_p == 0.05

import pytest
from pydantic import ValidationError
from runtime.adapters.llama_cpp import RuntimeConfig
from pathlib import Path

@pytest.mark.parametrize('value',[-.01,1.01,float('nan'),float('inf')])
def test_min_p_rejects_invalid_values(value):
    with pytest.raises(ValidationError):
        CoreConfig(min_p=value)
    with pytest.raises(ValueError):
        RuntimeConfig(Path('llama-server'),Path('model.gguf'),min_p=value)


def test_old_profile_json_loads_with_disabled_min_p():
    old=CoreConfig().model_dump()
    old.pop('min_p')
    assert CoreConfig.model_validate(old).min_p == 0.0
