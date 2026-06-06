# Secure Amplify Secrets CLI — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Python CLI (`aws-admin`) that manages Amplify environment variables by app name + action, so secret *values* never appear in any prompt, transcript, or inline command.

**Architecture:** Single installable package in `src/aws_admin/`. Amplify is the source of truth; an encrypted local snapshot (`~/.config/aws-admin/`) is an edit/backup buffer. All output flows through one redaction chokepoint. Command functions take an injected boto3 client so tests can pass a fake. Thin global slash commands wrap the CLI.

**Tech Stack:** Python 3.12, `boto3` (Amplify), `cryptography` (Fernet), `pytest`. No `moto`/Stubber — tests use a hand-written fake client.

---

## Conventions used throughout

- **State directory** is resolved from env var `AWS_ADMIN_HOME` if set, else `~/.config/aws-admin`. Tests set `AWS_ADMIN_HOME` to a temp dir.
- **Snapshot schema** (the JSON inside each `<app>.enc`):
  ```json
  {"app_id": "d0000000000000", "branch": "main",
   "app_level": {"KEY": "value"}, "branch_level": {},
   "pulled_at": "2026-05-30T08:48:00Z"}
  ```
- **Region** `us-east-1`, **default branch** `main`, **account** `123456789012`.
- Run all Python via the project venv: `.venv_linux/bin/python` and `.venv_linux/bin/pytest`.

---

## File Structure

- `pyproject.toml` — package metadata, deps, `aws-admin` entry point.
- `src/aws_admin/__init__.py` — version.
- `src/aws_admin/config.py` — paths, region/account constants, app-alias resolution.
- `src/aws_admin/redact.py` — value rendering, key-only diffs, summaries (the only place values are touched for output).
- `src/aws_admin/vault.py` — Fernet key-file mgmt, encrypt/decrypt, snapshot load/save, backups, interactive edit buffer.
- `src/aws_admin/aws_client.py` — boto3 Amplify client factory.
- `src/aws_admin/commands/env.py` — `pull`/`diff`/`push`/`redeploy`/`edit` logic.
- `src/aws_admin/cli.py` — argparse dispatch + `main()`.
- `tests/conftest.py` — fixtures: temp `AWS_ADMIN_HOME`, fake Amplify client.
- `tests/test_*.py` — one file per module.
- `~/.claude/commands/aws-env-*.md` — slash-command wrappers (Task 13).

---

## Task 1: Project scaffolding & venv

**Files:**
- Create: `pyproject.toml`
- Create: `src/aws_admin/__init__.py`
- Create: `src/aws_admin/commands/__init__.py`
- Create: `tests/__init__.py`

- [ ] **Step 1: Create the package version file**

`src/aws_admin/__init__.py`:
```python
__version__ = "0.1.0"
```

- [ ] **Step 2: Create empty package/test init files**

`src/aws_admin/commands/__init__.py`: (empty file)
`tests/__init__.py`: (empty file)

- [ ] **Step 3: Create `pyproject.toml`**

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "aws-admin"
version = "0.1.0"
description = "Secure AWS admin CLI — manage Amplify env vars without leaking secret values into prompts"
requires-python = ">=3.12"
dependencies = [
    "boto3>=1.34",
    "cryptography>=42.0",
]

[project.optional-dependencies]
dev = ["pytest>=8.0"]

[project.scripts]
aws-admin = "aws_admin.cli:main"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]
```

- [ ] **Step 4: Create venv and install**

Run:
```bash
cd /mnt/d/Documents/Code/GitHub/AWS-Admin
python3 -m venv .venv_linux
.venv_linux/bin/pip install -q --upgrade pip
.venv_linux/bin/pip install -q -e ".[dev]"
```
Expected: installs boto3, cryptography, pytest with no errors. (Note: these libraries are well-established and far older than 7 days; no supply-chain age concern.)

- [ ] **Step 5: Verify import and entry point**

Run:
```bash
.venv_linux/bin/python -c "import aws_admin; print(aws_admin.__version__)"
.venv_linux/bin/aws-admin --help 2>&1 | head -1 || echo "cli not wired yet (expected)"
```
Expected: prints `0.1.0`. The `aws-admin --help` will error until Task 10 — that's fine.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml src/ tests/
git commit -m "feat: scaffold aws-admin package and venv"
```

---

## Task 2: Config — paths & constants

**Files:**
- Create: `src/aws_admin/config.py`
- Test: `tests/test_config_paths.py`

- [ ] **Step 1: Write the failing test**

`tests/test_config_paths.py`:
```python
import os
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


def test_constants():
    assert config.REGION == "us-east-1"
    assert config.ACCOUNT_ID == "123456789012"
    assert config.DEFAULT_BRANCH == "main"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv_linux/bin/pytest tests/test_config_paths.py -v`
Expected: FAIL — `ModuleNotFoundError` / `AttributeError` (config has no `state_dir`).

- [ ] **Step 3: Write minimal implementation**

`src/aws_admin/config.py`:
```python
"""Paths, constants, and Amplify app-alias resolution."""
from __future__ import annotations

import os
from pathlib import Path

REGION = "us-east-1"
ACCOUNT_ID = "123456789012"
DEFAULT_BRANCH = "main"


def state_dir() -> Path:
    """Directory holding the vault key, snapshots, and backups.

    Honors AWS_ADMIN_HOME (used by tests); defaults to ~/.config/aws-admin.
    Lives outside the repo and outside Dropbox on purpose.
    """
    override = os.environ.get("AWS_ADMIN_HOME")
    if override:
        return Path(override)
    return Path.home() / ".config" / "aws-admin"


def key_path() -> Path:
    return state_dir() / "vault.key"


def vault_path(app_name: str) -> Path:
    return state_dir() / "vaults" / f"{app_name}.enc"


def backup_path(app_name: str, timestamp: str) -> Path:
    return state_dir() / "backups" / f"{app_name}-{timestamp}.enc"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv_linux/bin/pytest tests/test_config_paths.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/aws_admin/config.py tests/test_config_paths.py
git commit -m "feat: config paths and constants"
```

---

## Task 3: Config — app-alias resolution

**Files:**
- Modify: `src/aws_admin/config.py`
- Test: `tests/test_config_resolve.py`

- [ ] **Step 1: Write the failing test**

