"""`{{NAME}}` placeholders: detect, translate to psycopg named params, and collect
values from the user via a shredded editor buffer (never through the prompt)."""
from __future__ import annotations

import re

from .. import vault

_TOKEN = re.compile(r"\{\{([A-Za-z_][A-Za-z0-9_]*)\}\}")


def find_placeholders(sql: str) -> list[str]:
    """Distinct placeholder names, in first-seen order."""
    seen: list[str] = []
    for m in _TOKEN.finditer(sql):
        if m.group(1) not in seen:
            seen.append(m.group(1))
    return seen


def to_psycopg(sql: str) -> str:
    """Translate {{NAME}} -> %(NAME)s for psycopg named-parameter binding."""
    return _TOKEN.sub(lambda m: f"%({m.group(1)})s", sql)


def render_values_buffer(names: list[str]) -> str:
    header = "# Fill each value, then save & close. Values are bound as parameters\n"
    header += "# (not interpolated) and this buffer is shredded after.\n"
    return header + "".join(f"{n}=\n" for n in names)


def parse_values_buffer(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.rstrip("\r")
    return values


def collect_values(names: list[str], _open_editor=None) -> dict[str, str]:
    """Open an editor buffer for the placeholder values; return a bound-params dict.

    Raises ValueError if any placeholder is left unfilled.
    """
    text = vault.edit_buffer(render_values_buffer(names), _open_editor=_open_editor)
    parsed = parse_values_buffer(text)
    missing = [n for n in names if not parsed.get(n)]
    if missing:
        raise ValueError(f"placeholder(s) left unfilled: {', '.join(missing)}")
    return {n: parsed[n] for n in names}
