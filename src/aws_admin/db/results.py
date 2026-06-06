"""Persist query results to a 0600 file and render redacted summaries.

The summary (default output) contains only key/structural info — never row values.
Row values appear only in the on-disk file and in render_inline (used by --show)."""
from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from pathlib import Path

from .. import config


_FORMULA_PREFIXES = ("=", "+", "-", "@")


def _sanitize_cell(value):
    """Render a cell as text, guarding against CSV/spreadsheet formula injection.

    A value beginning with =, +, -, or @ is prefixed with a single quote so a
    spreadsheet won't execute it — EXCEPT plain numbers (e.g. -5, +3.2), which are
    left intact. DB content (emails, names) is attacker-controllable, so this guards
    the files an operator opens.
    """
    if value is None:
        return ""
    s = value if isinstance(value, str) else str(value)
    if s and s[0] in _FORMULA_PREFIXES:
        try:
            float(s)  # numeric values are safe, pass through
            return s
        except ValueError:
            return "'" + s
    return s


def write_results(name: str, columns: list[str], rows: list[tuple]) -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    directory = config.results_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}-{ts}.csv"
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(columns)
        writer.writerows([_sanitize_cell(v) for v in row] for row in rows)
    path.chmod(0o600)
    return path


def summary(name: str, columns: list[str], rows: list[tuple], path) -> str:
    n = len(rows)
    cols = ", ".join(columns)
    plural = "row" if n == 1 else "rows"
    return f"{name}: {n} {plural}, columns: [{cols}], written to {path}"


def render_inline(columns: list[str], rows: list[tuple]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(columns)
    writer.writerows([_sanitize_cell(v) for v in row] for row in rows)
    return buf.getvalue().rstrip("\r\n")


def clean_results() -> int:
    directory = config.results_dir()
    if not directory.exists():
        return 0
    count = 0
    for entry in directory.iterdir():
        if entry.is_file():
            entry.unlink()
            count += 1
    return count