`tests/test_config_resolve.py`:
```python
import pytest
from aws_admin import config


@pytest.mark.parametrize("token,expected", [
    ("eo", "ExampleOrg"),
    ("EO", "ExampleOrg"),
    ("exampleorg", "ExampleOrg"),
    ("d0000000000000", "ExampleOrg"),
    ("ab", "AppBeta"),
    ("aa", "AppAlpha"),
    ("ag", "AppGamma"),
    ("my", "MyApp2"),
    ("MyApp2", "MyApp2"),
    ("d0000000000000", "MyApp2"),
])
def test_resolve_app_known(token, expected):
    ref = config.resolve_app(token)
    assert ref.name == expected


def test_resolve_app_ids_correct():
    assert config.resolve_app("my").app_id == "d0000000000000"
    assert config.resolve_app("my").app_id == "d0000000000000"


def test_resolve_unknown_raises_with_choices():
    with pytest.raises(config.UnknownAppError) as exc:
        config.resolve_app("nope")
    msg = str(exc.value)
    assert "nope" in msg
    # Lists valid choices so the user can self-correct.
    assert "MyApp2" in msg and "ExampleOrg" in msg
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv_linux/bin/pytest tests/test_config_resolve.py -v`
Expected: FAIL — `AttributeError: module 'aws_admin.config' has no attribute 'resolve_app'`.

- [ ] **Step 3: Write minimal implementation**

Append to `src/aws_admin/config.py`:
```python
from dataclasses import dataclass


@dataclass(frozen=True)
class AppRef:
    name: str
    app_id: str


# Canonical name -> (app_id, extra alias tokens). Name itself is always an alias.
_APPS: dict[str, tuple[str, tuple[str, ...]]] = {
    "ExampleOrg": ("d0000000000000", ("eo",)),
    "AppBeta": ("d0000000000000", ("ab",)),
    "AppAlpha": ("d0000000000000", ("aa",)),
    "AppGamma": ("d0000000000000", ("ag",)),
    "MyApp2": ("d0000000000000", ("my",)),
}


class UnknownAppError(ValueError):
    """Raised when an app token matches no known app."""


def known_apps() -> list[AppRef]:
    return [AppRef(name, app_id) for name, (app_id, _) in _APPS.items()]


def resolve_app(token: str) -> AppRef:
    """Resolve an acronym, full name, or app ID (case-insensitive) to an AppRef.

    Never guesses: an unknown token raises UnknownAppError listing valid choices.
    """
    key = token.strip().lower()
    for name, (app_id, aliases) in _APPS.items():
        candidates = {name.lower(), app_id.lower(), *(a.lower() for a in aliases)}
        if key in candidates:
            return AppRef(name, app_id)
    choices = ", ".join(
        f"{name} ({aliases[0] if aliases else app_id})"
        for name, (app_id, aliases) in _APPS.items()
    )
    raise UnknownAppError(f"Unknown app '{token}'. Valid apps: {choices}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv_linux/bin/pytest tests/test_config_resolve.py -v`
Expected: PASS (13 tests).

- [ ] **Step 5: Commit**

```bash
git add src/aws_admin/config.py tests/test_config_resolve.py
git commit -m "feat: app-alias resolution with acronyms"
```

---

## Task 4: Redaction — value rendering & summaries

**Files:**
- Create: `src/aws_admin/redact.py`
- Test: `tests/test_redact.py`

- [ ] **Step 1: Write the failing test**

`tests/test_redact.py`:
```python
from aws_admin import redact


def test_render_value_hides_raw_value():
    secret = "sk_live_ABC123_super_secret"
    out = redact.render_value(secret)
    assert secret not in out
    assert "27 chars" in out
    assert out.startswith("<set,")
    assert "sha256:" in out


def test_render_empty():
    assert redact.render_value("") == "<empty>"


def test_summarize_counts_only():
    env = {"A": "1", "B": "2", "C": "3"}
    out = redact.summarize(env)
    assert out == "3 keys"
    assert "1" not in out.replace("3 keys", "")  # no values leak


def test_summarize_singular():
    assert redact.summarize({"A": "1"}) == "1 key"
    assert redact.summarize({}) == "0 keys"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv_linux/bin/pytest tests/test_redact.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'aws_admin.redact'`.

- [ ] **Step 3: Write minimal implementation**

`src/aws_admin/redact.py`:
```python
"""The single chokepoint for rendering secret-bearing data as output.

No function here returns a raw secret value. Display hashes use a per-process
random salt so the rendered digest is not a stable fingerprint across transcripts.
"""
from __future__ import annotations

import hashlib
import os

_SALT = os.urandom(16)


def _digest(value: str) -> str:
    h = hashlib.sha256(_SALT + value.encode("utf-8")).hexdigest()
    return h[:4]


def render_value(value: str) -> str:
    """Render a single secret value without revealing it."""
    if value == "":
        return "<empty>"
    return f"<set, {len(value)} chars, sha256:{_digest(value)}…>"


def summarize(env: dict[str, str]) -> str:
    """Count keys only — never values."""
    n = len(env)
    return f"{n} key" if n == 1 else f"{n} keys"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv_linux/bin/pytest tests/test_redact.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/aws_admin/redact.py tests/test_redact.py
git commit -m "feat: redaction primitives for value rendering and summaries"
```

---

## Task 5: Redaction — key-only diffs

**Files:**
- Modify: `src/aws_admin/redact.py`
- Test: `tests/test_redact_diff.py`

- [ ] **Step 1: Write the failing test**

`tests/test_redact_diff.py`:
```python
from aws_admin import redact


def test_key_diff_detects_changes_by_value_not_hash():
    old = {"A": "1", "B": "2", "STRIPE": "old"}
    new = {"A": "1", "C": "3", "STRIPE": "new"}
    d = redact.key_diff(old, new)
    assert d == {"added": ["C"], "removed": ["B"], "changed": ["STRIPE"]}


def test_key_diff_no_values_in_output():
    old = {"SECRET": "top-secret-value"}
    new = {"SECRET": "different-secret"}
    d = redact.key_diff(old, new)
    flat = str(d)
    assert "top-secret-value" not in flat
    assert "different-secret" not in flat


def test_format_diff_key_only():
    d = {"added": ["C"], "removed": ["B"], "changed": ["STRIPE"]}
    text = redact.format_diff(d)
    assert "added: C" in text
    assert "removed: B" in text
    assert "changed: STRIPE" in text


def test_format_diff_empty():
    d = {"added": [], "removed": [], "changed": []}
    assert redact.format_diff(d) == "no changes"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv_linux/bin/pytest tests/test_redact_diff.py -v`
Expected: FAIL — `AttributeError: module 'aws_admin.redact' has no attribute 'key_diff'`.

- [ ] **Step 3: Write minimal implementation**

Append to `src/aws_admin/redact.py`:
```python
def key_diff(old: dict[str, str], new: dict[str, str]) -> dict[str, list[str]]:
    """Compare two env maps and return key-only changes.

    'changed' is determined by direct value comparison (both values are already
    in memory); values themselves are never returned.
    """
    old_keys, new_keys = set(old), set(new)
    added = sorted(new_keys - old_keys)
    removed = sorted(old_keys - new_keys)
    changed = sorted(k for k in old_keys & new_keys if old[k] != new[k])
    return {"added": added, "removed": removed, "changed": changed}


def format_diff(diff: dict[str, list[str]]) -> str:
    parts = []
    for label in ("added", "removed", "changed"):
        for key in diff[label]:
            parts.append(f"{label}: {key}")
    return "\n".join(parts) if parts else "no changes"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv_linux/bin/pytest tests/test_redact_diff.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/aws_admin/redact.py tests/test_redact_diff.py
git commit -m "feat: key-only env diffs"
```

