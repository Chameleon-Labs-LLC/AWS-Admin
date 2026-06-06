# Open-Source Readiness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the live AWS infrastructure inventory (account ID, RDS host, DB name, Amplify app IDs) from all tracked code, tests, and docs — and from git history — without changing tool behavior, so the repo can be open-sourced.

**Architecture:** Sensitive values move from hardcoded `config.py` constants into a gitignored `~/.config/aws-admin/config.toml`, loaded lazily (`tomllib`) with env-var overrides and PEP 562 `__getattr__` so every existing call site is unchanged. A shipped `config.example.toml` documents the schema. Tests get a synthetic config from an autouse fixture. History is rewritten with `git filter-repo` using a replacements file generated **outside** the repo at scrub time.

**Tech Stack:** Python 3.12 (`tomllib` stdlib — no new dependency), pytest, `git filter-repo`.

**Sensitivity decision:** Product names (ExampleOrg, AppBeta, AppAlpha, AppGamma, MyApp2) are **public** — NOT redacted. Only the four classes of identifier above are sensitive.

**Plan hygiene:** This plan contains **no real identifier literals**. Real values are read at runtime from the existing `config.py` / local `config.toml`. The only file that ever holds the real literals is `replacements.txt`, which is created outside the repo and deleted after the scrub.

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `~/.config/aws-admin/config.toml` | Maintainer's real values (gitignored, outside repo) | Create (Task 1) |
| `tests/conftest.py` | Autouse synthetic `config.toml` per test | Modify (Task 2) |
| `src/aws_admin/config.py` | Lazy settings loader + PEP 562 access | Modify (Task 3) |
| `tests/test_config_paths.py`, `tests/test_config_db.py`, `tests/test_config_resolve.py` | Assert synthetic values + loader behavior | Modify (Task 3) |
| `config.example.toml` | Shipped schema with generic placeholders | Create (Task 4) |
| `.gitignore` | Ignore `config.toml`, `.scan-reports/` | Modify (Task 4) |
| `docs/usage.md`, `README.md` | "copy config.example.toml" step | Modify (Task 4) |
| `docs/implementation-summary.md`, `docs/superpowers/plans/*`, `docs/superpowers/specs/*` | Scrub real values → placeholders | Modify (Task 5) |

---

## Task 1: Preserve daily use — capture real values into local config.toml

This runs ONCE on the maintainer's machine. No commit (the file is outside the repo). It both keeps the slash commands working after the refactor and becomes the source for the history-scrub replacements file.

**Files:**
- Create: `~/.config/aws-admin/config.toml` (outside the repo)

- [ ] **Step 1: Generate the real local config from the current (pre-refactor) config.py**

This reads the live constants that still exist in `config.py` right now and serializes them to TOML. It accesses the private `_APPS` map intentionally.

```bash
cd /mnt/d/Documents/Code/GitHub/AWS-Admin
.venv_linux/bin/python - <<'PY'
from pathlib import Path
from aws_admin import config

dest = Path.home() / ".config" / "aws-admin" / "config.toml"
dest.parent.mkdir(parents=True, exist_ok=True)

def q(s): return '"' + str(s).replace('"', '\\"') + '"'

lines = [
    f"account_id = {q(config.ACCOUNT_ID)}",
    "",
    "[database]",
    f"host = {q(config.DB_HOST)}",
    f"name = {q(config.DB_NAME)}",
    "",
]
for name, (app_id, aliases) in config._APPS.items():
    lines.append(f"[apps.{name}]")
    lines.append(f"app_id = {q(app_id)}")
    alias_items = ", ".join(q(a) for a in aliases)
    lines.append(f"aliases = [{alias_items}]")
    lines.append("")

dest.write_text("\n".join(lines))
print(f"wrote {dest}")
PY
```

Expected: `wrote /home/leland/.config/aws-admin/config.toml`

- [ ] **Step 2: Verify the file is outside the repo and not tracked**

Run:
```bash
git -C /mnt/d/Documents/Code/GitHub/AWS-Admin status --porcelain ~/.config/aws-admin/config.toml; echo "exit=$?"
```
Expected: no output referencing the file (it lives outside the work tree). Confirm it exists:
```bash
test -f ~/.config/aws-admin/config.toml && echo OK
```
Expected: `OK`

