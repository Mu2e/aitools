"""Markdown, as models write it, to what Slack shows correctly and safely.

Slack's own markup ("mrkdwn") has single asterisks for bold, links as
``<url|text>``, no headings and no tables. It also gives angle brackets a
meaning: ``<!channel>`` notifies a whole channel, ``<@U123>`` a person. So
the text is not passed through: every ``&``, ``<`` and ``>`` the caller
wrote is escaped, and the only angle brackets that reach Slack are the links
built here, to http(s) addresses. Nothing a caller writes can notify anyone.
"""

from __future__ import annotations

import re

CHUNK = 3500  # characters per Slack message; Slack cuts long ones

_MD_LINK = re.compile(r"\[([^\]\n]+)\]\((https?://[^)\s<>|]+)\)")
_AUTO_LINK = re.compile(r"<(https?://[^>\s<|]+)>")
_BOLD = re.compile(r"\*\*(.+?)\*\*", re.S)
_UNDERSCORE_BOLD = re.compile(r"__(.+?)__", re.S)
_HEADER = re.compile(r"^#{1,6}\s+(.+)$", re.M)
_BULLET = re.compile(r"^(\s*)[-*]\s+", re.M)
# "@channel" in plain text does not notify (link_names is off), but it
# should not look as if it did either.
_AT_ALL = re.compile(r"@(channel|here|everyone)\b", re.I)
_HOLD = "\x00{}\x00"


def escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _prose(text: str) -> str:
    links: list[str] = []

    def hold(url: str, label: str | None) -> str:
        links.append(f"<{url}|{escape(label)}>" if label else f"<{url}>")
        return _HOLD.format(len(links) - 1)

    text = text.replace("\x00", "")
    text = _MD_LINK.sub(lambda m: hold(m.group(2), m.group(1)), text)
    text = _AUTO_LINK.sub(lambda m: hold(m.group(1), None), text)
    text = escape(text)
    text = _HEADER.sub(r"*\1*", text)
    text = _BOLD.sub(r"*\1*", text)
    text = _UNDERSCORE_BOLD.sub(r"*\1*", text)
    text = _BULLET.sub("\\1\u2022 ", text)
    text = _AT_ALL.sub("@\u200b\\1", text)
    return re.sub(r"\x00(\d+)\x00", lambda m: links[int(m.group(1))], text)


def to_mrkdwn(text: str) -> str:
    """Markdown to Slack's markup; code fences keep their content (escaped)."""
    parts = (text or "").split("```")
    for i, part in enumerate(parts):
        if i % 2:
            parts[i] = escape(part.replace("\x00", ""))
            continue
        part = _prose(part)
        # Slack sets a code block off by itself: an empty line next to one
        # shows as an extra gap.
        if i > 0 and part.strip():
            part = "\n" + part.lstrip("\n")
        if i < len(parts) - 1 and part.strip():
            part = part.rstrip("\n") + "\n"
        parts[i] = part
    return "```".join(parts)


def chunks(text: str, size: int = CHUNK) -> list[str]:
    """Split on line boundaries into pieces of at most ``size`` characters.
    A code fence that is open where a piece ends is closed there and opened
    again in the next piece."""
    out, current, fenced = [], "", False
    lines = []
    for line in text.splitlines(keepends=True):
        while len(line) > size - 8:
            lines.append(line[:size - 8])
            line = line[size - 8:]
        lines.append(line)
    for line in lines:
        if current and len(current) + len(line) > size - 8:
            out.append(current + ("\n```" if fenced else ""))
            current = "```\n" if fenced else ""
        current += line
        if line.count("```") % 2:
            fenced = not fenced
    if current.strip():
        out.append(current)
    return out
