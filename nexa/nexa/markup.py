"""Minimal Markdown renderer for model and knowledge answers.

The input is HTML-escaped before any markup is produced, so the only tags in the output are the
ones generated here. Links are limited to http(s) and mailto targets.
"""
import re

from django.utils.html import escape
from django.utils.safestring import mark_safe

INLINE = (
    (re.compile(r"\*\*(.+?)\*\*"), r"<strong>\1</strong>"),
    (re.compile(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])"), r"<em>\1</em>"),
    (re.compile(r"\[([^\]\n]+)\]\(((?:https?://|mailto:)[^\s)\"'<>]+)\)"),
     r'<a href="\2" rel="noopener noreferrer" target="_blank">\1</a>'),
)
HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
ORDERED = re.compile(r"^\s*\d+[.)]\s+(.*)$")
BULLET = re.compile(r"^\s*[-*+]\s+(.*)$")
FENCE = re.compile(r"^\s*```")


def inline(text):
    parts = re.split(r"(`[^`\n]+`)", escape(text))
    for index, part in enumerate(parts):
        if index % 2:
            parts[index] = f"<code>{part[1:-1]}</code>"
            continue
        for pattern, replacement in INLINE:
            part = pattern.sub(replacement, part)
        parts[index] = part
    return "".join(parts)


def render(text):
    html, paragraph, items, kind, code = [], [], [], None, None

    def flush():
        nonlocal kind
        if paragraph:
            html.append("<p>" + "<br>".join(inline(line) for line in paragraph) + "</p>")
            paragraph.clear()
        if items:
            html.append(f"<{kind}>" + "".join(f"<li>{inline(item)}</li>" for item in items) + f"</{kind}>")
            items.clear()
            kind = None

    for line in str(text).replace("\r\n", "\n").split("\n"):
        if code is not None:
            if FENCE.match(line):
                html.append('<pre dir="ltr"><code>' + escape("\n".join(code)) + "</code></pre>")
                code = None
            else:
                code.append(line)
            continue
        if FENCE.match(line):
            flush()
            code = []
            continue
        if not line.strip():
            flush()
            continue
        if heading := HEADING.match(line):
            flush()
            level = min(len(heading.group(1)) + 2, 6)
            html.append(f"<h{level}>{inline(heading.group(2))}</h{level}>")
            continue
        for pattern, list_kind in ((ORDERED, "ol"), (BULLET, "ul")):
            if match := pattern.match(line):
                if paragraph or kind not in (None, list_kind):
                    flush()
                kind = list_kind
                items.append(match.group(1))
                break
        else:
            if items:
                flush()
            paragraph.append(line)
    if code is not None:
        html.append('<pre dir="ltr"><code>' + escape("\n".join(code)) + "</code></pre>")
    flush()
    return mark_safe("".join(html))
