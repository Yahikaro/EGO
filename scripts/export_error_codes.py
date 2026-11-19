"""Write docs/ERROR_CODES.md from server/ego_core/errors.py (run after editing the catalog).

  .venv/Scripts/python.exe scripts/export_error_codes.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def render():
    from server.ego_core.errors import CATALOG
    lines = ['# Коды ошибок EGO', '',
             'Генерируется из `server/ego_core/errors.py` скриптом `scripts/export_error_codes.py`.',
             'Код — стабильный машинный контракт API; тексты можно менять. Ответ API об ошибке:',
             '`{"code": "...", "message": "...", "hint": "..."}`. Полный каталог отдаёт `GET /v1/errors`.', '',
             '| Код | Что случилось | Что делать |', '|---|---|---|']
    for code in sorted(CATALOG):
        entry = CATALOG[code]
        lines.append(f"| `{code}` | {entry['message']} | {entry['hint']} |")
    return '\n'.join(lines) + '\n'


def main():
    (ROOT / 'docs' / 'ERROR_CODES.md').write_text(render(), encoding='utf-8', newline='\n')
    print('written docs/ERROR_CODES.md')


if __name__ == '__main__':
    main()
