"""Write the API contract snapshot used by test_m17_api_contract.py.

Run after an intentional, reviewed API change:
  .venv/Scripts/python.exe scripts/export_openapi.py
"""
from __future__ import annotations
import json
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SNAPSHOT = ROOT / 'docs' / 'contracts' / 'm1-openapi.json'


def contract():
    from server.ego_core.api import create_app
    class _Service:  # Schema generation never touches storage or a runtime.
        installation_id = None
        last_api_error = None
    schema = create_app(_Service(), secrets.token_urlsafe(32)).openapi()
    # The package version changes every release; the contract is paths + schemas.
    schema['info'] = {'title': schema['info']['title'], 'version': 'contract'}
    return schema


def main():
    SNAPSHOT.write_text(json.dumps(contract(), ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    print(f'written {SNAPSHOT.relative_to(ROOT)}')


if __name__ == '__main__':
    main()
