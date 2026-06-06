"""PostgreSQL admin commands. Results default to a file; only redacted summaries,
counts, and static text are returned. Sensitive literals and the password never
appear in any return value."""
from __future__ import annotations

import getpass
from pathlib import Path

from .. import config, vault
from ..db import connection, placeholders, queries, results, runner


def set_password(_getpass=getpass.getpass) -> None:
    """Prompt (hidden) for the DB password and store it encrypted."""
    vault.set_db_password(_getpass("PostgreSQL password: "))


def check(conn=None) -> str:
    owns = conn is None
    if owns:
        conn = connection.connect(read_only=True)
    try:
        conn.cursor().execute("SELECT 1")
        return f"connected as {config.DB_USER}@{config.DB_HOST}/{config.DB_NAME} (read-only)"
    finally:
        if owns:
            conn.close()


def list_queries() -> str:
    lines = [f"{name}  —  {desc}" for name, desc in queries.list_curated()]
    return "\n".join(lines)


def run(target: str, *, write: bool = False, commit: bool = False, show: bool = False,
        conn=None, _collect_values=None) -> str:
    _collect_values = _collect_values or placeholders.collect_values

    if queries.is_curated(target):
        if write:
            raise ValueError(
                f"'{target}' is a curated read-only query; --write is not allowed."
            )
        sql_text = queries.load_query(target)
        name = target
    else:
        path = Path(target)
        if not path.exists():
            raise FileNotFoundError(
                f"No curated query or file named '{target}'. See `aws-admin db list`."
            )
        sql_text = path.read_text(encoding="utf-8")
        name = path.stem

    names = placeholders.find_placeholders(sql_text)
    params = _collect_values(names) if names else {}
    sql = placeholders.to_psycopg(sql_text)

    result = runner.run_sql(sql, params, write=write, commit=commit, conn=conn)

    if result.columns:
        path = results.write_results(name, result.columns, result.rows)
        out = results.summary(name, result.columns, result.rows, path)
        if show:
            out += "\n" + results.render_inline(result.columns, result.rows)
        return out

    if result.committed:
        return f"{name}: {result.rowcount} row(s) changed and committed."
    if write:
        return (f"{name}: {result.rowcount} row(s) would change (rolled back). "
                f"Re-run with --commit to persist.")
    return f"{name}: statement executed (no result rows)."


def clean_results() -> int:
    return results.clean_results()
