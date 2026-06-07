"""Execute SQL with bound parameters under read-only / write / commit transaction modes."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import LiteralString, cast

from . import connection

_DOLLAR_TAG = re.compile(r"\$[A-Za-z_][A-Za-z0-9_]*\$|\$\$")


@dataclass
class Result:
    columns: list[str]
    rows: list[tuple]
    rowcount: int
    committed: bool


def split_statements(sql: str) -> list[str]:
    """Split a SQL script into statements on top-level semicolons.

    Quote- and comment-aware: semicolons inside 'single quotes' (incl. ''
    escapes), "double-quoted identifiers", $$dollar$$ / $tag$dollar$tag$
    strings, -- line comments, and /* block comments */ never split. Empty
    statements (e.g. a trailing semicolon) are dropped; comment text within a
    statement is preserved verbatim.
    """
    stmts: list[str] = []
    buf: list[str] = []
    i, n = 0, len(sql)
    state: str | None = None  # None | "'" | '"' | "--" | "/*" | "$"
    dollar_tag = ""
    while i < n:
        ch = sql[i]
        nxt = sql[i + 1] if i + 1 < n else ""
        if state is None:
            if ch == "'":
                state = "'"
            elif ch == '"':
                state = '"'
            elif ch == "-" and nxt == "-":
                state = "--"
            elif ch == "/" and nxt == "*":
                state = "/*"
            elif ch == "$":
                m = _DOLLAR_TAG.match(sql, i)
                if m:
                    dollar_tag = m.group(0)
                    state = "$"
                    buf.append(dollar_tag)
                    i = m.end()
                    continue
            elif ch == ";":
                stmt = "".join(buf).strip()
                if stmt:
                    stmts.append(stmt)
                buf = []
                i += 1
                continue
        elif state == "'":
            if ch == "'" and nxt == "'":  # '' escape stays inside the string
                buf.append("''")
                i += 2
                continue
            if ch == "'":
                state = None
        elif state == '"':
            if ch == '"':
                state = None
        elif state == "--":
            if ch == "\n":
                state = None
        elif state == "/*":
            if ch == "*" and nxt == "/":
                buf.append("*/")
                i += 2
                state = None
                continue
        elif state == "$":
            if sql.startswith(dollar_tag, i):
                buf.append(dollar_tag)
                i += len(dollar_tag)
                state = None
                continue
        buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        stmts.append(tail)
    return stmts


def run_sql(sql: str, params: dict | None = None, *,
            write: bool = False, commit: bool = False, conn=None) -> Result:
    """Run a SQL statement or script. Read-only unless write=True; persists only
    if commit=True.

    Multi-statement scripts (seeds/migrations) are split with split_statements
    and executed one statement at a time in a SINGLE transaction — all-or-
    nothing on --commit. psycopg's extended protocol forbids multiple commands
    per execute when parameters are bound, so per-statement execution is what
    makes `{{NAME}}` placeholders and scripts compose. The shared params
    mapping is passed to every statement (extra keys are ignored by psycopg).

    Result semantics for scripts: columns/rows come from the LAST statement
    that returns a result set; rowcount is summed across statements (negative
    per-statement counts, e.g. for DDL, are skipped).

    A caller-provided `conn` is used as-is and left open (its read-only mode is
    the caller's responsibility); otherwise a connection is opened (read-only
    iff not write) and closed before returning.
    """
    if commit and not write:
        raise ValueError("--commit requires --write")
    params = params or {}
    owns = conn is None
    if owns:
        conn = connection.connect(read_only=not write)
    try:
        cur = conn.cursor()
        columns: list[str] = []
        rows: list[tuple] = []
        rowcount = 0
        for stmt in split_statements(sql):
            # Curated query files / user-supplied .sql; values are bound separately
            # via `params`, so the LiteralString guard doesn't apply to this threat
            # model.
            cur.execute(cast(LiteralString, stmt), params)
            if cur.description:
                columns = [c.name for c in cur.description]
                rows = cur.fetchall()
            if cur.rowcount and cur.rowcount > 0:
                rowcount += cur.rowcount
        committed = False
        if write and commit:
            conn.commit()
            committed = True
        else:
            conn.rollback()
        return Result(columns=columns, rows=rows, rowcount=rowcount, committed=committed)
    finally:
        if owns:
            conn.close()
