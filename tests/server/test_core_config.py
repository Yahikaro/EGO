import importlib
from pathlib import Path
import pytest


def config_module():
    assert (Path(__file__).parents[2]/'server/ego_core/config.py').is_file(), 'M1 validated configuration missing'
    return importlib.import_module('server.ego_core.config')


def test_default_core_is_loopback_and_unconfigured():
    c = config_module().CoreConfig()
    assert c.host == '127.0.0.1' and c.api_port == 18443 and c.runtime_port == 18081
    assert c.runtime_config() is None


@pytest.mark.parametrize('values', [
    {'host':'0.0.0.0'}, {'host':'localhost'}, {'api_port':18081},
    {'api_port':80}, {'runtime_port':True}, {'binary':'https://example.com/server'},
    {'token':'must-not-be-configurable'}, {'context':100000},
])
def test_invalid_or_unsafe_configuration_is_rejected(values):
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        config_module().CoreConfig(**values)


def test_configuration_roundtrip_and_no_overwrite_on_bad_input(tmp_path):
    m=config_module()
    binary=tmp_path/'runtime space.exe'; binary.write_bytes(b'local')
    model=tmp_path/'модель.gguf'; model.write_bytes(b'GGUFtest')
    config=m.CoreConfig(binary=str(binary),model=str(model),context=2048)
    path=tmp_path/'config.json'
    m.save_config(path,config)
    assert m.load_config(path)==config
    assert config.runtime_config().port==18081
    before=path.read_bytes()
    with pytest.raises(Exception): m.save_config(path,{'host':'0.0.0.0'})
    assert path.read_bytes()==before
