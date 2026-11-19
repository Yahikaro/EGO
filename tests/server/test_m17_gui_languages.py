"""Control Center languages (2026-11-06): every (Russian, English) text has fr/es/de."""
import ast
import io
import os
import re
import tokenize
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import pytest

from server.ego_core.presets import MODEL_PRESETS, PRESETS
from windows_ui.translations import TEXTS, translate

ROOT = Path(__file__).resolve().parents[2]
CYR = re.compile('[А-Яа-яЁё]')


def _pairs(path):
    toks = [t for t in tokenize.generate_tokens(io.StringIO(path.read_text(encoding='utf-8')).readline)
            if t.type not in (tokenize.NL, tokenize.NEWLINE, tokenize.COMMENT, tokenize.INDENT, tokenize.DEDENT)]
    for a, b, c in zip(toks, toks[1:], toks[2:]):
        if a.type == c.type == tokenize.STRING and b.string == ',' and a.string[:1] not in 'fF' and c.string[:1] not in 'fF':
            ru, en = ast.literal_eval(a.string), ast.literal_eval(c.string)
            if isinstance(ru, str) and isinstance(en, str) and CYR.search(ru) and not CYR.search(en) and en.strip():
                yield ru, en


def test_every_window_text_is_translated():
    texts = list(_pairs(ROOT / 'windows_ui/window.py'))
    texts += [item['name'] for item in (*PRESETS, *MODEL_PRESETS)] + [item['description'] for item in (*PRESETS, *MODEL_PRESETS)]
    assert len(texts) > 150
    for lang in ('fr', 'es', 'de'):
        missing = sorted({en.strip() for _, en in texts if en.strip() not in TEXTS[lang]})
        assert not missing, (lang, missing[:10])
    assert translate('de', 'Готово', ' File found. Click Save, then Apply.').startswith(' Datei gefunden')
    assert translate('ru', 'Готово', 'Ready') == 'Готово'
    assert translate('es', 'x', 'Not in the list') == 'Not in the list'


class Backend:
    def execute(self, action, **_):
        return {'status': 'PASS', 'core': {'state': 'OFFLINE'}} if action == 'status' else {'status': 'PASS', 'details': {}}
    def history(self): return []
    def logs(self, **_): return []


@pytest.mark.parametrize('lang,home', [('fr', 'Général'), ('es', 'General'), ('de', 'Allgemein')])
def test_window_switches_language(tmp_path, monkeypatch, lang, home):
    monkeypatch.setenv('EGO_SETTINGS_DIR', str(tmp_path / 'qsettings'))
    from PySide6.QtWidgets import QApplication
    from windows_ui.window import ControlCenterWindow, LANGUAGES
    app = QApplication.instance() or QApplication([])
    window = ControlCenterWindow(tmp_path / 'EGO', backend=Backend())
    try:
        window.language_combo.setCurrentIndex([k for k, _ in LANGUAGES].index(lang))
        window.save_preferences()
        assert window.configuration.snapshot()['gui']['language'] == lang
        assert window.navigation.item(0).text() == home
    finally:
        window.poll_timer.stop(); window._allow_close = True; window.close(); window.deleteLater(); app.processEvents()
