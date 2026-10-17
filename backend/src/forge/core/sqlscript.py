"""Split SQL scripts into single statements (asyncpg executes one statement per call)."""

from __future__ import annotations

import re

_DOLLAR = re.compile(r"\$[A-Za-z_]*\$")


def statements(script: str) -> list[str]:
    """Split on `;` outside quotes, dollar-quoted bodies and `--` comments."""
    out: list[str] = []
    buf: list[str] = []
    i, n = 0, len(script)
    quote: str | None = None  # "'" or a dollar tag like "$$"
    while i < n:
        ch = script[i]
        if quote:
            if script.startswith(quote, i):
                buf.append(quote)
                i += len(quote)
                quote = None
                continue
            buf.append(ch)
            i += 1
            continue
        if ch == "-" and script.startswith("--", i):
            end = script.find("\n", i)
            i = n if end == -1 else end
            continue
        if ch == "'":
            quote = "'"
            buf.append(ch)
            i += 1
            continue
        if ch == "$" and (m := _DOLLAR.match(script, i)):
            quote = m.group(0)
            buf.append(quote)
            i += len(quote)
            continue
        if ch == ";":
            stmt = "".join(buf).strip()
            if stmt:
                out.append(stmt)
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        out.append(tail)
    return out
