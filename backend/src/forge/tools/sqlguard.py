"""Read-only SQL for tools (US-2.2.7): a literal-aware scanner that finds `:name` parameters,
rejects anything that is not one SELECT/WITH statement, and rewrites to `$n` placeholders.

The scanner is a first line of defence: the statement also runs in a READ ONLY transaction
with a statement timeout, which is what actually stops writes."""

from __future__ import annotations

import re
from dataclasses import dataclass

FORBIDDEN = frozenset(
    {
        "insert",
        "update",
        "delete",
        "drop",
        "alter",
        "create",
        "truncate",
        "grant",
        "revoke",
        "copy",
        "call",
        "do",
        "execute",
        "vacuum",
        "analyze",
        "reindex",
        "cluster",
        "listen",
        "notify",
        "lock",
        "refresh",
        "merge",
        "comment",
        "security",
        "reset",
        "set",
        "show",
        "discard",
        "prepare",
        "deallocate",
        "declare",
        "fetch",
        "move",
        "into",
        "pg_read_file",
        "pg_read_binary_file",
        "pg_ls_dir",
        "pg_stat_file",
        "lo_import",
        "lo_export",
        "dblink",
        "set_config",
        "pg_sleep",
        "pg_terminate_backend",
        "pg_cancel_backend",
        "pg_reload_conf",
    }
)
IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*")


class SqlRejected(ValueError):
    pass


@dataclass
class ParsedSql:
    text: str  # with $1, $2, … placeholders
    names: list[str]  # parameter names in placeholder order (each name once)


def parse(query: str) -> ParsedSql:
    out: list[str] = []
    words: list[str] = []
    order: dict[str, int] = {}
    i, n = 0, len(query)
    semicolons = 0
    while i < n:
        c = query[i]
        nxt = query[i + 1] if i + 1 < n else ""
        if c == "'":  # string literal ('' escapes)
            j = i + 1
            while j < n:
                if query[j] == "'" and query[j + 1 : j + 2] == "'":
                    j += 2
                elif query[j] == "'":
                    break
                else:
                    j += 1
            out.append(query[i : j + 1])
            i = j + 1
        elif c == '"':  # quoted identifier
            j = query.find('"', i + 1)
            if j < 0:
                raise SqlRejected("unterminated quoted identifier")
            out.append(query[i : j + 1])
            i = j + 1
        elif c == "$" and (nxt == "$" or IDENT.match(nxt or " ")):
            raise SqlRejected("dollar-quoted strings are not allowed")
        elif (c == "-" and nxt == "-") or (c == "/" and nxt == "*"):
            raise SqlRejected("comments are not allowed")
        elif c == ";":
            semicolons += 1
            if query[i + 1 :].strip():
                raise SqlRejected("only one statement is allowed")
            i += 1
        elif c == ":" and nxt == ":":  # a cast
            out.append("::")
            i += 2
        elif c == ":" and IDENT.match(nxt or " "):
            m = IDENT.match(query, i + 1)
            assert m
            name = m.group(0).lower()
            if name not in order:
                order[name] = len(order) + 1
            out.append(f"${order[name]}")
            i = m.end()
        elif IDENT.match(c):
            m = IDENT.match(query, i)
            assert m
            words.append(m.group(0).lower())
            out.append(m.group(0))
            i = m.end()
        else:
            out.append(c)
            i += 1
    if not words or words[0] not in ("select", "with"):
        raise SqlRejected("the statement must start with SELECT or WITH")
    bad = sorted(FORBIDDEN & set(words))
    if bad:
        raise SqlRejected(f"not allowed in a read-only query: {', '.join(bad)}")
    return ParsedSql("".join(out).strip(), list(order))