(No commit — this file must never be committed.)

---

## Task 2: Test harness — autouse synthetic config.toml

The synthetic config keeps the **real public app names + aliases** (so command tests using `my`/`my`/`MyApp2` keep passing) but uses **fake app IDs**, a **fake account ID**, a **fake RDS host**, and a **fake DB name**. No real identifier appears in `tests/`.

**Files:**
- Modify: `tests/conftest.py` (the `isolated_home` fixture, lines 57-61)

- [ ] **Step 1: Add the synthetic-config writer to the autouse `isolated_home` fixture**

Replace the existing fixture:

```python
@pytest.fixture(autouse=True)
def isolated_home(monkeypatch, tmp_path):
    """Every test gets its own AWS_ADMIN_HOME so vault state never leaks across tests."""
    monkeypatch.setenv("AWS_ADMIN_HOME", str(tmp_path))
    return tmp_path
```

with:

```python
SYNTHETIC_CONFIG_TOML = """\
account_id = "000000000000"

[database]
host = "db.example.invalid"
name = "example_db"

[apps.ExampleOrg]
app_id = "d0000000000eo0"
aliases = ["my"]

[apps.AppBeta]
app_id = "d0000000000ab0"
aliases = ["ab"]

[apps.AppAlpha]
app_id = "d0000000000aa0"
aliases = ["aa"]

[apps.AppGamma]
app_id = "d0000000000ag0"
aliases = ["ag"]

[apps.MyApp2]
app_id = "d0000000000my0"
aliases = ["my"]
"""


@pytest.fixture(autouse=True)
def isolated_home(monkeypatch, tmp_path):
    """Every test gets its own AWS_ADMIN_HOME (vault isolation) seeded with a
    synthetic config.toml so config-dependent code has known, non-sensitive values."""
    monkeypatch.setenv("AWS_ADMIN_HOME", str(tmp_path))
    (tmp_path / "config.toml").write_text(SYNTHETIC_CONFIG_TOML)
    return tmp_path
```

- [ ] **Step 2: Run the full suite to confirm nothing broke yet**

The old `config.py` still uses hardcoded constants and ignores `config.toml`, so writing the file is inert. Tests should still pass.

Run: `.venv_linux/bin/python -m pytest -q`
Expected: PASS (same as before the change).

- [ ] **Step 3: Commit**

```bash
git add tests/conftest.py
git commit -m "test: seed each test's AWS_ADMIN_HOME with a synthetic config.toml

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Refactor config.py to load settings from config.toml (TDD)

**Files:**
- Modify: `src/aws_admin/config.py`
- Modify (tests first): `tests/test_config_paths.py`, `tests/test_config_db.py`, `tests/test_config_resolve.py`
- Modify (also embed the real MyApp2 app ID — must be scrubbed AND updated to the synthetic ID, since `resolve_app("my")` now returns the synthetic value): `tests/test_env_pull_diff.py`, `tests/test_vault_snapshot.py`

- [ ] **Step 1: Rewrite the config tests to expect synthetic values + loader behavior**

Replace `tests/test_config_paths.py` entirely:

```python
from pathlib import Path
from aws_admin import config


def test_state_dir_uses_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("AWS_ADMIN_HOME", str(tmp_path))
    assert config.state_dir() == tmp_path


def test_state_dir_default(monkeypatch):
    monkeypatch.delenv("AWS_ADMIN_HOME", raising=False)
    assert config.state_dir() == Path.home() / ".config" / "aws-admin"


def test_path_helpers(monkeypatch, tmp_path):
    monkeypatch.setenv("AWS_ADMIN_HOME", str(tmp_path))
    assert config.key_path() == tmp_path / "vault.key"
    assert config.vault_path("MyApp2") == tmp_path / "vaults" / "MyApp2.enc"
    assert config.backup_path("MyApp2", "20260530-084800") == (
        tmp_path / "backups" / "MyApp2-20260530-084800.enc"
    )


def test_plain_constants():
    assert config.REGION == "us-east-1"
    assert config.DEFAULT_BRANCH == "main"


def test_account_id_comes_from_config():
    assert config.ACCOUNT_ID == "000000000000"
