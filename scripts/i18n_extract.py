"""Collect the Russian UI strings of the client into client/js/i18n/strings.json.

Keys are the Russian texts as they appear in the page. Parts of template strings
between ${...} become {0}, {1}... (patterns). Run after changing client texts:

    .venv\\Scripts\\python.exe scripts\\i18n_extract.py
"""
from __future__ import annotations

import html
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLIENT = ROOT / 'client'
CYR = re.compile('[А-Яа-яЁё]')
TAG = re.compile(r'<[^<>]*>')
ATTR = re.compile(r'\b(?:title|placeholder|aria-label|alt|data-tip)\s*=\s*"([^"]*)"|\b(?:title|placeholder|aria-label|alt|data-tip)\s*=\s*\'([^\']*)\'')
SKIP_FILES = {'js/i18n', 'js/core/i18n.js'}


def _skip_literal(source, j):
    """Index just after the literal starting at source[j] (quote or backtick)."""
    q, k, n = source[j], j + 1, len(source)
    if q != '`':
        while k < n and source[k] != q and source[k] != '\n':
            k += 2 if source[k] == '\\' else 1
        return k + 1
    depth = 0
    while k < n:
        ch = source[k]
        if ch == '\\': k += 2; continue
        if depth == 0 and ch == '`': return k + 1
        if source.startswith('${', k): depth += 1; k += 2; continue
        if depth and ch in '\'"`': k = _skip_literal(source, k); continue
        if depth and ch == '{': depth += 1
        elif depth and ch == '}': depth -= 1
        k += 1
    return n


def literals(source):
    """String literals of a JS file (approximate tokenizer: comments skipped).
    Literals nested inside ${...} are yielded too."""
    i, n = 0, len(source)
    while i < n:
        c = source[i]
        if c == '/' and source.startswith('//', i):
            i = source.find('\n', i); i = n if i < 0 else i; continue
        if c == '/' and source.startswith('/*', i):
            i = source.find('*/', i + 2); i = n if i < 0 else i + 2; continue
        if c in '\'"':
            j = _skip_literal(source, i)
            yield source[i + 1:j - 1], False
            i = j; continue
        if c == '`':
            j, buf, exprs = i + 1, [], []
            while j < n:
                ch = source[j]
                if ch == '\\': buf.append(source[j:j + 2]); j += 2; continue
                if ch == '`': break
                if source.startswith('${', j):
                    k, depth = j + 2, 1
                    while k < n and depth:
                        if source[k] in '\'"`': k = _skip_literal(source, k); continue
                        if source[k] == '{': depth += 1
                        elif source[k] == '}': depth -= 1
                        k += 1
                    exprs.append(source[j + 2:k - 1]); buf.append('\0x\1'); j = k; continue
                buf.append(ch); j += 1
            yield ''.join(buf), True
            for e in exprs:
                yield from literals(e)
            i = j + 1; continue
        i += 1


def unescape(text):
    text = re.sub(r'\\u([0-9a-fA-F]{4})', lambda m: chr(int(m.group(1), 16)), text)
    return text.replace("\\'", "'").replace('\\"', '"').replace('\\n', '\n').replace('\\`', '`').replace('\\\\', '\\')


def pieces(text, template):
    """Visible text pieces of a literal: split on tags, ${} -> {n}."""
    out = []
    for value in ATTR.findall(text):
        for v in value:
            if v: out.append(v)
    for part in TAG.split(text):
        if template:
            k = 0
            def ph(_m):
                nonlocal k
                k += 1
                return '{%d}' % (k - 1)
            part = re.sub('\0[^\1]*\1', ph, part)
        out.append(part)
    result = []
    for p in out:
        p = html.unescape(unescape(p))
        for line in p.split('\n'):
            line = re.sub(r'\s+', ' ', line).strip()
            if line and CYR.search(line) and len(line) <= 600 and '\0' not in line and '\1' not in line:
                result.append(line)
    return result


def collect():
    """{Russian text: file} for every visible Russian string of the client."""
    found = {}
    files = [CLIENT / 'index.html', *sorted(CLIENT.glob('js/**/*.js'))]
    for path in files:
        rel = path.relative_to(CLIENT).as_posix()
        if any(rel.startswith(s) for s in SKIP_FILES):
            continue
        source = path.read_text(encoding='utf-8')
        if path.suffix == '.html':
            items = [(source, False)]
        else:
            items = list(literals(source))
        for text, template in items:
            if not CYR.search(text):
                continue
            for piece in pieces(text, template):
                found.setdefault(piece, rel)
    return found


def main():
    found = collect()
    out = CLIENT / 'js' / 'i18n' / 'strings.json'
    out.parent.mkdir(parents=True, exist_ok=True)
    data = dict(sorted(found.items(), key=lambda kv: (kv[1], kv[0])))
    out.write_text(json.dumps(data, ensure_ascii=False, indent=0), encoding='utf-8')
    patterns = sum(1 for k in data if '{0}' in k)
    print(f'{len(data)} strings ({patterns} patterns), {sum(map(len, data))} chars -> {out.relative_to(ROOT)}')


if __name__ == '__main__':
    main()
