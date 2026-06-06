"""Registry and loader for curated, read-only SQL queries shipped with the package."""
from __future__ import annotations

from pathlib import Path

_QUERIES_DIR = Path(__file__).resolve().parent.parent / "queries"

# name -> (filename, description). All curated queries are read-only.
CURATED: dict[str, tuple[str, str]] = {
    "unverified-users": ("unverified-users.sql", "Users with no verified email, newest first"),
    "verification-tokens": ("verification-tokens.sql", "10 most recent verification tokens"),
    "user-count": ("user-count.sql", "Total number of users"),
}


def is_curated(name: str) -> bool:
    return name in CURATED


def list_curated() -> list[tuple[str, str]]:
    return [(name, desc) for name, (_, desc) in CURATED.items()]


def load_query(name: str) -> str:
    if name not in CURATED:
        raise KeyError(name)
    filename, _ = CURATED[name]
    return (_QUERIES_DIR / filename).read_text(encoding="utf-8")