```

Replace `tests/test_config_db.py` entirely:

```python
from aws_admin import config


def test_db_values():
    assert config.DB_HOST == "db.example.invalid"
    assert config.DB_PORT == 5432
    assert config.DB_NAME == "example_db"
    assert config.DB_USER == "postgres"
    assert config.DB_SSLMODE == "require"


def test_db_paths(monkeypatch, tmp_path):
    monkeypatch.setenv("AWS_ADMIN_HOME", str(tmp_path))
    assert config.db_password_path() == tmp_path / "db-password.enc"
    assert config.results_dir() == tmp_path / "results"


def test_env_override_wins(monkeypatch, tmp_path):
    # A fresh home (so the loader cache key differs) with its own config.
    home = tmp_path / "ovr"
    home.mkdir()
    (home / "config.toml").write_text(
        'account_id = "111111111111"\n[database]\nhost = "h"\nname = "n"\n'
    )
    monkeypatch.setenv("AWS_ADMIN_HOME", str(home))
    monkeypatch.setenv("AWS_ADMIN_DB_HOST", "override.example.invalid")
    assert config.DB_HOST == "override.example.invalid"


def test_missing_config_raises(monkeypatch, tmp_path):
    home = tmp_path / "empty"
    home.mkdir()
    monkeypatch.setenv("AWS_ADMIN_HOME", str(home))
    import pytest
    with pytest.raises(config.ConfigError):
        _ = config.ACCOUNT_ID
```

Replace the parametrized cases in `tests/test_config_resolve.py` so the app-ID rows use the synthetic fake IDs:

```python
import pytest
from aws_admin import config


@pytest.mark.parametrize("token,expected", [
    ("eo", "ExampleOrg"),
    ("EO", "ExampleOrg"),
    ("exampleorg", "ExampleOrg"),
    ("d0000000000eo0", "ExampleOrg"),
    ("ab", "AppBeta"),
    ("aa", "AppAlpha"),
    ("ag", "AppGamma"),
    ("my", "MyApp2"),
    ("MyApp2", "MyApp2"),
    ("d0000000000my0", "MyApp2"),
])
def test_resolve_app_known(token, expected):
    ref = config.resolve_app(token)
    assert ref.name == expected


def test_resolve_app_ids_correct():
    assert config.resolve_app("my").app_id == "d0000000000my0"
    assert config.resolve_app("my").app_id == "d0000000000eo0"


def test_resolve_unknown_raises_with_choices():
    with pytest.raises(config.UnknownAppError) as exc:
        config.resolve_app("nope")
    msg = str(exc.value)
    assert "nope" in msg
    assert "MyApp2" in msg and "ExampleOrg" in msg
```

Also scrub the real MyApp2 app ID out of two more test files (they will otherwise break once `resolve_app` returns the synthetic ID, and they hold a real value). In each, the real MyApp2 app-ID string literal (a `d`-prefixed Amplify app ID — find it; do NOT type it from memory) must be replaced with the synthetic `"d0000000000my0"`:

- `tests/test_env_pull_diff.py` — the `assert snap["app_id"] == <real MyApp2 app id>` line becomes `assert snap["app_id"] == "d0000000000my0"`.
- `tests/test_vault_snapshot.py` — the snapshot dict literal `{"app_id": <real MyApp2 app id>, "branch": "main", ...}` becomes `{"app_id": "d0000000000my0", "branch": "main", ...}`.

To locate the exact literal without hardcoding it, read the value from the maintainer's local config and grep for it:
```bash
MY=$(.venv_linux/bin/python -c "import tomllib,pathlib;print(tomllib.loads((pathlib.Path.home()/'.config'/'aws-admin'/'config.toml').read_text())['apps']['MyApp2']['app_id'])")
grep -rn "$MY" tests/test_env_pull_diff.py tests/test_vault_snapshot.py
```

- [ ] **Step 2: Run the config tests to verify they FAIL against the old config.py**

Run: `.venv_linux/bin/python -m pytest tests/test_config_paths.py tests/test_config_db.py tests/test_config_resolve.py -q`
Expected: FAIL — old `config.py` returns the hardcoded real values (e.g. `ACCOUNT_ID` is the real account ID, not `"000000000000"`) and has no `ConfigError`.

- [ ] **Step 3: Rewrite `src/aws_admin/config.py`**

Full new content:

```python
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
    return [AppRef(name, app_id) for name, (app_id, _) in _settings().apps.items()]


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
        for name, (app_id, aliases) in apps.items()
    )
    raise UnknownAppError(f"Unknown app '{token}'. Valid apps: {choices}")
