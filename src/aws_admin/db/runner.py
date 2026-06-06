"""Execute SQL with bound parameters under read-only / write / commit transaction modes."""
from __future__ import annotations

from dataclasses import dataclass
from typing import LiteralString, cast

from . import connection


@dataclass
class Result:
    columns: list[str]
    rows: list[tuple]
    rowcount: int
    committed: bool


def run_sql(sql: str, params: dict | None = None, *,
            write: bool = False, commit: bool = False, conn=None) -> Result:
    """Run one SQL statement. Read-only unless write=True; persists only if commit=True.

    Runs a single SQL statement; multi-statement scripts are not supported
    (rowcount/committed would reflect only the last statement).

    A caller-provided `conn` is used as-is and left open; otherwise a connection is
    opened (read-only iff not write) and closed before returning.

    A caller-supplied `conn` is used as-is and its read-only mode is the caller's
    responsibility.
    """
    if commit and not write:
        raise ValueError("--commit requires --write")
    params = params or {}
    owns = conn is None
    if owns:
        conn = connection.connect(read_only=not write)
    try:
        cur = conn.cursor()
        # Curated query files / user-supplied .sql; values are bound separately via
        # `params`, so the LiteralString guard doesn't apply to this threat model.
        cur.execute(cast(LiteralString, sql), params)
        columns = [c.name for c in cur.description] if cur.description else []
        rows = cur.fetchall() if cur.description else []
        rowcount = cur.rowcount
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
