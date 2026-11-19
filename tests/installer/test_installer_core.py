"""EGO setup logic (no window): unpacking, folder checks, llama.cpp asset choice, uninstall helpers."""
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'packaging' / 'installer'))
import installer_core as core  # noqa: E402


def make_zip(path, files):
    with zipfile.ZipFile(path, 'w') as z:
        for name, data in files.items():
            z.writestr(name, data)
    return path


def test_extract_unpacks_and_never_touches_user_data(tmp_path):
    archive = make_zip(tmp_path / 'p.zip', {'EGO.exe': 'x', 'client/index.html': '<html>', 'data/main.db': 'payload-data',
                                            'settings/profiles.json': '{}'})
    dest = tmp_path / 'EGO'
    (dest / 'data').mkdir(parents=True)
    (dest / 'data' / 'main.db').write_text('mine')
    seen = []
    assert core.extract(archive, dest, lambda f, name='': seen.append(f)) == 2
    assert (dest / 'EGO.exe').read_text() == 'x' and (dest / 'client' / 'index.html').exists()
    assert (dest / 'data' / 'main.db').read_text() == 'mine' and not (dest / 'settings').exists()
    assert seen and seen[-1] == pytest.approx(1.0)


def test_extract_refuses_paths_outside_the_folder(tmp_path):
    archive = make_zip(tmp_path / 'bad.zip', {'../evil.txt': 'x'})
    with pytest.raises(ValueError, match='UNSAFE_PATH'):
        core.extract(archive, tmp_path / 'EGO')
    assert not (tmp_path / 'evil.txt').exists()


def test_check_target(tmp_path):
    assert core.check_target(tmp_path / 'new' / 'EGO') is None                 # created when missing
    other = tmp_path / 'docs'
    other.mkdir()
    (other / 'a.txt').write_text('x')
    assert core.check_target(other) == 'NOT_EGO_FOLDER'                         # never install into a foreign folder
    mine = tmp_path / 'old'
    mine.mkdir()
    (mine / 'EGO.exe').write_text('x')
    assert core.check_target(mine) is None                                      # re-install over EGO is fine
    assert core.check_target(Path('')) in ('EMPTY', None, 'NOT_WRITABLE')


def test_pick_llama_assets_chooses_cuda12_x64():
    release = {'assets': [
        {'name': 'llama-b1-bin-win-cpu-x64.zip', 'browser_download_url': 'u/cpu'},
        {'name': 'llama-b1-bin-win-cuda-12.4-x64.zip', 'browser_download_url': 'u/cuda'},
        {'name': 'llama-b1-bin-win-cuda-13.1-x64.zip', 'browser_download_url': 'u/cuda13'},
        {'name': 'cudart-llama-bin-win-cuda-12.4-x64.zip', 'browser_download_url': 'u/rt'},
        {'name': 'llama-b1-bin-ubuntu-x64.zip', 'browser_download_url': 'u/linux'}]}
    assert core.pick_llama_assets(release) == {'llama': 'u/cuda', 'cudart': 'u/rt'}
    assert core.pick_llama_assets({'assets': []}) == {'llama': None, 'cudart': None}


def test_payload_size_and_default_dir(tmp_path, monkeypatch):
    archive = make_zip(tmp_path / 'p.zip', {'a': 'x' * 10, 'b/c': 'y' * 5})
    assert core.payload_size(archive) == 15
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    assert core.default_dir() == tmp_path / 'Programs' / 'EGO'


def test_shortcut_quoting():
    assert core._ps_quote("a'b") == "'a''b'"