```

- [ ] **Step 4: Run the config tests to verify they PASS**

Run: `.venv_linux/bin/python -m pytest tests/test_config_paths.py tests/test_config_db.py tests/test_config_resolve.py -q`
Expected: PASS.

- [ ] **Step 5: Run the FULL suite (catch call-site regressions)**

Run: `.venv_linux/bin/python -m pytest -q`
Expected: PASS — `aws_client.py`, `db/connection.py`, `commands/*` consume `config.ACCOUNT_ID`/`DB_HOST`/`DB_NAME`/`resolve_app` unchanged via `__getattr__`.

- [ ] **Step 6: Confirm the maintainer's tool still works end-to-end**

Run: `.venv_linux/bin/aws-admin db check 2>&1 | head -5` (reads the real `~/.config/aws-admin/config.toml` from Task 1)
Expected: a `connected as postgres@.../...` line OR a clean connection error — NOT a `ConfigError`. (A DB connection failure is fine; a ConfigError means Task 1 was skipped.)

- [ ] **Step 7: Commit**

```bash
git add src/aws_admin/config.py tests/test_config_paths.py tests/test_config_db.py tests/test_config_resolve.py tests/test_env_pull_diff.py tests/test_vault_snapshot.py
git commit -m "feat(config): load deployment values from config.toml instead of hardcoding

Account ID, RDS host, DB name, and the Amplify app map now come from
\$AWS_ADMIN_HOME/config.toml with env-var overrides; PEP 562 __getattr__
keeps every call site unchanged. Generic constants stay in-module.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Ship config.example.toml, update .gitignore and docs

**Files:**
- Create: `config.example.toml`
- Modify: `.gitignore`
- Modify: `docs/usage.md`, `README.md`

- [ ] **Step 1: Create `config.example.toml`**

```toml
# Copy this file to ~/.config/aws-admin/config.toml (or $AWS_ADMIN_HOME/config.toml)
# and fill in your own values. This file ships with placeholders only.

# Your 12-digit AWS account ID.
account_id = "123456789012"

[database]
# RDS PostgreSQL endpoint and database name.
host = "your-db.xxxxxxxxxxxx.us-east-1.rds.amazonaws.com"
name = "your_db"

# One [apps.<Name>] table per Amplify app. <Name> is the canonical app name;
# `aliases` are extra short tokens you can pass on the command line.
[apps.MyApp]
app_id = "d0000000000000"
aliases = ["my"]

[apps.AnotherApp]
app_id = "d0000000000001"
aliases = ["other"]
```

- [ ] **Step 2: Add ignore entries**

Append to `.gitignore` under the secrets section (after the `config.json` line):

```
config.toml
.scan-reports/
```

- [ ] **Step 3: Verify `config.toml` would be ignored if present in-repo**

Run: `git check-ignore -v config.toml || echo "NOT IGNORED"`
Expected: a line showing `.gitignore:<n>:config.toml` (ignored). If `NOT IGNORED`, fix the entry.

- [ ] **Step 4: Add a setup step to the docs**

In `docs/usage.md`, add near the top (after any install section) — and mirror a one-liner in `README.md`:

```markdown
## First-time setup

Copy the example config and fill in your AWS values (the file is gitignored and
never leaves your machine):

    cp config.example.toml ~/.config/aws-admin/config.toml
    $EDITOR ~/.config/aws-admin/config.toml

`aws-admin` reads `account_id`, the `[database]` host/name, and one
`[apps.<Name>]` table per Amplify app from this file. Generic settings (region,
default branch, DB port/user/sslmode) have built-in defaults.
```

- [ ] **Step 5: Run the suite (docs/config-example are inert to tests)**

Run: `.venv_linux/bin/python -m pytest -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add config.example.toml .gitignore docs/usage.md README.md
git commit -m "docs(config): ship config.example.toml; ignore config.toml and scan reports

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Scrub real identifiers from tracked docs

The docs still embed the real account ID, RDS host, DB name, and app IDs in prose and example code blocks. Replace them with the SAME placeholders used in `config.example.toml` / the spec, so the working tree is clean before history rewrite.

**Files:**
- Modify: `docs/implementation-summary.md`
- Modify: `docs/superpowers/plans/2026-05-30-db-subcommand.md`
- Modify: `docs/superpowers/plans/2026-05-30-secure-amplify-secrets-cli.md`
- Modify: `docs/superpowers/specs/2026-05-30-db-subcommand-design.md`
- Modify: `docs/superpowers/specs/2026-05-30-secure-amplify-secrets-cli-design.md`

- [ ] **Step 1: List every tracked file that still contains a real identifier**

Run:
```bash
cd /mnt/d/Documents/Code/GitHub/AWS-Admin
git grep -lE "$(.venv_linux/bin/python - <<'PY'
from pathlib import Path
import tomllib
data = tomllib.loads((Path.home()/'.config'/'aws-admin'/'config.toml').read_text())
vals = [data['account_id'], data['database']['host'], data['database']['name']]
vals += [a['app_id'] for a in data.get('apps', {}).values()]
import re
print('|'.join(re.escape(v) for v in vals))
PY
)" -- $(git ls-files)
```
Expected: the five doc files listed above (NOT `src/` or `tests/`, which Tasks 2-3 already cleaned). If `src/` or `tests/` appear, fix those first.

- [ ] **Step 2: Replace real values with placeholders in each listed doc**

For each file from Step 1, replace:
- the real account ID → `123456789012`
- the real RDS host → `your-db.xxxxxxxxxxxx.us-east-1.rds.amazonaws.com`
- the real DB name → `your_db`
- each real app ID → `d0000000000000` (any consistent placeholder; exactness in docs does not matter)

Use the same generated regex to find exact line locations, then edit each occurrence. Product NAMES stay as-is (public).

- [ ] **Step 3: Verify the working tree is clean of real identifiers**

Run (reuses the generated alternation):
```bash
git grep -nE "$(.venv_linux/bin/python - <<'PY'
from pathlib import Path
import tomllib, re
data = tomllib.loads((Path.home()/'.config'/'aws-admin'/'config.toml').read_text())
vals = [data['account_id'], data['database']['host'], data['database']['name']]
vals += [a['app_id'] for a in data.get('apps', {}).values()]
print('|'.join(re.escape(v) for v in vals))
PY
)" -- $(git ls-files) && echo "!!! STILL PRESENT" || echo "WORKING TREE CLEAN"
```
Expected: `WORKING TREE CLEAN`.

- [ ] **Step 4: Run the full suite**

Run: `.venv_linux/bin/python -m pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add docs/
git commit -m "docs: replace real AWS identifiers with placeholders

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Final working-tree verification

- [ ] **Step 1: Full suite green**

Run: `.venv_linux/bin/python -m pytest -q`
Expected: PASS (all tests).

- [ ] **Step 2: No real identifier anywhere in the tracked tree**

Run the Step-3 grep from Task 5 again over `git ls-files`.
Expected: `WORKING TREE CLEAN`.

- [ ] **Step 3: Sanity — secrets/state still ignored**

Run:
```bash
git check-ignore -v config.toml .scan-reports/ 2>/dev/null; git status --porcelain
```
Expected: `config.toml` and `.scan-reports/` shown as ignored; `git status` clean (all work committed).

---

## Task 7: Rewrite git history (maintainer runs the force-push)

`git filter-repo` rewrites every commit. The replacements file is the ONLY artifact holding real literals; it is created **outside** the repo and deleted immediately after.

**Files:**
- Create (outside repo, temporary): `/tmp/aws-admin-replacements.txt`

- [ ] **Step 1: Confirm `git filter-repo` is installed**

Run: `git filter-repo --version || echo "MISSING"`
Expected: a version string. If `MISSING`, stop and tell the maintainer to install it (`pipx install git-filter-repo` or distro package) — do not attempt a system install.

- [ ] **Step 2: Back up the repo before rewriting history**

Run:
```bash
cd /mnt/d/Documents/Code/GitHub
cp -a AWS-Admin AWS-Admin.prebackup-$(date +%Y%m%d-%H%M%S)
echo "backup made"
```
Expected: `backup made`.

- [ ] **Step 3: Generate the replacements file OUTSIDE the repo**

```bash
.venv_linux/bin/python - <<'PY'
from pathlib import Path
import tomllib
data = tomllib.loads((Path.home()/'.config'/'aws-admin'/'config.toml').read_text())
vals = [data['account_id'], data['database']['host'], data['database']['name']]
vals += [a['app_id'] for a in data.get('apps', {}).values()]
tokens = {
    data['account_id']: 'REDACTED_ACCOUNT_ID',
    data['database']['host']: 'REDACTED_DB_HOST',
    data['database']['name']: 'REDACTED_DB_NAME',
}
for i, a in enumerate(data.get('apps', {}).values()):
    tokens[a['app_id']] = f'REDACTED_APP_ID_{i+1}'
out = Path('/tmp/aws-admin-replacements.txt')
out.write_text('\n'.join(f'{v}==>{t}' for v, t in tokens.items()) + '\n')
print(f'wrote {out} ({len(tokens)} entries)')
PY
```
Expected: `wrote /tmp/aws-admin-replacements.txt (8 entries)` (account + host + db name + 5 app IDs). Product names are intentionally absent.

- [ ] **Step 4: Rewrite history**

Run:
```bash
cd /mnt/d/Documents/Code/GitHub/AWS-Admin
git filter-repo --replace-text /tmp/aws-admin-replacements.txt --force
echo "exit=$?"
```
Expected: filter-repo runs to completion; `exit=0`. (filter-repo removes the `origin` remote by design.)

- [ ] **Step 5: Verify NOTHING survives in history**

Run:
```bash
git log --all -p | grep -nFf <(cut -d= -f1 /tmp/aws-admin-replacements.txt) && echo "!!! VALUES STILL IN HISTORY" || echo "HISTORY CLEAN"
```
Expected: `HISTORY CLEAN`.

- [ ] **Step 6: Delete the replacements file (it holds the only literal copy)**

Run: `shred -u /tmp/aws-admin-replacements.txt 2>/dev/null || rm -f /tmp/aws-admin-replacements.txt; echo done`
Expected: `done`.

- [ ] **Step 7: Hand off the force-push to the maintainer**

Do NOT push automatically. Print these instructions for the maintainer to run when ready to publish:

```bash
# Re-add the remote (filter-repo dropped it), then force-push the rewritten history:
git remote add origin <git-remote-url>
git push --force-with-lease origin master
# If a public mirror is a NEW repo, instead:  git push -u origin master
```
Also remind them: anyone who already cloned the old history still has the old values; force-push only fixes the canonical repo. If the repo was ever pushed publicly before this scrub, treat the identifiers as exposed.

---

## Self-Review

**Spec coverage:**
- Externalize config (account/host/db/apps) → Task 3. ✓
- Generic constants stay → Task 3 (`REGION`/`DEFAULT_BRANCH`/`DB_PORT`/`DB_USER`/`DB_SSLMODE`). ✓
- Lazy load + env overrides + PEP 562 → Task 3. ✓
- `config.example.toml` + `.gitignore` (`config.toml`, `.scan-reports/`) → Task 4. ✓
- Maintainer's real local config → Task 1. ✓
- Synthetic test config + rewrite 3 config tests → Tasks 2, 3. ✓
- Scrub docs → Task 5. ✓
- History rewrite with out-of-repo replacements + maintainer force-push → Task 7. ✓
- Verification gates (suite green; working tree + history grep clean) → Tasks 3, 5, 6, 7. ✓

**Placeholder scan:** No "TBD"/"handle edge cases"/"similar to" — every code/test step shows full content; doc-scrub step (Task 5 Step 2) is mechanical replace with exact mappings.

**Type consistency:** `Settings`, `AppRef`, `ConfigError`, `UnknownAppError`, `_load_settings_cached`/`_settings`/`config_path`/`__getattr__` are defined in Task 3 and referenced consistently in tests (Task 3 Step 1) and later tasks. Synthetic fake IDs (`d0000000000eo0` … `d0000000000my0`) match between conftest (Task 2) and `test_config_resolve` (Task 3).
