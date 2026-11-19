"""Find GGUF models and llama-server on this PC for the AI models page (2026-11-06).

Only looks in a few known folders (no full-disk scan): EGO\\models, the folders of models
used in profiles, and folders the user picked before. Subfolders are searched one level deep.
"""
from __future__ import annotations

import fnmatch
from pathlib import Path

MAX_FILES = 200


def _folders(root, extra=()):
    root = Path(root)
    seen, out = set(), []
    for folder in (root / 'models', root.parent / 'models', *extra):
        try:
            folder = Path(folder)
            if not folder.is_dir():
                continue
            key = str(folder.resolve()).casefold()
        except (OSError, ValueError):
            continue
        if key not in seen:
            seen.add(key); out.append(folder)
    return out


def find_models(root, known_paths=(), folders=()):
    """Return [{'path', 'name', 'size'}] sorted by name; known_paths may point anywhere."""
    found = {}

    def add(path):
        try:
            path = Path(path)
            if path.suffix.lower() == '.gguf' and path.is_file():
                key = str(path.resolve()).casefold()
                if key not in found:
                    found[key] = {'path': str(path), 'name': path.name, 'size': path.stat().st_size}
        except (OSError, ValueError):
            pass

    parents = []
    for path in known_paths:
        if isinstance(path, str) and path.strip():
            add(path.strip()); parents.append(Path(path.strip()).parent)
    for folder in _folders(root, [*folders, *parents]):
        try:
            entries = list(folder.iterdir())
        except OSError:
            continue
        for entry in entries:
            if len(found) >= MAX_FILES:
                break
            try:
                if entry.is_dir():
                    for sub in entry.glob('*.gguf'):
                        add(sub)
                else:
                    add(entry)
            except OSError:
                continue
    # Split multi-part models (…-00002-of-00003.gguf): only the first part is loadable.
    rows = [r for r in found.values() if '-of-' not in r['name'] or '-00001-of-' in r['name']]
    return sorted(rows, key=lambda r: r['name'].casefold())


def match_preset(models, patterns):
    """First found model whose file name matches a preset pattern (case-insensitive)."""
    for row in models:
        if any(fnmatch.fnmatch(row['name'].casefold(), p.casefold()) for p in patterns):
            return row['path']
    return None


def find_llama_server(root, known=()):
    """llama-server.exe from the profile/preferences, else inside EGO\\runtime."""
    for path in known:
        if isinstance(path, str) and path.strip() and Path(path.strip()).is_file():
            return path.strip()
    root = Path(root)
    for folder in (root / 'runtime' / 'llama', root / 'runtime', root.parent / 'llama'):
        for name in ('llama-server.exe', 'llama-server'):
            candidate = folder / name
            if candidate.is_file():
                return str(candidate)
    return None


def size_text(size, lang='ru'):
    return f'{size / 1024 ** 3:.1f} ' + ('ГБ' if lang == 'ru' else 'GB')