---

## Task 6: Vault — key file & encrypt/decrypt

**Files:**
- Create: `src/aws_admin/vault.py`
- Test: `tests/test_vault_crypto.py`

- [ ] **Step 1: Write the failing test**

`tests/test_vault_crypto.py`:
```python
import os
import stat
from aws_admin import vault, config


def test_ensure_key_creates_0600_file(monkeypatch, tmp_path):
    monkeypatch.setenv("AWS_ADMIN_HOME", str(tmp_path))
    key = vault.ensure_key()
    assert isinstance(key, bytes) and len(key) > 0
    mode = stat.S_IMODE(os.stat(config.key_path()).st_mode)
    assert mode == 0o600


def test_ensure_key_is_stable(monkeypatch, tmp_path):
    monkeypatch.setenv("AWS_ADMIN_HOME", str(tmp_path))
    assert vault.ensure_key() == vault.ensure_key()


def test_encrypt_decrypt_round_trip(monkeypatch, tmp_path):
    monkeypatch.setenv("AWS_ADMIN_HOME", str(tmp_path))
    data = {"app_id": "x", "app_level": {"K": "v"}}
    token = vault.encrypt(data)
    assert b"app_id" not in token  # ciphertext, not plaintext
    assert vault.decrypt(token) == data
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv_linux/bin/pytest tests/test_vault_crypto.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'aws_admin.vault'`.

- [ ] **Step 3: Write minimal implementation**

`src/aws_admin/vault.py`:
```python
"""Encrypted local store for Amplify env-var snapshots.

Fernet (AES-128-CBC + HMAC). The key lives at config.key_path() with mode 0600.
Decryption is in-memory only; the sole plaintext that touches disk is the
transient edit buffer (see edit_app_buffer), which is shredded.
"""
from __future__ import annotations

import json
from cryptography.fernet import Fernet

from . import config


def ensure_key() -> bytes:
    """Return the Fernet key, generating a 0600 key file on first use."""
    path = config.key_path()
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(Fernet.generate_key())
        path.chmod(0o600)
    return path.read_bytes()


def _fernet() -> Fernet:
    return Fernet(ensure_key())


def encrypt(data: dict) -> bytes:
    return _fernet().encrypt(json.dumps(data).encode("utf-8"))


def decrypt(token: bytes) -> dict:
    return json.loads(_fernet().decrypt(token).decode("utf-8"))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv_linux/bin/pytest tests/test_vault_crypto.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/aws_admin/vault.py tests/test_vault_crypto.py
git commit -m "feat: vault Fernet key management and encryption"
```

---

## Task 7: Vault — snapshot load/save & backups

**Files:**
- Modify: `src/aws_admin/vault.py`
- Test: `tests/test_vault_snapshot.py`

- [ ] **Step 1: Write the failing test**

`tests/test_vault_snapshot.py`:
```python
from aws_admin import vault, config


def test_load_missing_returns_none(monkeypatch, tmp_path):
    monkeypatch.setenv("AWS_ADMIN_HOME", str(tmp_path))
    assert vault.load_snapshot("MyApp2") is None


def test_save_then_load(monkeypatch, tmp_path):
    monkeypatch.setenv("AWS_ADMIN_HOME", str(tmp_path))
    snap = {"app_id": "d0000000000000", "branch": "main",
            "app_level": {"K": "v"}, "branch_level": {}, "pulled_at": "t"}
    vault.save_snapshot("MyApp2", snap)
    assert vault.load_snapshot("MyApp2") == snap
    # File on disk is ciphertext.
    assert b"app_id" not in config.vault_path("MyApp2").read_bytes()


def test_backup_writes_timestamped_file(monkeypatch, tmp_path):
    monkeypatch.setenv("AWS_ADMIN_HOME", str(tmp_path))
    snap = {"app_id": "x", "app_level": {"K": "v"}, "branch_level": {}}
    path = vault.backup_snapshot("MyApp2", snap)
    assert path.exists()
    assert path.parent == tmp_path / "backups"
    assert path.name.startswith("MyApp2-")
    assert path.name.endswith(".enc")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv_linux/bin/pytest tests/test_vault_snapshot.py -v`
Expected: FAIL — `AttributeError: ... has no attribute 'load_snapshot'`.

- [ ] **Step 3: Write minimal implementation**

Append to `src/aws_admin/vault.py`:
```python
from datetime import datetime, timezone


def load_snapshot(app_name: str) -> dict | None:
    path = config.vault_path(app_name)
    if not path.exists():
        return None
    return decrypt(path.read_bytes())


def save_snapshot(app_name: str, data: dict) -> None:
    path = config.vault_path(app_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encrypt(data))
    path.chmod(0o600)


def backup_snapshot(app_name: str, data: dict) -> "Path":
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    path = config.backup_path(app_name, ts)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encrypt(data))
    path.chmod(0o600)
    return path
```

Add `from pathlib import Path` to the imports at the top of `vault.py` (so the return annotation resolves).

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv_linux/bin/pytest tests/test_vault_snapshot.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/aws_admin/vault.py tests/test_vault_snapshot.py
git commit -m "feat: encrypted snapshot load/save and backups"
```

---

## Task 8: Vault — interactive edit buffer (shredded)

**Files:**
- Modify: `src/aws_admin/vault.py`
- Test: `tests/test_vault_edit.py`

- [ ] **Step 1: Write the failing test**

`tests/test_vault_edit.py`:
```python
from aws_admin import vault


def test_parse_buffer_round_trips_sections():
    text = (
        "# ===== APP-LEVEL =====\n"
        "FOO=bar\n"
        "STRIPE=sk_live_xyz\n"
        "# ===== BRANCH-LEVEL =====\n"
        "BRANCH_ONLY=1\n"
    )
    app_level, branch_level = vault.parse_buffer(text)
    assert app_level == {"FOO": "bar", "STRIPE": "sk_live_xyz"}
    assert branch_level == {"BRANCH_ONLY": "1"}


def test_parse_buffer_value_with_equals():
    text = "# ===== APP-LEVEL =====\nURL=postgres://u:p@h/db?x=1\n# ===== BRANCH-LEVEL =====\n"
    app_level, branch_level = vault.parse_buffer(text)
    assert app_level == {"URL": "postgres://u:p@h/db?x=1"}
    assert branch_level == {}


def test_render_buffer_has_both_sections():
    text = vault.render_buffer({"A": "1"}, {"B": "2"})
    assert "# ===== APP-LEVEL =====" in text
    assert "# ===== BRANCH-LEVEL =====" in text
    assert "A=1" in text
    assert "B=2" in text


