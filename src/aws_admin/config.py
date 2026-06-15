"""Paths, constants, and Amplify app-alias resolution.

Sensitive deployment values (account ID, RDS host, DB name, Amplify app IDs)
are NOT hardcoded here. They are loaded at runtime from
``$AWS_ADMIN_HOME/config.toml`` (default ``~/.config/aws-admin/config.toml``);
copy ``config.example.toml`` there and fill in your values.
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

# --- Non-sensitive generic constants ---
REGION = "us-east-1"
DEFAULT_BRANCH = "main"
DB_PORT = 5432
DB_USER = "postgres"
DB_SSLMODE = "require"


def state_dir() -> Path:
    """Directory holding the vault key, snapshots, config, and backups.

    Honors AWS_ADMIN_HOME (used by tests); defaults to ~/.config/aws-admin.
    Lives outside the repo on purpose.
    """
    override = os.environ.get("AWS_ADMIN_HOME")
    if override:
        return Path(override)
    return Path.home() / ".config" / "aws-admin"


def config_path() -> Path:
    return state_dir() / "config.toml"


def key_path() -> Path:
    return state_dir() / "vault.key"


def vault_path(app_name: str) -> Path:
    return state_dir() / "vaults" / f"{app_name}.enc"


def backup_path(app_name: str, timestamp: str) -> Path:
    return state_dir() / "backups" / f"{app_name}-{timestamp}.enc"


def db_password_path() -> Path:
    return state_dir() / "db-password.enc"


def results_dir() -> Path:
    return state_dir() / "results"


@dataclass(frozen=True)
class AppRef:
    name: str
    app_id: str


@dataclass(frozen=True)
class Settings:
    account_id: str
    db_host: str
    db_name: str
    # canonical name -> (app_id, alias tokens)
    apps: dict[str, tuple[str, tuple[str, ...]]]


class ConfigError(RuntimeError):
    """Raised when config.toml is missing or malformed."""


class UnknownAppError(ValueError):
    """Raised when an app token matches no known app."""


@lru_cache(maxsize=None)
def _load_settings_cached(state: str) -> Settings:
    path = Path(state) / "config.toml"
    if not path.exists():
        raise ConfigError(
            f"No config at {path}. Copy config.example.toml there and fill in "
            f"your AWS account ID, RDS host, DB name, and Amplify app IDs."
        )
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    try:
        account_id = os.environ.get("AWS_ADMIN_ACCOUNT_ID") or data["account_id"]
        db = data["database"]
        db_host = os.environ.get("AWS_ADMIN_DB_HOST") or db["host"]
        db_name = os.environ.get("AWS_ADMIN_DB_NAME") or db["name"]
        apps = {
            name: (spec["app_id"], tuple(spec.get("aliases", [])))
            for name, spec in data.get("apps", {}).items()
        }
    except (KeyError, TypeError) as exc:
        raise ConfigError(f"Malformed config at {path}: missing key {exc}") from exc
    return Settings(account_id=account_id, db_host=db_host, db_name=db_name, apps=apps)


def _settings() -> Settings:
    return _load_settings_cached(str(state_dir()))


# PEP 562: resolve sensitive constants lazily so existing call sites
# (config.ACCOUNT_ID / config.DB_HOST / config.DB_NAME) are unchanged.
def __getattr__(name: str):
    settings = _settings()
    if name == "ACCOUNT_ID":
        return settings.account_id
    if name == "DB_HOST":
        return settings.db_host
    if name == "DB_NAME":
        return settings.db_name
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def known_apps() -> list[AppRef]:
    """Every configured app, sorted by name (case-insensitive)."""
    return sorted(
        (AppRef(name, app_id) for name, (app_id, _) in _settings().apps.items()),
        key=lambda ref: ref.name.lower(),
    )


def app_aliases() -> list[tuple[str, tuple[str, ...]]]:
    """(canonical name, alias tokens) per configured app, sorted by name.

    Deliberately omits app IDs — help output stays to non-sensitive
    names/acronyms only.
    """
    return sorted(
        ((name, aliases) for name, (_, aliases) in _settings().apps.items()),
        key=lambda item: item[0].lower(),
    )


def resolve_app(token: str) -> AppRef:
    """Resolve an acronym, full name, or app ID (case-insensitive) to an AppRef.

    Never guesses: an unknown token raises UnknownAppError listing valid choices.
    """
    key = token.strip().lower()
    apps = _settings().apps
    for name, (app_id, aliases) in apps.items():
        candidates = {name.lower(), app_id.lower(), *(a.lower() for a in aliases)}
        if key in candidates:
            return AppRef(name, app_id)
    choices = ", ".join(
        f"{name} ({aliases[0] if aliases else app_id})"
        for name, (app_id, aliases) in sorted(apps.items(), key=lambda kv: kv[0].lower())
    )
    raise UnknownAppError(f"Unknown app '{token}'. Valid apps: {choices}")


def resolve_apps(tokens: list[str]) -> list[AppRef]:
    """Resolve a list of app tokens to AppRefs.

    If any token is 'all' (case-insensitive), returns every known app (sorted).
    Otherwise resolves each token via resolve_app() and dedupes by canonical
    name, preserving first-seen order. Unknown tokens raise UnknownAppError.
    """
    if any(t.strip().lower() == "all" for t in tokens):
        return known_apps()
    seen: dict[str, AppRef] = {}
    for token in tokens:
        ref = resolve_app(token)
        seen.setdefault(ref.name, ref)
    return list(seen.values())
