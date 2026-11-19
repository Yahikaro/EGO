"""Session-wide test isolation for every suite under the EGO root."""
import os
import tempfile

import pytest


@pytest.fixture(autouse=True)
def _isolated_gui_settings(tmp_path, monkeypatch):
    # GUI preferences normally live in HKCU\Software\EGO; tests must never read
    # the developer's real model paths/cards or write into them.
    # Sibling of tmp_path: some tests scan every file inside tmp_path.
    directory = tmp_path.parent / (tmp_path.name + '-qsettings')
    directory.mkdir(exist_ok=True)
    monkeypatch.setenv('EGO_SETTINGS_DIR', str(directory))


# Subprocesses started outside a test function (module fixtures) inherit this.
os.environ.setdefault('EGO_SETTINGS_DIR', tempfile.mkdtemp(prefix='ego-qsettings-'))


@pytest.fixture(autouse=True)
def _russian_model_language():
    # The model language is remembered process-wide from the client's X-EGO-Lang header
    # (languages.py); every test starts from the installation default.
    from server.ego_core import languages
    languages._owner = languages.DEFAULT
    yield
    languages._owner = languages.DEFAULT