def test_edit_app_buffer_shreds_temp(monkeypatch, tmp_path):
    monkeypatch.setenv("AWS_ADMIN_HOME", str(tmp_path))
    snap = {"app_id": "x", "branch": "main",
            "app_level": {"A": "1"}, "branch_level": {}, "pulled_at": "t"}
    vault.save_snapshot("MyApp2", snap)

    captured = {}

    def fake_editor(path):
        captured["path"] = path
        # Simulate the user changing A and adding NEW.
        path.write_text(vault.render_buffer({"A": "2", "NEW": "x"}, {}))

    changed = vault.edit_app_buffer("MyApp2", _open_editor=fake_editor)
    assert changed is True
    updated = vault.load_snapshot("MyApp2")
    assert updated["app_level"] == {"A": "2", "NEW": "x"}
    # Temp buffer no longer exists (shredded + unlinked).
    assert not captured["path"].exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv_linux/bin/pytest tests/test_vault_edit.py -v`
Expected: FAIL — `AttributeError: ... has no attribute 'parse_buffer'`.

- [ ] **Step 3: Write minimal implementation**

Append to `src/aws_admin/vault.py`:
```python
import os
import subprocess
import tempfile

_APP_HEADER = "# ===== APP-LEVEL ====="
_BRANCH_HEADER = "# ===== BRANCH-LEVEL ====="


def render_buffer(app_level: dict[str, str], branch_level: dict[str, str]) -> str:
    lines = [_APP_HEADER]
    lines += [f"{k}={v}" for k, v in app_level.items()]
    lines.append(_BRANCH_HEADER)
    lines += [f"{k}={v}" for k, v in branch_level.items()]
    return "\n".join(lines) + "\n"


def parse_buffer(text: str) -> tuple[dict[str, str], dict[str, str]]:
    section = None
    app_level: dict[str, str] = {}
    branch_level: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.rstrip("\n")
        if line.strip() == _APP_HEADER:
            section = app_level
            continue
        if line.strip() == _BRANCH_HEADER:
            section = branch_level
            continue
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if "=" not in line or section is None:
            continue
        key, value = line.split("=", 1)
        section[key.strip()] = value
    return app_level, branch_level


def _default_open_editor(path: "Path") -> None:
    editor = os.environ.get("EDITOR", "nano")
    subprocess.run([editor, str(path)], check=True)


def _shred(path: "Path") -> None:
    """Best-effort overwrite then unlink of a plaintext temp file."""
    try:
        size = path.stat().st_size
        with open(path, "wb") as fh:
            fh.write(os.urandom(max(size, 1)))
            fh.flush()
            os.fsync(fh.fileno())
    except FileNotFoundError:
        return
    finally:
        path.unlink(missing_ok=True)


def edit_app_buffer(app_name: str, _open_editor=_default_open_editor) -> bool:
    """Open the decrypted snapshot in $EDITOR; re-encrypt and shred on save.

    Returns True if values changed. Prefers /dev/shm (RAM-backed) for the temp file.
    """
    snap = load_snapshot(app_name)
    if snap is None:
        raise FileNotFoundError(
            f"No local snapshot for {app_name}. Run `aws-admin env pull {app_name}` first."
        )
    tmp_dir = "/dev/shm" if Path("/dev/shm").is_dir() else None
    fd, name = tempfile.mkstemp(suffix=".env", dir=tmp_dir)
    buf = Path(name)
    os.close(fd)
    buf.chmod(0o600)
    try:
        buf.write_text(render_buffer(snap["app_level"], snap["branch_level"]))
        _open_editor(buf)
        new_app, new_branch = parse_buffer(buf.read_text())
    finally:
        _shred(buf)

    changed = new_app != snap["app_level"] or new_branch != snap["branch_level"]
    if changed:
        snap["app_level"] = new_app
        snap["branch_level"] = new_branch
        save_snapshot(app_name, snap)
    return changed
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv_linux/bin/pytest tests/test_vault_edit.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/aws_admin/vault.py tests/test_vault_edit.py
git commit -m "feat: interactive edit buffer with shredded temp file"
```

---

## Task 9: AWS client factory

**Files:**
- Create: `src/aws_admin/aws_client.py`
- Test: `tests/test_aws_client.py`

- [ ] **Step 1: Write the failing test**

`tests/test_aws_client.py`:
```python
from aws_admin import aws_client, config


def test_amplify_client_uses_region(monkeypatch):
    captured = {}

    def fake_client(service, region_name=None):
        captured["service"] = service
        captured["region"] = region_name
        return object()

    monkeypatch.setattr(aws_client.boto3, "client", fake_client)
    aws_client.amplify_client()
    assert captured["service"] == "amplify"
    assert captured["region"] == config.REGION
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv_linux/bin/pytest tests/test_aws_client.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'aws_admin.aws_client'`.

- [ ] **Step 3: Write minimal implementation**

`src/aws_admin/aws_client.py`:
```python
"""Thin boto3 Amplify client factory (uses the default ~/.aws profile)."""
from __future__ import annotations

import boto3

from . import config


def amplify_client():
    return boto3.client("amplify", region_name=config.REGION)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv_linux/bin/pytest tests/test_aws_client.py -v`
Expected: PASS (1 test).

- [ ] **Step 5: Commit**

```bash
git add src/aws_admin/aws_client.py tests/test_aws_client.py
git commit -m "feat: boto3 amplify client factory"
```

---

## Task 10: Shared test fixtures (fake Amplify client)

**Files:**
- Create: `tests/conftest.py`

- [ ] **Step 1: Write the fake client and fixtures**

`tests/conftest.py`:
```python
import pytest


class FakeAmplify:
    """Hand-written stand-in for the boto3 Amplify client used in command tests.

    Records calls so tests can assert REPLACE-not-merge behavior, and returns
    canned app/branch env vars.
    """

    def __init__(self, app_env=None, branch_env=None, branch_exists=True):
        self._app_env = dict(app_env or {})
        self._branch_env = dict(branch_env or {})
        self._branch_exists = branch_exists
        self.calls = []

    def get_app(self, appId):
        self.calls.append(("get_app", {"appId": appId}))
        return {"app": {"appId": appId, "name": "Fake",
                        "environmentVariables": dict(self._app_env)}}

    def get_branch(self, appId, branchName):
        self.calls.append(("get_branch", {"appId": appId, "branchName": branchName}))
        if not self._branch_exists:
            from botocore.exceptions import ClientError
            raise ClientError(
                {"Error": {"Code": "NotFoundException", "Message": "no branch"}},
                "GetBranch",
            )
        return {"branch": {"branchName": branchName,
                           "environmentVariables": dict(self._branch_env)}}

    def update_app(self, appId, environmentVariables):
        self.calls.append(("update_app",
                           {"appId": appId, "environmentVariables": environmentVariables}))
        self._app_env = dict(environmentVariables)
        return {"app": {"appId": appId}}

    def update_branch(self, appId, branchName, environmentVariables):
        self.calls.append(("update_branch",
                           {"appId": appId, "branchName": branchName,
                            "environmentVariables": environmentVariables}))
        self._branch_env = dict(environmentVariables)
        return {"branch": {"branchName": branchName}}

    def start_job(self, appId, branchName, jobType):
        self.calls.append(("start_job",
                           {"appId": appId, "branchName": branchName, "jobType": jobType}))
        return {"jobSummary": {"jobId": "1", "status": "PENDING"}}


