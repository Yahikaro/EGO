"""Safely render local chat content to rich Qt HTML (no scripts or external resources)."""
from __future__ import annotations
import html
import re

INLINE = re.compile(r'(`[^`\n]+`|\*\*[^*\n]+\*\*|\*[^*\n]+\*|~~[^~\n]+~~)')


def inline_markdown(text: str) -> str:
    out = []
    for part in INLINE.split(text):
        if part.startswith('**') and part.endswith('**'):
            out.append('<b>' + html.escape(part[2:-2]) + '</b>')
        elif part.startswith('~~') and part.endswith('~~'):
            out.append('<s>' + html.escape(part[2:-2]) + '</s>')
        elif part.startswith('*') and part.endswith('*'):
            out.append('<i>' + html.escape(part[1:-1]) + '</i>')
        elif part.startswith('`') and part.endswith('`'):
            out.append('<code>' + html.escape(part[1:-1]) + '</code>')
        else:
            out.append(html.escape(part))
    return ''.join(out)


def render_markdown(text: str) -> str:
    """Supports bold, italic, code, quotations and fenced blocks, never raw HTML."""
    blocks = []
    fenced = False
    code = []
    for line in text.splitlines():
        if line.strip().startswith('```'):
            if fenced:
                blocks.append('<pre style="background-color:#0a1222; padding:9px; white-space:pre-wrap;">' + html.escape('\n'.join(code)) + '</pre>')
                code.clear()
            fenced = not fenced
            continue
        if fenced:
            code.append(line)
        elif line.startswith('> '):
            blocks.append('<div style="border-left:3px solid #74aaff; padding-left:10px; color:#b8c9e4;">' + inline_markdown(line[2:]) + '</div>')
        elif not line.strip():
            blocks.append('<div style="height:7px;"></div>')
        else:
            blocks.append('<div>' + inline_markdown(line) + '</div>')
    if fenced:
        blocks.append('<pre>' + html.escape('\n'.join(code)) + '</pre>')
    return ''.join(blocks)


def render_conversation(messages: list[dict]) -> str:
    """Qt-rich-text compatible message bubbles; user right, AI left.

    QTextBrowser supports only a subset of browser CSS, so table *cells*
    carry the background and explicit spacer columns carry alignment.
    """
    out = ['<html><body style="font-family:Segoe UI; font-size:14px; color:#eff4ff;">']
    for message in messages:
        role = message.get('role', 'unknown')
        user = role == 'user'
        title = 'ВЫ' if user else ('AI' if role == 'assistant' else html.escape(str(role).upper()))
        background = '#245083' if user else '#18283f'
        content = render_markdown(str(message.get('content', '')))
        bubble = (f'<td width="78%" bgcolor="{background}" '
                  f'style="background-color:{background}; padding:11px;">'
                  f'<font color="#b9d6ff" size="2"><b>{title}</b></font>'
                  f'<div style="margin-top:7px;">{content}</div></td>')
        spacer = '<td width="22%">&nbsp;</td>'
        cells = spacer + bubble if user else bubble + spacer
        out.append('<table width="100%" cellspacing="0" cellpadding="0" '
                   f'style="margin-bottom:12px;"><tr>{cells}</tr></table>'
                   '<div style="height:6px;"></div>')
    return ''.join(out) + '</body></html>'
