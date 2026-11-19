"""AI models page (2026-11-06): found-model list, ready-made model presets."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from server.ego_core.presets import MODEL_PRESETS, PRESETS
from windows_ui.model_library import find_models, find_llama_server, match_preset


def _gguf(path, size=16):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'GGUF' + b'\0' * size)
    return path


def test_finds_models_in_ego_models_subfolders_and_known_paths(tmp_path):
    root = tmp_path / 'EGO'
    _gguf(root / 'models' / 'Model-12B.Q5_K_M.gguf')
    _gguf(root / 'models' / 'candidates' / 'Other.gguf')
    _gguf(root / 'models' / 'big-00002-of-00002.gguf')
    elsewhere = _gguf(tmp_path / 'far' / 'Model-8B.gguf')
    (root / 'models' / 'notes.txt').write_text('x')
    rows = find_models(root, [str(elsewhere)])
    names = [r['name'] for r in rows]
    assert names == ['Model-12B.Q5_K_M.gguf', 'Model-8B.gguf', 'Other.gguf']
    assert match_preset(rows, MODEL_PRESETS[0]['files']).endswith('Model-12B.Q5_K_M.gguf')
    assert match_preset(rows, ('nothing*.gguf',)) is None


def test_llama_server_is_found_inside_ego_runtime(tmp_path):
    exe = tmp_path / 'runtime' / 'llama' / 'llama-server.exe'
    exe.parent.mkdir(parents=True); exe.write_bytes(b'MZ')
    assert find_llama_server(tmp_path) == str(exe)
    assert find_llama_server(tmp_path / 'none') is None


def test_preset_names_carry_no_gpu_model():
    for item in (*PRESETS, *MODEL_PRESETS):
        for name in item['name']:
            assert 'RTX' not in name and '3060' not in name


class Backend:
    def execute(self, action, **_):
        if action == 'status': return {'status': 'PASS', 'core': {'state': 'OFFLINE'}}
        return {'status': 'PASS', 'details': {}}
    def history(self): return []
    def logs(self, **_): return []


@pytest.fixture
def window(tmp_path, monkeypatch):
    monkeypatch.setenv('EGO_SETTINGS_DIR', str(tmp_path / 'qsettings'))
    from windows_ui.window import ControlCenterWindow
    app = QApplication.instance() or QApplication([])
    root = tmp_path / 'EGO'
    _gguf(root / 'models' / 'Model-12B.Q5_K_M.gguf')
    exe = root / 'runtime' / 'llama' / 'llama-server.exe'; exe.parent.mkdir(parents=True); exe.write_bytes(b'MZ')
    value = ControlCenterWindow(root, backend=Backend())
    yield value, app
    value.poll_timer.stop(); value._allow_close = True; value.close(); value.deleteLater(); app.processEvents()


def test_ready_models_are_listed_and_fill_the_found_file(window):
    value, app = window
    data = [value.profile_combo.itemData(i) for i in range(value.profile_combo.count())]
    assert [d for d in data if d] == ['preset:' + m['id'] for m in MODEL_PRESETS]
    value.profile_combo.setCurrentIndex(value.profile_combo.findData('preset:model-12b'))
    assert value.model_fields['model'].text().endswith('Model-12B.Q5_K_M.gguf')
    assert value.model_choice.currentData() == value.model_fields['model'].text()
    assert value.model_fields['context'].value() == 16384
    assert value.model_fields['binary'].text().endswith('llama-server.exe')
    profile = value.save_profile()
    assert profile['name'] == value.t(*MODEL_PRESETS[0]['name'])
    # The saved profile replaces the ready entry of the same name; saving again does not duplicate.
    data = [value.profile_combo.itemData(i) for i in range(value.profile_combo.count())]
    assert 'preset:model-12b' not in data
    value.save_profile()
    assert len(value.configuration.snapshot()['profiles']) == 1


def test_missing_ready_model_asks_for_the_file(window):
    value, app = window
    value.profile_combo.setCurrentIndex(value.profile_combo.findData('preset:model-8b'))
    assert value.model_fields['model'].text() == ''
    assert 'GGUF' in value.profile_info.text()


def test_any_file_by_path_appears_in_the_list(window, tmp_path):
    value, app = window
    other = _gguf(tmp_path / 'elsewhere' / 'Mine.gguf')
    value.model_fields['model'].setText(str(other))
    assert value.model_choice.currentData() == str(other)
    value.model_fields['model'].setText(str(tmp_path / 'gone.gguf'))
    assert 'gone.gguf' in value.model_choice.currentText()