@pytest.fixture
def fake_amplify():
    return FakeAmplify


@pytest.fixture(autouse=True)
def isolated_home(monkeypatch, tmp_path):
    """Every test gets its own AWS_ADMIN_HOME so vault state never leaks across tests."""
    monkeypatch.setenv("AWS_ADMIN_HOME", str(tmp_path))
    return tmp_path
```

- [ ] **Step 2: Verify fixtures import cleanly**

Run: `.venv_linux/bin/pytest tests/ -q`
Expected: existing tests still PASS (the autouse fixture sets `AWS_ADMIN_HOME`; tests that already set it via monkeypatch still work since the later setenv wins).

- [ ] **Step 3: Commit**

```bash
git add tests/conftest.py
git commit -m "test: fake Amplify client and isolated-home fixtures"
```

---

## Task 11: env pull & diff

**Files:**
- Create: `src/aws_admin/commands/env.py`
- Test: `tests/test_env_pull_diff.py`

- [ ] **Step 1: Write the failing test**

`tests/test_env_pull_diff.py`:
```python
from aws_admin.commands import env
from aws_admin import vault


def test_pull_writes_snapshot_and_returns_summary(fake_amplify):
    client = fake_amplify(app_env={"A": "1", "B": "2"}, branch_env={"BR": "x"})
    summary = env.pull("my", client=client)
    snap = vault.load_snapshot("MyApp2")
    assert snap["app_id"] == "d0000000000000"
    assert snap["app_level"] == {"A": "1", "B": "2"}
    assert snap["branch_level"] == {"BR": "x"}
    assert "2 app-level keys" in summary
    assert "1 branch-level key" in summary
    # Summary leaks no values.
    assert "1" not in summary.replace("2 app-level keys", "").replace("1 branch-level key", "")


def test_pull_handles_missing_branch(fake_amplify):
    client = fake_amplify(app_env={"A": "1"}, branch_exists=False)
    env.pull("my", client=client)
    snap = vault.load_snapshot("MyApp2")
    assert snap["branch_level"] == {}


def test_diff_against_remote_is_key_only(fake_amplify):
    # Local snapshot from an earlier pull.
    client = fake_amplify(app_env={"A": "1", "STRIPE": "old"}, branch_env={})
    env.pull("my", client=client)
    # Remote changes underneath us.
    client2 = fake_amplify(app_env={"A": "1", "STRIPE": "new", "C": "3"}, branch_env={})
    text = env.diff("my", client=client2)
    assert "changed: STRIPE" in text
    assert "added: C" in text
    assert "old" not in text and "new" not in text


def test_diff_without_snapshot_raises(fake_amplify):
    client = fake_amplify(app_env={"A": "1"})
    try:
        env.diff("my", client=client)
        assert False, "expected error"
    except FileNotFoundError as e:
        assert "pull" in str(e)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv_linux/bin/pytest tests/test_env_pull_diff.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'aws_admin.commands.env'`.

- [ ] **Step 3: Write minimal implementation**

`src/aws_admin/commands/env.py`:
```python
"""Amplify env-var commands. Values never appear in any return value or print."""
from __future__ import annotations

from datetime import datetime, timezone

from botocore.exceptions import ClientError

from .. import aws_client, config, redact, vault


def _client(client):
    return client if client is not None else aws_client.amplify_client()


def _fetch_remote(client, app_id: str, branch: str) -> tuple[dict, dict]:
    app_env = client.get_app(appId=app_id)["app"].get("environmentVariables", {}) or {}
    try:
        branch_env = (
            client.get_branch(appId=app_id, branchName=branch)["branch"]
            .get("environmentVariables", {}) or {}
        )
    except ClientError as e:
        if e.response["Error"]["Code"] == "NotFoundException":
            branch_env = {}
        else:
            raise
    return dict(app_env), dict(branch_env)


def pull(app_token: str, client=None) -> str:
    ref = config.resolve_app(app_token)
    client = _client(client)
    app_env, branch_env = _fetch_remote(client, ref.app_id, config.DEFAULT_BRANCH)

    existing = vault.load_snapshot(ref.name)
    if existing is not None:
        vault.backup_snapshot(ref.name, existing)

    snap = {
        "app_id": ref.app_id,
        "branch": config.DEFAULT_BRANCH,
        "app_level": app_env,
        "branch_level": branch_env,
        "pulled_at": datetime.now(timezone.utc).isoformat(),
    }
    vault.save_snapshot(ref.name, snap)

    bl = len(branch_env)
    return (f"{ref.name}: {len(app_env)} app-level keys, "
            f"{bl} branch-level {'key' if bl == 1 else 'keys'}")


