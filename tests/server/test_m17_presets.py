"""Measured runtime presets: values, merging rules and the GUI fill action."""
import pytest

from server.ego_core.config import CoreConfig
from server.ego_core.presets import (DEFAULT_PRESET_ID, MEASUREMENTS, PRESET_FIELDS, PRESETS,
                                     apply_preset, preset)
from test_control_window import window  # noqa: F401  (fixture reuse)

USER = {'binary': r'D:\llama\llama-server.exe', 'model': r'D:\models\model.gguf',
        'api_port': 18555, 'runtime_port': 18182, 'startup_timeout': 90.0,
        'context': 2048, 'max_tokens': 128, 'temperature': 0.0}


def test_every_preset_is_valid_measured_and_fits_rtx_3060():
    assert len({p['id'] for p in PRESETS}) == len(PRESETS)
    assert preset(DEFAULT_PRESET_ID)['values']['context'] == 8192
    for item in PRESETS:
        assert set(item['values']) == set(PRESET_FIELDS)
        CoreConfig.model_validate({**CoreConfig().model_dump(), **item['values']})
        m = MEASUREMENTS[item['id']]
        assert m['vram_peak_mib'] < 12288 - 1024, 'keeps >=1 GB headroom on a 12 GB card'
        assert m['prefill_tokens'] >= 0.85 * (item['values']['context'] - item['values']['max_tokens'])


def test_preset_keeps_user_paths_ports_and_timeout():
    merged = apply_preset(USER, 'long-scenes-12gb')
    for key in ('binary', 'model', 'api_port', 'runtime_port', 'startup_timeout'):
        assert merged[key] == USER[key]
    assert merged['context'] == 16384 and merged['max_tokens'] == 1024 and merged['temperature'] == 0.6


def test_unknown_preset_is_rejected():
    with pytest.raises(ValueError, match='PRESET_NOT_FOUND'):
        apply_preset(USER, 'nope')


def test_gui_fill_from_preset_changes_only_tuning_fields(window):
    value, backend, app = window
    value.model_fields['binary'].setText(USER['binary'])
    value.model_fields['model'].setText(USER['model'])
    value.model_fields['api_port'].setValue(18555)
    value.model_fields['max_tokens'].setValue(128)
    value.preset_combo.setCurrentIndex(value.preset_combo.findData('short-12gb'))
    assert '35.5' in value.preset_info.text()
    value.fill_from_preset()
    form = value._values()
    assert form['binary'] == USER['binary'] and form['model'] == USER['model'] and form['api_port'] == 18555
    assert form['context'] == 4096 and form['max_tokens'] == 256 and form['flash_attention'] is True
    assert abs(form['temperature'] - 0.7) < 1e-9 and value._dirty


def test_new_profile_starts_from_recommended_preset(window):
    value, backend, app = window
    value.new_profile()
    form = value._values()
    assert form['context'] == 8192 and form['max_tokens'] == 512 and abs(form['temperature'] - 0.6) < 1e-9