def diff(app_token: str, client=None) -> str:
    ref = config.resolve_app(app_token)
    snap = vault.load_snapshot(ref.name)
    if snap is None:
        raise FileNotFoundError(
            f"No local snapshot for {ref.name}. Run `aws-admin env pull {app_token}` first."
        )
    client = _client(client)
    app_env, branch_env = _fetch_remote(client, ref.app_id, config.DEFAULT_BRANCH)

    app_d = redact.key_diff(snap["app_level"], app_env)
    branch_d = redact.key_diff(snap["branch_level"], branch_env)
    return (f"[app-level]\n{redact.format_diff(app_d)}\n"
            f"[branch-level]\n{redact.format_diff(branch_d)}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv_linux/bin/pytest tests/test_env_pull_diff.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/aws_admin/commands/env.py tests/test_env_pull_diff.py
git commit -m "feat: env pull and diff commands"
```

---

## Task 12: env push (dry-run default, --apply, branch handling, backup)

**Files:**
- Modify: `src/aws_admin/commands/env.py`
- Test: `tests/test_env_push.py`

- [ ] **Step 1: Write the failing test**

`tests/test_env_push.py`:
```python
from aws_admin.commands import env
from aws_admin import vault


def _seed_local(fake_amplify, app_env, branch_env, local_app, local_branch):
    """Pull remote into a snapshot, then mutate the local snapshot to desired state."""
    client = fake_amplify(app_env=app_env, branch_env=branch_env)
    env.pull("my", client=client)
    snap = vault.load_snapshot("MyApp2")
    snap["app_level"] = local_app
    snap["branch_level"] = local_branch
    vault.save_snapshot("MyApp2", snap)


def test_push_dry_run_does_not_call_update(fake_amplify):
    _seed_local(fake_amplify, {"A": "1"}, {}, {"A": "2"}, {})
    client = fake_amplify(app_env={"A": "1"}, branch_env={})
    out = env.push("my", apply=False, client=client)
    assert "DRY RUN" in out
    assert "changed: A" in out
    assert not any(c[0] == "update_app" for c in client.calls)


def test_push_apply_sends_full_set_and_backs_up(fake_amplify):
    _seed_local(fake_amplify, {"A": "1", "B": "2"}, {}, {"A": "9", "C": "3"}, {})
    client = fake_amplify(app_env={"A": "1", "B": "2"}, branch_env={})
    env.push("my", apply=True, client=client)
    update_calls = [c for c in client.calls if c[0] == "update_app"]
    assert len(update_calls) == 1
    # REPLACE-not-merge: the full desired set is sent, not just the delta.
    assert update_calls[0][1]["environmentVariables"] == {"A": "9", "C": "3"}
    # A backup of the pre-change remote state was written.
    backups = list((vault.config.state_dir() / "backups").glob("MyApp2-*.enc"))
    assert backups


def test_push_apply_updates_branch_when_branch_vars_present(fake_amplify):
    _seed_local(fake_amplify, {"A": "1"}, {"BR": "x"}, {"A": "1"}, {"BR": "y"})
    client = fake_amplify(app_env={"A": "1"}, branch_env={"BR": "x"})
    env.push("my", apply=True, client=client)
    assert any(c[0] == "update_branch" for c in client.calls)


def test_push_apply_skips_branch_when_none(fake_amplify):
    _seed_local(fake_amplify, {"A": "1"}, {}, {"A": "2"}, {})
    client = fake_amplify(app_env={"A": "1"}, branch_env={})
    env.push("my", apply=True, client=client)
    assert not any(c[0] == "update_branch" for c in client.calls)


def test_push_apply_redeploy_triggers_job(fake_amplify):
    _seed_local(fake_amplify, {"A": "1"}, {}, {"A": "2"}, {})
    client = fake_amplify(app_env={"A": "1"}, branch_env={})
    env.push("my", apply=True, redeploy=True, client=client)
    jobs = [c for c in client.calls if c[0] == "start_job"]
    assert jobs and jobs[0][1]["jobType"] == "RELEASE"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv_linux/bin/pytest tests/test_env_push.py -v`
Expected: FAIL — `AttributeError: module 'aws_admin.commands.env' has no attribute 'push'`.

- [ ] **Step 3: Write minimal implementation**

Append to `src/aws_admin/commands/env.py`:
```python
def push(app_token: str, apply: bool = False, redeploy: bool = False, client=None) -> str:
    ref = config.resolve_app(app_token)
    snap = vault.load_snapshot(ref.name)
    if snap is None:
        raise FileNotFoundError(
            f"No local snapshot for {ref.name}. Run `aws-admin env pull {app_token}` first."
        )
    client = _client(client)
    remote_app, remote_branch = _fetch_remote(client, ref.app_id, config.DEFAULT_BRANCH)

    app_d = redact.key_diff(remote_app, snap["app_level"])
    branch_d = redact.key_diff(remote_branch, snap["branch_level"])
    diff_text = (f"[app-level]\n{redact.format_diff(app_d)}\n"
                 f"[branch-level]\n{redact.format_diff(branch_d)}")

    if not apply:
        warn = ""
        if snap["branch_level"]:
            warn = ("\nNOTE: branch-level vars exist and OVERRIDE app-level vars; "
                    "both will be updated on --apply.")
        return f"DRY RUN — {ref.name} (no changes sent):\n{diff_text}{warn}"

    # Back up the pre-change REMOTE state for rollback.
    vault.backup_snapshot(ref.name, {
        "app_id": ref.app_id, "branch": config.DEFAULT_BRANCH,
        "app_level": remote_app, "branch_level": remote_branch,
        "pulled_at": datetime.now(timezone.utc).isoformat(),
    })

    # REPLACE-not-merge: always send the FULL desired set.
    client.update_app(appId=ref.app_id, environmentVariables=snap["app_level"])
    if snap["branch_level"] or remote_branch:
        client.update_branch(
            appId=ref.app_id, branchName=config.DEFAULT_BRANCH,
            environmentVariables=snap["branch_level"],
        )

    result = f"APPLIED — {ref.name}:\n{diff_text}"
    if redeploy:
        client.start_job(appId=ref.app_id, branchName=config.DEFAULT_BRANCH, jobType="RELEASE")
        result += "\nredeploy: RELEASE job started"
    return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv_linux/bin/pytest tests/test_env_push.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/aws_admin/commands/env.py tests/test_env_push.py
git commit -m "feat: env push with dry-run, full-set replace, branch handling, backups"
```

---

## Task 13: env redeploy & edit wrappers

**Files:**
- Modify: `src/aws_admin/commands/env.py`
- Test: `tests/test_env_redeploy_edit.py`

- [ ] **Step 1: Write the failing test**

`tests/test_env_redeploy_edit.py`:
```python
from aws_admin.commands import env
from aws_admin import vault


def test_redeploy_starts_release_job(fake_amplify):
    client = fake_amplify(app_env={"A": "1"})
    out = env.redeploy("my", client=client)
    jobs = [c for c in client.calls if c[0] == "start_job"]
    assert jobs and jobs[0][1]["jobType"] == "RELEASE"
    assert "RELEASE" in out


def test_edit_delegates_to_vault(fake_amplify, monkeypatch):
    client = fake_amplify(app_env={"A": "1"})
    env.pull("my", client=client)
    called = {}

    def fake_edit(app_name, _open_editor=None):
        called["app"] = app_name
        return True

    monkeypatch.setattr(vault, "edit_app_buffer", fake_edit)
    out = env.edit("my")
    assert called["app"] == "MyApp2"
    assert "updated" in out.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv_linux/bin/pytest tests/test_env_redeploy_edit.py -v`
Expected: FAIL — `AttributeError: ... has no attribute 'redeploy'`.

- [ ] **Step 3: Write minimal implementation**

Append to `src/aws_admin/commands/env.py`:
```python
def redeploy(app_token: str, client=None) -> str:
    ref = config.resolve_app(app_token)
    client = _client(client)
    client.start_job(appId=ref.app_id, branchName=config.DEFAULT_BRANCH, jobType="RELEASE")
    return f"{ref.name}: RELEASE job started on '{config.DEFAULT_BRANCH}'."


def edit(app_token: str) -> str:
    ref = config.resolve_app(app_token)
    changed = vault.edit_app_buffer(ref.name)
    if changed:
        return (f"{ref.name}: local snapshot updated. "
                f"Run `aws-admin env push {app_token}` to review, then `--apply`.")
    return f"{ref.name}: no changes."
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv_linux/bin/pytest tests/test_env_redeploy_edit.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/aws_admin/commands/env.py tests/test_env_redeploy_edit.py
git commit -m "feat: env redeploy and edit commands"
```

---

## Task 14: CLI dispatch

**Files:**
- Create: `src/aws_admin/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing test**

`tests/test_cli.py`:
```python
import subprocess
import sys
from aws_admin import cli


def test_cli_help_runs():
    # Smoke: the installed entry point prints usage and exits 0.
    out = subprocess.run(
        [sys.executable, "-m", "aws_admin.cli", "--help"],
        capture_output=True, text=True,
    )
    assert out.returncode == 0
    assert "env" in out.stdout


def test_parse_env_pull():
    args = cli.build_parser().parse_args(["env", "pull", "my"])
    assert args.group == "env"
    assert args.action == "pull"
    assert args.app == "my"


def test_parse_env_push_apply_flag():
    args = cli.build_parser().parse_args(["env", "push", "my", "--apply", "--redeploy"])
    assert args.apply is True
    assert args.redeploy is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv_linux/bin/pytest tests/test_cli.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'aws_admin.cli'`.

- [ ] **Step 3: Write minimal implementation**

`src/aws_admin/cli.py`:
```python
"""Command-line dispatch for aws-admin."""
from __future__ import annotations

import argparse
import sys

from . import config
from .commands import env as env_cmd


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aws-admin",
        description="Secure AWS admin — manage Amplify env vars without exposing values.",
    )
    groups = parser.add_subparsers(dest="group", required=True)

    env_p = groups.add_parser("env", help="Amplify environment variables")
    actions = env_p.add_subparsers(dest="action", required=True)

    for name, help_text in [
        ("pull", "Fetch app+branch env vars into the encrypted local snapshot"),
        ("diff", "Show key-only diff between local snapshot and remote"),
        ("edit", "Open the local snapshot in $EDITOR (values stay local)"),
        ("redeploy", "Start a RELEASE job on the branch"),
    ]:
        sp = actions.add_parser(name, help=help_text)
        sp.add_argument("app", help="App acronym, name, or id (e.g. my, eo, MyApp2)")

    push_p = actions.add_parser("push", help="Push local snapshot to Amplify")
    push_p.add_argument("app", help="App acronym, name, or id")
    push_p.add_argument("--apply", action="store_true",
                        help="Actually send changes (default is dry-run)")
    push_p.add_argument("--redeploy", action="store_true",
                        help="Start a RELEASE job after a successful --apply")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.group == "env":
            if args.action == "pull":
                print(env_cmd.pull(args.app))
            elif args.action == "diff":
                print(env_cmd.diff(args.app))
            elif args.action == "edit":
                print(env_cmd.edit(args.app))
            elif args.action == "redeploy":
                print(env_cmd.redeploy(args.app))
            elif args.action == "push":
                print(env_cmd.push(args.app, apply=args.apply, redeploy=args.redeploy))
    except (config.UnknownAppError, FileNotFoundError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv_linux/bin/pytest tests/test_cli.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/aws_admin/cli.py tests/test_cli.py
git commit -m "feat: CLI dispatch and entry point"
```

---

## Task 15: Cross-cutting redaction leak test

**Files:**
- Test: `tests/test_no_value_leak.py`

This is the security regression test: drive every read/diff/push command with a fixture secret and assert that secret never appears in any output.

- [ ] **Step 1: Write the test**

`tests/test_no_value_leak.py`:
```python
from aws_admin.commands import env
from aws_admin import vault

SECRET = "sk_live_DO_NOT_LEAK_d34db33f"


def test_no_command_output_contains_secret_value(fake_amplify):
    # pull
    client = fake_amplify(app_env={"STRIPE": SECRET, "A": "1"}, branch_env={"BR": SECRET})
    assert SECRET not in env.pull("my", client=client)

    # diff (remote changes the secret)
    client2 = fake_amplify(app_env={"STRIPE": SECRET + "X", "A": "1"}, branch_env={"BR": SECRET})
    assert SECRET not in env.diff("my", client=client2)

    # edit a new secret into the snapshot, then ensure push output never shows it
    snap = vault.load_snapshot("MyApp2")
    snap["app_level"]["STRIPE"] = SECRET + "_rotated"
    vault.save_snapshot("MyApp2", snap)

    client3 = fake_amplify(app_env={"STRIPE": SECRET, "A": "1"}, branch_env={"BR": SECRET})
    dry = env.push("my", apply=False, client=client3)
    applied = env.push("my", apply=True, client=client3)
    assert SECRET not in dry
    assert SECRET not in applied
    assert (SECRET + "_rotated") not in dry
    assert (SECRET + "_rotated") not in applied


def test_vault_files_are_never_plaintext(fake_amplify):
    client = fake_amplify(app_env={"STRIPE": SECRET}, branch_env={})
    env.pull("my", client=client)
    blob = vault.config.vault_path("MyApp2").read_bytes()
    assert SECRET.encode() not in blob
```

- [ ] **Step 2: Run test to verify it passes**

Run: `.venv_linux/bin/pytest tests/test_no_value_leak.py -v`
Expected: PASS (2 tests). If anything FAILS here, a value is leaking — fix the offending command before proceeding.

- [ ] **Step 3: Run the full suite**

Run: `.venv_linux/bin/pytest -q`
Expected: all tests PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/test_no_value_leak.py
git commit -m "test: cross-cutting no-value-leak regression test"
```

---

## Task 16: Global slash commands

**Files:**
- Create: `~/.claude/commands/aws-env-pull.md`
- Create: `~/.claude/commands/aws-env-diff.md`
- Create: `~/.claude/commands/aws-env-push.md`
- Create: `~/.claude/commands/aws-env-edit.md`

- [ ] **Step 1: Create the pull command**

`~/.claude/commands/aws-env-pull.md`:
```markdown
---
description: Pull Amplify env vars into the encrypted local snapshot (no values shown)
argument-hint: <app: my|eo|ab|aa|ag or name/id>
allowed-tools: Bash
---

Run the secure AWS admin tool to pull env vars for the app `$ARGUMENTS`:

!`/mnt/d/Documents/Code/GitHub/AWS-Admin/.venv_linux/bin/aws-admin env pull $ARGUMENTS`

Report the redacted summary back. Never ask for or echo secret values.
```

- [ ] **Step 2: Create the diff command**

`~/.claude/commands/aws-env-diff.md`:
```markdown
---
description: Show key-only diff between local snapshot and live Amplify (no values)
argument-hint: <app: my|eo|ab|aa|ag or name/id>
allowed-tools: Bash
---

Show the key-only diff for `$ARGUMENTS`:

!`/mnt/d/Documents/Code/GitHub/AWS-Admin/.venv_linux/bin/aws-admin env diff $ARGUMENTS`

Summarize which keys changed. Do not request or display values.
```

- [ ] **Step 3: Create the push command**

`~/.claude/commands/aws-env-push.md`:
```markdown
---
description: Dry-run a push of the local snapshot to Amplify (key-only diff)
argument-hint: <app: my|eo|ab|aa|ag or name/id>
allowed-tools: Bash
---

Dry-run the push for `$ARGUMENTS` (this sends nothing):

!`/mnt/d/Documents/Code/GitHub/AWS-Admin/.venv_linux/bin/aws-admin env push $ARGUMENTS`

Report the key-only diff. To actually apply, the user must explicitly confirm; only then
run `aws-admin env push $ARGUMENTS --apply` (add `--redeploy` if they want a redeploy).
```

- [ ] **Step 4: Create the edit command**

`~/.claude/commands/aws-env-edit.md`:
```markdown
---
description: Edit Amplify env-var values locally in your editor (bypasses the model)
argument-hint: <app: my|eo|ab|aa|ag or name/id>
allowed-tools: Bash
---

Editing secret values must happen in YOUR terminal so values never reach the model.
Tell the user to run this themselves (the `!` prefix runs it in their session):

    ! /mnt/d/Documents/Code/GitHub/AWS-Admin/.venv_linux/bin/aws-admin env edit $ARGUMENTS

After they save, suggest `/aws-env-push $ARGUMENTS` to review the key-only diff.
Do NOT run the edit command yourself and never ask the user to paste values into chat.
```

- [ ] **Step 5: Verify commands are discoverable**

Run: `ls -1 ~/.claude/commands/aws-env-*.md`
Expected: lists all four files.

- [ ] **Step 6: Commit (project copy for reference)**

Keep a reference copy in the repo so the commands are version-controlled:
```bash
mkdir -p /mnt/d/Documents/Code/GitHub/AWS-Admin/slash-commands
cp ~/.claude/commands/aws-env-*.md /mnt/d/Documents/Code/GitHub/AWS-Admin/slash-commands/
cd /mnt/d/Documents/Code/GitHub/AWS-Admin
git add slash-commands/
git commit -m "feat: global slash-command wrappers for aws-admin env"
```

---

## Task 17: README & docs

**Files:**
- Create: `README.md`
- Create: `Docs/usage.md`

- [ ] **Step 1: Write `README.md`**

`README.md`:
```markdown
# AWS-Admin

Secure local CLI for AWS admin tasks. First capability: managing Amplify
environment variables **without secret values ever entering a prompt, transcript,
or shell command**.

## Why
`aws amplify get-app --query environmentVariables` prints secrets straight into the
conversation. This tool keeps Amplify as the source of truth, mirrors env vars into an
encrypted local snapshot, and only ever emits key names + redacted digests.

## Install
```bash
python3 -m venv .venv_linux
.venv_linux/bin/pip install -e ".[dev]"
```

## Workflow
```bash
aws-admin env pull my        # mirror MyApp2's env vars into the encrypted snapshot
aws-admin env edit my        # change values in $EDITOR (you type them, not the model)
aws-admin env push my        # dry-run: key-only diff of what would change
aws-admin env push my --apply --redeploy   # send full set + redeploy
aws-admin env diff my        # compare local snapshot vs live
aws-admin env redeploy my    # start a RELEASE job
```

Apps: `eo` ExampleOrg, `ab` AppBeta, `aa` AppAlpha, `ag` AppGamma, `my` MyApp2.

## Where state lives
`~/.config/aws-admin/` (outside the repo, outside Dropbox): `vault.key` (0600),
`vaults/<app>.enc`, `backups/<app>-<ts>.enc`. AWS auth uses your default `~/.aws` profile.

## Security model
- Secret values never appear in command output (single redaction chokepoint, regression-tested).
- No `--set KEY=VALUE` flags (would land in shell history); new values come only via `edit`.
- `push` is dry-run unless `--apply`; every apply backs up the prior remote state first.
- REPLACE-not-merge: pushes always send the full var set; branch-level overrides handled.
```

- [ ] **Step 2: Write `Docs/usage.md`**

`Docs/usage.md`:
```markdown
# aws-admin usage

## Rotating a secret (e.g. a Stripe key)
1. `aws-admin env pull eo` — refresh the local snapshot from Amplify.
2. `aws-admin env edit eo` — your editor opens; change the value, save, close.
   The temp buffer is RAM-backed (/dev/shm) and shredded on close.
3. `aws-admin env push eo` — review the key-only diff (should show `changed: <KEY>`).
4. `aws-admin env push eo --apply --redeploy` — apply and redeploy.

## Branch-level overrides
Amplify branch-level vars override app-level ones. `pull` captures both; `edit` shows both
under `# ===== BRANCH-LEVEL =====`; `push --apply` updates branch-level whenever the
snapshot or remote has any. Best practice: keep branch-level empty.

## Recovery
Pre-change remote state is saved to `~/.config/aws-admin/backups/<app>-<ts>.enc` before
every `--apply`. To inspect a backup, decrypt it with the same key
(`~/.config/aws-admin/vault.key`).
```

- [ ] **Step 3: Commit**

```bash
git add README.md Docs/usage.md
git commit -m "docs: README and usage guide"
```

---

## Task 18: Final verification

- [ ] **Step 1: Full suite + coverage of the security invariant**

Run:
```bash
cd /mnt/d/Documents/Code/GitHub/AWS-Admin
.venv_linux/bin/pytest -q
```
Expected: all tests PASS (config, redact, vault, env pull/diff/push/redeploy/edit, cli, no-leak).

- [ ] **Step 2: Confirm no secrets are tracked by git**

Run:
```bash
git status --porcelain
git ls-files | grep -E '\.(enc|key)$' && echo "LEAK: secret files tracked!" || echo "clean: no secret files tracked"
```
Expected: working tree clean; "clean: no secret files tracked".

- [ ] **Step 3: Optional live smoke test (manual, off by default)**

Only if you want to confirm real AWS wiring (makes one read-only call):
```bash
.venv_linux/bin/aws-admin env pull my
```
Expected: `MyApp2: N app-level keys, M branch-level key(s)` with no values shown.
```

---

## Self-Review Notes

- **Spec coverage:** package layout (Task 1), config+aliases incl. MyApp2 (Tasks 2-3), Fernet vault + 0600 key + backups (Tasks 6-7), in-memory-only decrypt + shredded edit buffer (Task 8), pull/diff/push/redeploy/edit incl. REPLACE-not-merge + branch handling + dry-run default (Tasks 11-13), redaction chokepoint + key-only diffs + no value flags + leak test (Tasks 4-5, 15), boto3 default profile (Task 9), slash commands (Task 16), tests with fake client/no moto (Task 10), opt-in live smoke (Task 18). All spec sections map to tasks.
- **Type consistency:** `AppRef(name, app_id)`, snapshot keys (`app_id/branch/app_level/branch_level/pulled_at`), `redact.key_diff`→`{added,removed,changed}`, and command signatures (`pull/diff(app, client=None)`, `push(app, apply, redeploy, client=None)`, `edit(app)`, `redeploy(app, client=None)`) are used consistently across tasks and the CLI.
- **No placeholders:** every code step contains complete code; every run step states the exact command and expected result.
