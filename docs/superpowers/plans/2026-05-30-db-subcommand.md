# DB Subcommand Group — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `db` command group to `aws-admin` for PostgreSQL admin work where the DB password, query results (PII), and sensitive SQL literals never enter a prompt, transcript, or shell command.

**Architecture:** New `db/` package inside `aws_admin`, reusing the vault (encrypted DB password), redaction posture, `config`, and CLI. `psycopg` v3 connects with `sslmode=require`; curated read-only `.sql` queries plus a file-fed runner; `{{NAME}}` placeholders filled in a shredded `$EDITOR` buffer and bound as parameters; results default to a 0600 file with a redacted summary; read-only by default with `--write` (rollback preview) / `--commit` (persist) gates.

**Tech Stack:** Python 3.12, `psycopg[binary]` v3, `cryptography` (existing vault), `pytest` (fake DB connection — no live DB in the suite).

**Builds on:** spec `docs/superpowers/specs/2026-05-30-db-subcommand-design.md` and the existing `aws-admin` package.

---

## Conventions

- Run Python/pytest via `.venv_linux/bin/`.
- The autouse `isolated_home` fixture (tests/conftest.py) sets `AWS_ADMIN_HOME` per test.
- DB connection facts live in `config.py` (not secret). Password comes from the vault.
- Placeholder syntax in SQL is `{{NAME}}`, `NAME` matching `[A-Za-z_][A-Za-z0-9_]*`.

## File Structure

- `pyproject.toml` — add `psycopg[binary]` dependency.
- `src/aws_admin/config.py` — DB constants + `db_password_path()` + `results_dir()`.
- `src/aws_admin/vault.py` — `edit_buffer()` (extracted), `set_db_password()`, `get_db_password()`.
- `src/aws_admin/db/__init__.py`
- `src/aws_admin/db/placeholders.py` — find/translate `{{NAME}}`, collect values via shredded buffer.
- `src/aws_admin/db/connection.py` — build connection from config + vault password.
- `src/aws_admin/db/runner.py` — execute SQL with bound params; read-only/write/commit modes.
- `src/aws_admin/db/results.py` — write rows to 0600 file; redacted summary; inline render; clean.
- `src/aws_admin/db/queries.py` — curated-query registry + loaders.
- `src/aws_admin/queries/*.sql` — curated read-only queries.
- `src/aws_admin/commands/db.py` — `set_password`/`check`/`list_queries`/`run`/`clean_results`.
- `src/aws_admin/cli.py` — `db` subparser + dispatch + psycopg error handling.
- `tests/conftest.py` — add `fake_db` fixture (fake psycopg connection/cursor).
- `tests/test_db_*.py` — per-module tests + no-value-leak.
- `slash-commands/` + `~/.claude/commands/` — `/aws-db-*` wrappers.

---

## Task 1: Dependency + config additions

**Files:**
- Modify: `pyproject.toml`
- Modify: `src/aws_admin/config.py`
- Test: `tests/test_config_db.py`

- [ ] **Step 1: Add psycopg to pyproject**

In `pyproject.toml`, change the `dependencies` list to:
```toml
dependencies = [
    "boto3>=1.34",
    "cryptography>=42.0",
    "psycopg[binary]>=3.1,<3.3",
]
```

- [ ] **Step 2: Install**

Run:
```bash
cd /mnt/d/Documents/Code/GitHub/AWS-Admin
.venv_linux/bin/pip install -q -e ".[dev]"
.venv_linux/bin/python -c "import psycopg; print('psycopg', psycopg.__version__)"
```
Expected: prints a `3.2.x` version (mature line). If pip resolves a version published <7 days ago, lower the ceiling and reinstall.

- [ ] **Step 3: Write the failing test**

`tests/test_config_db.py`:
```python
from aws_admin import config


def test_db_constants():
    assert config.DB_HOST == "your-db.xxxxxxxxxxxx.us-east-1.rds.amazonaws.com"
    assert config.DB_PORT == 5432
    assert config.DB_NAME == "your_db"
    assert config.DB_USER == "postgres"
    assert config.DB_SSLMODE == "require"


def test_db_paths(monkeypatch, tmp_path):
    monkeypatch.setenv("AWS_ADMIN_HOME", str(tmp_path))
    assert config.db_password_path() == tmp_path / "db-password.enc"
    assert config.results_dir() == tmp_path / "results"
```

- [ ] **Step 4: Run test to verify it fails**

Run: `.venv_linux/bin/pytest tests/test_config_db.py -v`
Expected: FAIL — `AttributeError: ... has no attribute 'DB_HOST'`.

- [ ] **Step 5: Add to `src/aws_admin/config.py`**

Append:
```python
# --- Database (RDS PostgreSQL, multi-tenant) ---
DB_HOST = "your-db.xxxxxxxxxxxx.us-east-1.rds.amazonaws.com"
DB_PORT = 5432
DB_NAME = "your_db"
DB_USER = "postgres"
DB_SSLMODE = "require"


def db_password_path() -> Path:
    return state_dir() / "db-password.enc"


def results_dir() -> Path:
    return state_dir() / "results"
```

- [ ] **Step 6: Run test to verify it passes**

Run: `.venv_linux/bin/pytest tests/test_config_db.py -v`
Expected: PASS (2 tests).

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml src/aws_admin/config.py tests/test_config_db.py
git commit -m "feat(db): add psycopg dep and DB config constants/paths"
```

---

## Task 2: Vault — DB password storage

**Files:**
- Modify: `src/aws_admin/vault.py`
- Test: `tests/test_vault_db_password.py`

- [ ] **Step 1: Write the failing test**

`tests/test_vault_db_password.py`:
```python
import pytest
from aws_admin import vault, config


def test_set_then_get_db_password(monkeypatch, tmp_path):
    monkeypatch.setenv("AWS_ADMIN_HOME", str(tmp_path))
    vault.set_db_password("s3cr3t-pw")
    assert vault.get_db_password() == "s3cr3t-pw"
    # On disk it is ciphertext, not plaintext.
    assert b"s3cr3t-pw" not in config.db_password_path().read_bytes()


def test_get_missing_db_password_raises(monkeypatch, tmp_path):
    monkeypatch.setenv("AWS_ADMIN_HOME", str(tmp_path))
    with pytest.raises(FileNotFoundError) as exc:
        vault.get_db_password()
    assert "set-password" in str(exc.value)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv_linux/bin/pytest tests/test_vault_db_password.py -v`
Expected: FAIL — `AttributeError: ... has no attribute 'set_db_password'`.

- [ ] **Step 3: Add to `src/aws_admin/vault.py`**

Append:
```python
def set_db_password(password: str) -> None:
    """Store the PostgreSQL password Fernet-encrypted (0600), never echoed."""
    _write_private(config.db_password_path(), encrypt({"password": password}))


def get_db_password() -> str:
    path = config.db_password_path()
    if not path.exists():
        raise FileNotFoundError(
            "No DB password stored. Run `aws-admin db set-password` first."
        )
    return decrypt(path.read_bytes())["password"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv_linux/bin/pytest tests/test_vault_db_password.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/aws_admin/vault.py tests/test_vault_db_password.py
git commit -m "feat(db): store DB password in the encrypted vault"
```

---

## Task 3: Vault — extract reusable `edit_buffer`

The `{{NAME}}` value-collection (Task 4) needs the same RAM-backed, shredded edit
buffer that `edit_app_buffer` uses. Extract it into a public helper and refactor
`edit_app_buffer` to use it. Existing edit tests are the safety net.

**Files:**
- Modify: `src/aws_admin/vault.py`
- Test: `tests/test_vault_edit_buffer.py`

- [ ] **Step 1: Write the failing test**

`tests/test_vault_edit_buffer.py`:
```python
from pathlib import Path
from aws_admin import vault


def test_edit_buffer_round_trip_and_shred(monkeypatch, tmp_path):
    monkeypatch.setenv("AWS_ADMIN_HOME", str(tmp_path))
    captured = {}

    def fake_editor(path):
        captured["path"] = path
        assert path.read_text() == "SEED\n"
        path.write_text("EDITED\n")

    out = vault.edit_buffer("SEED\n", _open_editor=fake_editor)
    assert out == "EDITED\n"
    assert not captured["path"].exists()  # shredded


def test_edit_buffer_shreds_on_editor_exception(monkeypatch, tmp_path):
    monkeypatch.setenv("AWS_ADMIN_HOME", str(tmp_path))
    captured = {}

    def boom(path):
        captured["path"] = path
        raise RuntimeError("crash")

    import pytest
    with pytest.raises(RuntimeError):
        vault.edit_buffer("X", _open_editor=boom)
    assert not captured["path"].exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv_linux/bin/pytest tests/test_vault_edit_buffer.py -v`
Expected: FAIL — `AttributeError: ... has no attribute 'edit_buffer'`.

- [ ] **Step 3: Add `edit_buffer` and refactor `edit_app_buffer`**

Add this function to `src/aws_admin/vault.py`:
```python
def edit_buffer(initial_text: str, _open_editor=None) -> str:
    """Open `initial_text` in $EDITOR via a RAM-backed 0600 temp file; return the
    saved text. The temp file is shredded in a finally (even on editor error).
    """
    opener = _open_editor or _default_open_editor
    tmp_dir = "/dev/shm" if Path("/dev/shm").is_dir() else None
    fd, name = tempfile.mkstemp(suffix=".txt", dir=tmp_dir)
    buf = Path(name)
    os.close(fd)
    buf.chmod(0o600)
    try:
        buf.write_text(initial_text)
        opener(buf)
        return buf.read_text()
    finally:
        _shred(buf)
```

Then replace the body of `edit_app_buffer` so it delegates to `edit_buffer`
(keeping its signature and all surrounding behavior — the malformed-line warning,
change detection, and save). The new body:
```python
def edit_app_buffer(app_name: str, _open_editor=_default_open_editor) -> bool:
    """Open the decrypted snapshot in $EDITOR; re-encrypt and shred on save.

    Returns True if values changed.
    """
    snap = load_snapshot(app_name)
    if snap is None:
        raise FileNotFoundError(
            f"No local snapshot for {app_name}. Run `aws-admin env pull {app_name}` first."
        )
    text = edit_buffer(render_buffer(snap["app_level"], snap["branch_level"]),
                       _open_editor=_open_editor)
    bad = _malformed_line_numbers(text)
    if bad:
        import sys
        print(
            f"warning: ignored {len(bad)} line(s) without '=' at line number(s) "
            f"{', '.join(map(str, bad))} (content not shown).",
            file=sys.stderr,
        )
    new_app, new_branch = parse_buffer(text)
    changed = new_app != snap["app_level"] or new_branch != snap["branch_level"]
    if changed:
        snap["app_level"] = new_app
        snap["branch_level"] = new_branch
        save_snapshot(app_name, snap)
    return changed
```
Remove the now-unused inline temp-file/`mkstemp`/`_shred` code that previously
lived in `edit_app_buffer` (the `tmp_dir`/`fd`/`buf` block). Keep `_shred`,
`_default_open_editor`, `render_buffer`, `parse_buffer`, `_malformed_line_numbers`.

- [ ] **Step 4: Run the edit tests AND the existing edit-app tests**

Run: `.venv_linux/bin/pytest tests/test_vault_edit_buffer.py tests/test_vault_edit.py -v`
Expected: PASS — both the new `edit_buffer` tests and ALL pre-existing
`test_vault_edit.py` tests (shred-on-exception, change detection, CRLF) still pass.

- [ ] **Step 5: Full suite**

Run: `.venv_linux/bin/pytest -q`
Expected: all pass (refactor changed no external behavior).

- [ ] **Step 6: Commit**

```bash
git add src/aws_admin/vault.py tests/test_vault_edit_buffer.py
git commit -m "refactor(vault): extract reusable edit_buffer; edit_app_buffer delegates to it"
```

---

## Task 4: Placeholders

**Files:**
- Create: `src/aws_admin/db/__init__.py`
- Create: `src/aws_admin/db/placeholders.py`
- Test: `tests/test_db_placeholders.py`

- [ ] **Step 1: Create the package init**

`src/aws_admin/db/__init__.py`: (empty file)

- [ ] **Step 2: Write the failing test**

`tests/test_db_placeholders.py`:
```python
import pytest
from aws_admin.db import placeholders


def test_find_placeholders_distinct_in_order():
    sql = "SELECT * FROM t WHERE a = {{EMAIL}} AND b = {{TOKEN}} OR c = {{EMAIL}}"
    assert placeholders.find_placeholders(sql) == ["EMAIL", "TOKEN"]


def test_find_placeholders_none():
    assert placeholders.find_placeholders("SELECT 1") == []


def test_to_psycopg_translates_tokens():
    sql = "WHERE a = {{EMAIL}} AND b = {{TOKEN}}"
    assert placeholders.to_psycopg(sql) == "WHERE a = %(EMAIL)s AND b = %(TOKEN)s"


def test_to_psycopg_leaves_casts_untouched():
    sql = "SELECT id::text FROM t WHERE x = {{V}}"
    assert placeholders.to_psycopg(sql) == "SELECT id::text FROM t WHERE x = %(V)s"


def test_render_and_parse_values_buffer_round_trip():
    text = placeholders.render_values_buffer(["EMAIL", "TOKEN"])
    assert "EMAIL=" in text and "TOKEN=" in text
    filled = text.replace("EMAIL=", "EMAIL=a@b.com").replace("TOKEN=", "TOKEN=xyz")
    assert placeholders.parse_values_buffer(filled) == {"EMAIL": "a@b.com", "TOKEN": "xyz"}


def test_collect_values_binds_filled(monkeypatch):
    def fake_editor(path):
        path.write_text(placeholders.render_values_buffer(["TOKEN"]).replace("TOKEN=", "TOKEN=secret"))
    # collect_values delegates editing to vault.edit_buffer, which we exercise via _open_editor.
    vals = placeholders.collect_values(["TOKEN"], _open_editor=fake_editor)
    assert vals == {"TOKEN": "secret"}


def test_collect_values_unfilled_raises(monkeypatch):
    def fake_editor(path):
        pass  # leaves TOKEN= blank
    with pytest.raises(ValueError) as exc:
        placeholders.collect_values(["TOKEN"], _open_editor=fake_editor)
    assert "TOKEN" in str(exc.value)
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv_linux/bin/pytest tests/test_db_placeholders.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'aws_admin.db.placeholders'`.

- [ ] **Step 4: Create `src/aws_admin/db/placeholders.py`**

```python
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
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv_linux/bin/pytest tests/test_db_placeholders.py -v`
Expected: PASS (7 tests).

- [ ] **Step 6: Commit**

```bash
git add src/aws_admin/db/__init__.py src/aws_admin/db/placeholders.py tests/test_db_placeholders.py
git commit -m "feat(db): {{NAME}} placeholder detection, translation, and shredded value collection"
```

---

## Task 5: Connection

**Files:**
- Create: `src/aws_admin/db/connection.py`
- Test: `tests/test_db_connection.py`

- [ ] **Step 1: Write the failing test**

`tests/test_db_connection.py`:
```python
from aws_admin import vault, config
from aws_admin.db import connection


class _Conn:
    def __init__(self):
        self.read_only = None


def test_connect_uses_config_and_vault_password(monkeypatch, tmp_path):
    monkeypatch.setenv("AWS_ADMIN_HOME", str(tmp_path))
    vault.set_db_password("pw-123")
    captured = {}

    def fake_connect(**kwargs):
        captured.update(kwargs)
        return _Conn()

    conn = connection.connect(read_only=True, _connect=fake_connect)
    assert captured["host"] == config.DB_HOST
    assert captured["port"] == config.DB_PORT
    assert captured["dbname"] == config.DB_NAME
    assert captured["user"] == config.DB_USER
    assert captured["sslmode"] == config.DB_SSLMODE
    assert captured["password"] == "pw-123"
    assert conn.read_only is True


def test_connect_write_mode_sets_read_only_false(monkeypatch, tmp_path):
    monkeypatch.setenv("AWS_ADMIN_HOME", str(tmp_path))
    vault.set_db_password("pw")
    conn = connection.connect(read_only=False, _connect=lambda **k: _Conn())
    assert conn.read_only is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv_linux/bin/pytest tests/test_db_connection.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'aws_admin.db.connection'`.

- [ ] **Step 3: Create `src/aws_admin/db/connection.py`**

```python
"""Build a psycopg connection from config + the vault-stored password."""
from __future__ import annotations

import psycopg

from .. import config, vault


def connect(read_only: bool = True, _connect=None):
    """Open a psycopg connection (sslmode=require). `_connect` is injectable for tests.

    The connection's transactions are read-only unless `read_only=False`.
    """
    connect_fn = _connect or psycopg.connect
    conn = connect_fn(
        host=config.DB_HOST,
        port=config.DB_PORT,
        dbname=config.DB_NAME,
        user=config.DB_USER,
        password=vault.get_db_password(),
        sslmode=config.DB_SSLMODE,
    )
    conn.read_only = read_only
    return conn
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv_linux/bin/pytest tests/test_db_connection.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/aws_admin/db/connection.py tests/test_db_connection.py
git commit -m "feat(db): psycopg connection from config + vault password"
```

---

## Task 6: Fake DB fixture

**Files:**
- Modify: `tests/conftest.py`

- [ ] **Step 1: Append the fake DB to `tests/conftest.py`**

Add at the end of `tests/conftest.py`:
```python
class _Col:
    """Stand-in for a psycopg Column (exposes .name)."""
    def __init__(self, name):
        self.name = name


class FakeCursor:
    def __init__(self, description=None, rows=None, rowcount=0):
        self._description = [_Col(n) for n in description] if description is not None else None
        self._rows = list(rows or [])
        self.rowcount = rowcount
        self.executed = []

    @property
    def description(self):
        return self._description

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchall(self):
        return list(self._rows)


class FakeDBConn:
    """Fake psycopg connection that records commit/rollback/close and read_only."""
    def __init__(self, description=None, rows=None, rowcount=0):
        self.read_only = None
        self.autocommit = False
        self.calls = []
        self._cursor = FakeCursor(description, rows, rowcount)

    def cursor(self):
        return self._cursor

    def commit(self):
        self.calls.append("commit")

    def rollback(self):
        self.calls.append("rollback")

    def close(self):
        self.calls.append("close")


@pytest.fixture
def fake_db():
    return FakeDBConn
```

- [ ] **Step 2: Verify the suite still collects/passes**

Run: `.venv_linux/bin/pytest -q`
Expected: all existing tests still PASS (only added unused fixture + classes).

- [ ] **Step 3: Commit**

```bash
git add tests/conftest.py
git commit -m "test(db): fake psycopg connection/cursor fixture"
```

---

## Task 7: Runner

**Files:**
- Create: `src/aws_admin/db/runner.py`
- Test: `tests/test_db_runner.py`

- [ ] **Step 1: Write the failing test**

`tests/test_db_runner.py`:
```python
import pytest
from aws_admin.db import runner


def test_read_query_returns_rows_and_rolls_back(fake_db):
    conn = fake_db(description=["id", "email"], rows=[(1, "a@b.com")], rowcount=1)
    result = runner.run_sql("SELECT id, email FROM users", {}, conn=conn)
    assert result.columns == ["id", "email"]
    assert result.rows == [(1, "a@b.com")]
    assert result.committed is False
    assert "rollback" in conn.calls and "commit" not in conn.calls
    assert "close" not in conn.calls  # caller-provided conn is not closed


def test_passes_params_to_cursor(fake_db):
    conn = fake_db(description=["id"], rows=[(1,)], rowcount=1)
    runner.run_sql("SELECT id FROM t WHERE x = %(V)s", {"V": "secret"}, conn=conn)
    assert conn.cursor().executed[0] == ("SELECT id FROM t WHERE x = %(V)s", {"V": "secret"})


def test_write_preview_rolls_back(fake_db):
    conn = fake_db(description=None, rowcount=3)
    result = runner.run_sql("UPDATE users SET x = 1", {}, write=True, conn=conn)
    assert result.columns == []
    assert result.rowcount == 3
    assert result.committed is False
    assert "rollback" in conn.calls and "commit" not in conn.calls


def test_write_commit_persists(fake_db):
    conn = fake_db(description=None, rowcount=2)
    result = runner.run_sql("DELETE FROM spam", {}, write=True, commit=True, conn=conn)
    assert result.committed is True
    assert "commit" in conn.calls and "rollback" not in conn.calls


def test_commit_without_write_rejected(fake_db):
    conn = fake_db(description=None, rowcount=0)
    with pytest.raises(ValueError) as exc:
        runner.run_sql("DELETE FROM t", {}, write=False, commit=True, conn=conn)
    assert "--write" in str(exc.value)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv_linux/bin/pytest tests/test_db_runner.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'aws_admin.db.runner'`.

- [ ] **Step 3: Create `src/aws_admin/db/runner.py`**

```python
"""Execute SQL with bound parameters under read-only / write / commit transaction modes."""
from __future__ import annotations

from dataclasses import dataclass

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

    A caller-provided `conn` is used as-is and left open; otherwise a connection is
    opened (read-only iff not write) and closed before returning.
    """
    if commit and not write:
        raise ValueError("--commit requires --write")
    params = params or {}
    owns = conn is None
    if owns:
        conn = connection.connect(read_only=not write)
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv_linux/bin/pytest tests/test_db_runner.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/aws_admin/db/runner.py tests/test_db_runner.py
git commit -m "feat(db): SQL runner with read-only/write/commit transaction modes"
```

---

## Task 8: Results

**Files:**
- Create: `src/aws_admin/db/results.py`
- Test: `tests/test_db_results.py`

- [ ] **Step 1: Write the failing test**

`tests/test_db_results.py`:
```python
import os
import stat
from aws_admin import config
from aws_admin.db import results


def test_write_results_creates_0600_csv(monkeypatch, tmp_path):
    monkeypatch.setenv("AWS_ADMIN_HOME", str(tmp_path))
    path = results.write_results("unverified-users", ["id", "email"], [(1, "a@b.com")])
    assert path.parent == config.results_dir()
    assert path.suffix == ".csv"
    mode = stat.S_IMODE(os.stat(path).st_mode)
    assert mode == 0o600
    content = path.read_text()
    assert "id,email" in content
    assert "a@b.com" in content  # the file is where the data legitimately lives


def test_summary_is_key_and_count_only():
    out = results.summary("q", ["id", "email"], [(1, "secret@x.com")], "/p/q.csv")
    assert "1 row" in out
    assert "columns: [id, email]" in out
    assert "/p/q.csv" in out
    assert "secret@x.com" not in out  # summary never contains values


def test_render_inline_includes_values():
    out = results.render_inline(["id", "email"], [(1, "a@b.com")])
    assert "id,email" in out
    assert "a@b.com" in out


def test_clean_results_removes_files(monkeypatch, tmp_path):
    monkeypatch.setenv("AWS_ADMIN_HOME", str(tmp_path))
    results.write_results("q", ["a"], [(1,)])
    results.write_results("q", ["a"], [(2,)])
    n = results.clean_results()
    assert n == 2
    assert list(config.results_dir().glob("*")) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv_linux/bin/pytest tests/test_db_results.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'aws_admin.db.results'`.

- [ ] **Step 3: Create `src/aws_admin/db/results.py`**

```python
"""Persist query results to a 0600 file and render redacted summaries.

The summary (default output) contains only key/structural info — never row values.
Row values appear only in the on-disk file and in render_inline (used by --show)."""
from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path

from .. import config


def write_results(name: str, columns: list[str], rows: list[tuple]) -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    directory = config.results_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}-{ts}.csv"
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(columns)
        writer.writerows(rows)
    path.chmod(0o600)
    return path


def summary(name: str, columns: list[str], rows: list[tuple], path) -> str:
    n = len(rows)
    cols = ", ".join(columns)
    plural = "row" if n == 1 else "rows"
    return f"{name}: {n} {plural}, columns: [{cols}], written to {path}"


def render_inline(columns: list[str], rows: list[tuple]) -> str:
    lines = [",".join(columns)]
    lines += [",".join("" if v is None else str(v) for v in row) for row in rows]
    return "\n".join(lines)


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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv_linux/bin/pytest tests/test_db_results.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/aws_admin/db/results.py tests/test_db_results.py
git commit -m "feat(db): result file writer, redacted summary, inline render, clean"
```

---

## Task 9: Curated query registry + SQL files

**Files:**
- Create: `src/aws_admin/queries/unverified-users.sql`
- Create: `src/aws_admin/queries/verification-tokens.sql`
- Create: `src/aws_admin/queries/user-count.sql`
- Create: `src/aws_admin/db/queries.py`
- Modify: `pyproject.toml` (package the .sql files)
- Test: `tests/test_db_queries.py`

- [ ] **Step 1: Create the three SQL files**

`src/aws_admin/queries/unverified-users.sql`:
```sql
SELECT id, email, name, "emailVerified", "createdAt"
FROM users
WHERE "emailVerified" IS NULL
ORDER BY "createdAt" DESC;
```

`src/aws_admin/queries/verification-tokens.sql`:
```sql
SELECT id, email, type, "createdAt", "expiresAt", "usedAt"
FROM verification_tokens
ORDER BY "createdAt" DESC
LIMIT 10;
```

`src/aws_admin/queries/user-count.sql`:
```sql
SELECT count(*) AS users FROM users;
```

- [ ] **Step 2: Package the SQL files in pyproject**

In `pyproject.toml`, add a package-data section so the `.sql` files ship with the
package. After the `[tool.setuptools.packages.find]` block, add:
```toml
[tool.setuptools.package-data]
aws_admin = ["queries/*.sql"]
```

- [ ] **Step 3: Write the failing test**

`tests/test_db_queries.py`:
```python
import pytest
from aws_admin.db import queries


def test_list_curated_has_seed_queries():
    names = dict(queries.list_curated())
    assert "unverified-users" in names
    assert "verification-tokens" in names
    assert "user-count" in names
    # descriptions are non-empty
    assert all(desc for desc in names.values())


def test_is_curated():
    assert queries.is_curated("user-count") is True
    assert queries.is_curated("./some/file.sql") is False


def test_load_query_returns_sql_text():
    sql = queries.load_query("user-count")
    assert "count(*)" in sql.lower()


def test_load_unknown_raises():
    with pytest.raises(KeyError):
        queries.load_query("does-not-exist")
```

- [ ] **Step 4: Run test to verify it fails**

Run: `.venv_linux/bin/pytest tests/test_db_queries.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'aws_admin.db.queries'`.

- [ ] **Step 5: Create `src/aws_admin/db/queries.py`**

```python
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
```

- [ ] **Step 6: Reinstall (so package-data/new files resolve) and run tests**

Run:
```bash
.venv_linux/bin/pip install -q -e ".[dev]"
.venv_linux/bin/pytest tests/test_db_queries.py -v
```
Expected: PASS (4 tests).

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml src/aws_admin/queries/ src/aws_admin/db/queries.py tests/test_db_queries.py
git commit -m "feat(db): curated read-only query registry and seed SQL files"
```

---

## Task 10: Commands

**Files:**
- Create: `src/aws_admin/commands/db.py`
- Test: `tests/test_db_commands.py`

- [ ] **Step 1: Write the failing test**

`tests/test_db_commands.py`:
```python
import pytest
from aws_admin import vault, config
from aws_admin.commands import db


def test_set_password_stores(monkeypatch, tmp_path):
    monkeypatch.setenv("AWS_ADMIN_HOME", str(tmp_path))
    db.set_password(_getpass=lambda prompt="": "pw-xyz")
    assert vault.get_db_password() == "pw-xyz"


def test_check_returns_connected_message(fake_db):
    conn = fake_db(description=["?column?"], rows=[(1,)], rowcount=1)
    out = db.check(conn=conn)
    assert "connected as postgres@" in out
    assert config.DB_NAME in out
    assert "read-only" in out


def test_list_queries_shows_names(monkeypatch):
    out = db.list_queries()
    assert "unverified-users" in out
    assert "user-count" in out


def test_run_curated_writes_results_and_redacted_summary(monkeypatch, tmp_path, fake_db):
    monkeypatch.setenv("AWS_ADMIN_HOME", str(tmp_path))
    conn = fake_db(description=["id", "email"], rows=[(1, "a@b.com")], rowcount=1)
    out = db.run("unverified-users", conn=conn)
    assert "1 row" in out and "written to" in out
    assert "a@b.com" not in out  # summary never leaks values


def test_run_curated_show_includes_values(monkeypatch, tmp_path, fake_db):
    monkeypatch.setenv("AWS_ADMIN_HOME", str(tmp_path))
    conn = fake_db(description=["users"], rows=[(42,)], rowcount=1)
    out = db.run("user-count", show=True, conn=conn)
    assert "42" in out  # --show opts into inline values


def test_run_curated_rejects_write(fake_db):
    conn = fake_db(description=["users"], rows=[(1,)], rowcount=1)
    with pytest.raises(ValueError) as exc:
        db.run("user-count", write=True, conn=conn)
    assert "read-only" in str(exc.value)


def test_run_file_with_placeholder_binds_params(monkeypatch, tmp_path, fake_db):
    monkeypatch.setenv("AWS_ADMIN_HOME", str(tmp_path))
    sql_file = tmp_path / "lookup.sql"
    sql_file.write_text("SELECT id FROM users WHERE email = {{EMAIL}}")
    conn = fake_db(description=["id"], rows=[(7,)], rowcount=1)
    out = db.run(str(sql_file), conn=conn,
                 _collect_values=lambda names: {"EMAIL": "secret@x.com"})
    executed_sql, executed_params = conn.cursor().executed[0]
    assert executed_sql == "SELECT id FROM users WHERE email = %(EMAIL)s"
    assert executed_params == {"EMAIL": "secret@x.com"}
    assert "secret@x.com" not in out  # bound value never in output


def test_run_missing_target_raises(monkeypatch, tmp_path, fake_db):
    monkeypatch.setenv("AWS_ADMIN_HOME", str(tmp_path))
    conn = fake_db()
    with pytest.raises(FileNotFoundError):
        db.run("not-a-query-or-file", conn=conn)


def test_run_write_preview_message(monkeypatch, tmp_path, fake_db):
    monkeypatch.setenv("AWS_ADMIN_HOME", str(tmp_path))
    sql_file = tmp_path / "upd.sql"
    sql_file.write_text("UPDATE users SET name = 'x' WHERE id = 1")
    conn = fake_db(description=None, rowcount=1)
    out = db.run(str(sql_file), write=True, conn=conn)
    assert "would change" in out and "rolled back" in out


def test_run_write_commit_message(monkeypatch, tmp_path, fake_db):
    monkeypatch.setenv("AWS_ADMIN_HOME", str(tmp_path))
    sql_file = tmp_path / "del.sql"
    sql_file.write_text("DELETE FROM spam WHERE id = 1")
    conn = fake_db(description=None, rowcount=1)
    out = db.run(str(sql_file), write=True, commit=True, conn=conn)
    assert "committed" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv_linux/bin/pytest tests/test_db_commands.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'aws_admin.commands.db'`.

- [ ] **Step 3: Create `src/aws_admin/commands/db.py`**

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv_linux/bin/pytest tests/test_db_commands.py -v`
Expected: PASS (10 tests).

- [ ] **Step 5: Commit**

```bash
git add src/aws_admin/commands/db.py tests/test_db_commands.py
git commit -m "feat(db): set-password/check/list/run/clean-results commands"
```

---

## Task 11: CLI wiring

**Files:**
- Modify: `src/aws_admin/cli.py`
- Test: `tests/test_cli_db.py`

- [ ] **Step 1: Write the failing test**

`tests/test_cli_db.py`:
```python
from aws_admin import cli


def test_parse_db_run_flags():
    args = cli.build_parser().parse_args(["db", "run", "user-count", "--write", "--commit", "--show"])
    assert args.group == "db"
    assert args.action == "run"
    assert args.target == "user-count"
    assert args.write is True
    assert args.commit is True
    assert args.show is True


def test_parse_db_list():
    args = cli.build_parser().parse_args(["db", "list"])
    assert args.group == "db" and args.action == "list"


def test_main_db_value_error_returns_1(monkeypatch, capsys):
    from aws_admin.commands import db as db_cmd
    def boom(target, **kwargs):
        raise ValueError("curated read-only")
    monkeypatch.setattr(db_cmd, "run", boom)
    rc = cli.main(["db", "run", "user-count", "--write"])
    assert rc == 1
    assert "error:" in capsys.readouterr().err


def test_main_db_error_returns_3(monkeypatch, capsys):
    import psycopg
    from aws_admin.commands import db as db_cmd
    def boom(target, **kwargs):
        raise psycopg.OperationalError("connection refused")
    monkeypatch.setattr(db_cmd, "run", boom)
    rc = cli.main(["db", "run", "user-count"])
    assert rc == 3
    assert "DB error" in capsys.readouterr().err
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv_linux/bin/pytest tests/test_cli_db.py -v`
Expected: FAIL — `db` subcommand not defined / `argparse` error.

- [ ] **Step 3: Modify `src/aws_admin/cli.py`**

Add the psycopg import near the top imports:
```python
import psycopg
```
And add `from .commands import db as db_cmd` next to the existing
`from .commands import env as env_cmd`.

In `build_parser()`, after the `env` parser block (before `return parser`), add:
```python
    db_p = groups.add_parser("db", help="PostgreSQL admin")
    db_actions = db_p.add_subparsers(dest="action", required=True)
    db_actions.add_parser("set-password", help="Store the DB password (hidden prompt)")
    db_actions.add_parser("check", help="Connectivity/auth smoke test")
    db_actions.add_parser("list", help="List curated read-only queries")
    db_actions.add_parser("clean-results", help="Delete saved result files")
    run_p = db_actions.add_parser("run", help="Run a curated query name or a .sql file")
    run_p.add_argument("target", help="Curated query name or path to a .sql file")
    run_p.add_argument("--write", action="store_true",
                       help="Allow writes (rolls back unless --commit)")
    run_p.add_argument("--commit", action="store_true",
                       help="Persist a --write run")
    run_p.add_argument("--show", action="store_true",
                       help="Print result rows inline (non-sensitive results only)")
```

In `main()`, add a `db` branch after the `env` branch (it sits inside the same
`try:` block as the `env` dispatch):
```python
        elif args.group == "db":
            if args.action == "set-password":
                db_cmd.set_password()
                print("DB password stored.")
            elif args.action == "check":
                print(db_cmd.check())
            elif args.action == "list":
                print(db_cmd.list_queries())
            elif args.action == "clean-results":
                print(f"Removed {db_cmd.clean_results()} result file(s).")
            elif args.action == "run":
                print(db_cmd.run(args.target, write=args.write,
                                 commit=args.commit, show=args.show))
```

Extend the exception handling. Change the existing
`except (config.UnknownAppError, FileNotFoundError, vault.VaultError) as e:`
to also catch `ValueError`:
```python
    except (config.UnknownAppError, FileNotFoundError, vault.VaultError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except (ClientError, BotoCoreError) as e:
        print(f"AWS error: {e}", file=sys.stderr)
        return 2
    except psycopg.Error as e:
        print(f"DB error: {e}", file=sys.stderr)
        return 3
```

- [ ] **Step 4: Delete the leftover placeholder assert**

No leftover lines to delete (the test above is already clean). Proceed.

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv_linux/bin/pytest tests/test_cli_db.py -v`
Expected: PASS (4 tests).

- [ ] **Step 6: Commit**

```bash
git add src/aws_admin/cli.py tests/test_cli_db.py
git commit -m "feat(db): wire db subcommands into the CLI with psycopg error handling"
```

---

## Task 12: No-value-leak regression test + full suite

**Files:**
- Test: `tests/test_db_no_value_leak.py`

- [ ] **Step 1: Write the test**

`tests/test_db_no_value_leak.py`:
```python
from aws_admin.commands import db
from aws_admin import vault

SECRET_EMAIL = "do-not-leak@example.com"
SECRET_PW = "pw-DO-NOT-LEAK-123"


def test_run_output_never_contains_row_values(monkeypatch, tmp_path, fake_db):
    monkeypatch.setenv("AWS_ADMIN_HOME", str(tmp_path))
    conn = fake_db(description=["id", "email"], rows=[(1, SECRET_EMAIL)], rowcount=1)
    out = db.run("unverified-users", conn=conn)
    assert SECRET_EMAIL not in out  # default summary is key-only


def test_placeholder_value_never_in_output(monkeypatch, tmp_path, fake_db):
    monkeypatch.setenv("AWS_ADMIN_HOME", str(tmp_path))
    sql_file = tmp_path / "q.sql"
    sql_file.write_text("SELECT id FROM users WHERE email = {{EMAIL}}")
    conn = fake_db(description=["id"], rows=[(1,)], rowcount=1)
    out = db.run(str(sql_file), conn=conn,
                 _collect_values=lambda names: {"EMAIL": SECRET_EMAIL})
    assert SECRET_EMAIL not in out


def test_password_never_in_command_output(monkeypatch, tmp_path, fake_db):
    monkeypatch.setenv("AWS_ADMIN_HOME", str(tmp_path))
    db.set_password(_getpass=lambda prompt="": SECRET_PW)
    conn = fake_db(description=["?column?"], rows=[(1,)], rowcount=1)
    out = db.check(conn=conn)
    assert SECRET_PW not in out
    assert SECRET_PW not in db.list_queries()
```

- [ ] **Step 2: Run the test**

Run: `.venv_linux/bin/pytest tests/test_db_no_value_leak.py -v`
Expected: PASS (3 tests). If any FAILS, a value is leaking — fix before continuing.

- [ ] **Step 3: Full suite + confirm nothing sensitive tracked**

Run:
```bash
.venv_linux/bin/pytest -q
git ls-files | grep -iE '\.(enc|key)$' && echo "LEAK!" || echo "clean: no secret files tracked"
```
Expected: all tests PASS; "clean: no secret files tracked".

- [ ] **Step 4: Commit**

```bash
git add tests/test_db_no_value_leak.py
git commit -m "test(db): cross-cutting no-value-leak regression test"
```

---

## Task 13: Slash commands

**Files:**
- Create: `~/.claude/commands/aws-db-list.md`
- Create: `~/.claude/commands/aws-db-run.md`
- Create: `~/.claude/commands/aws-db-set-password.md`
- Create (copies): `slash-commands/aws-db-*.md`

- [ ] **Step 1: Create the list command**

`~/.claude/commands/aws-db-list.md`:
```markdown
---
description: List curated read-only DB queries
allowed-tools: Bash
---

!`/mnt/d/Documents/Code/GitHub/AWS-Admin/.venv_linux/bin/aws-admin db list`

Report the available query names. Do not run any query unless asked.
```

- [ ] **Step 2: Create the run command**

`~/.claude/commands/aws-db-run.md`:
```markdown
---
description: Run a curated DB query or a .sql file (results saved to a file, summary only)
argument-hint: <curated-name | path/to/file.sql>
allowed-tools: Bash
---

Run the DB query/file `$ARGUMENTS` (read-only; results go to a local file, only a
redacted summary is shown):

!`/mnt/d/Documents/Code/GitHub/AWS-Admin/.venv_linux/bin/aws-admin db run $ARGUMENTS`

Report the row count / columns / file path. Do NOT print row values. If the SQL has
`{{NAME}}` placeholders it will open the user's editor — in that case tell the user to
run it themselves with `! aws-admin db run $ARGUMENTS`. For writes, the user must add
`--write` (preview) and then `--write --commit` to persist — never add those yourself
without explicit confirmation.
```

- [ ] **Step 3: Create the set-password command**

`~/.claude/commands/aws-db-set-password.md`:
```markdown
---
description: Store the PostgreSQL password in the encrypted vault (hidden prompt)
allowed-tools: Bash
---

This must run in YOUR terminal so the password is never seen by the model. Tell the
user to run (the `!` prefix runs it in their session):

    ! /mnt/d/Documents/Code/GitHub/AWS-Admin/.venv_linux/bin/aws-admin db set-password

Do NOT run this yourself and never ask the user to type the password into chat.
```

- [ ] **Step 4: Verify and copy into the repo**

Run:
```bash
ls -1 ~/.claude/commands/aws-db-*.md
cp ~/.claude/commands/aws-db-*.md /mnt/d/Documents/Code/GitHub/AWS-Admin/slash-commands/
ls -1 /mnt/d/Documents/Code/GitHub/AWS-Admin/slash-commands/aws-db-*.md
```
Expected: three files listed in each location.

- [ ] **Step 5: Commit**

```bash
cd /mnt/d/Documents/Code/GitHub/AWS-Admin
git add slash-commands/
git commit -m "feat(db): global /aws-db-* slash-command wrappers"
```

---

## Task 14: Docs + final verification

**Files:**
- Modify: `README.md`
- Modify: `docs/usage.md`

- [ ] **Step 1: Add a DB section to `README.md`**

Append to `README.md`:
```markdown

## Database (PostgreSQL)
```bash
aws-admin db set-password        # store the DB password (encrypted; hidden prompt)
aws-admin db check               # connectivity/auth smoke test
aws-admin db list                # curated read-only queries
aws-admin db run unverified-users           # results → file, redacted summary
aws-admin db run user-count --show          # opt into inline output for safe results
aws-admin db run ./my.sql                    # run your own SQL file
aws-admin db run ./change.sql --write        # preview a write (rolls back)
aws-admin db run ./change.sql --write --commit   # persist
aws-admin db clean-results       # delete saved result files
```

- Results write to `~/.config/aws-admin/results/<name>-<ts>.csv` (0600); only a
  key-only summary is printed. Use `--show` for non-sensitive results.
- SQL files may contain `{{NAME}}` placeholders; the tool opens your editor to fill
  them and binds them as query parameters (values never enter the prompt or DB logs).
- Read-only by default; writes need `--write` (preview) then `--write --commit`.
```

- [ ] **Step 2: Add a DB section to `docs/usage.md`**

Append to `docs/usage.md`:
```markdown

## Database admin

### Looking up a user by a sensitive value
1. Write a file `lookup.sql`:
   ```sql
   SELECT id, email, "emailVerified" FROM users WHERE email = {{EMAIL}};
   ```
2. `aws-admin db run ./lookup.sql` — your editor opens; fill `EMAIL=the@address`,
   save, close. The value is bound as a parameter and the buffer is shredded.
3. Read the saved result file (path is printed); or add `--show` only if the result
   is non-sensitive.

### Making a change safely
1. `aws-admin db run ./change.sql --write` — runs in a transaction, prints affected
   row count, then rolls back (a preview).
2. If the count is what you expect: `aws-admin db run ./change.sql --write --commit`.

### Password
`aws-admin db set-password` stores it Fernet-encrypted in `~/.config/aws-admin/`.
The connection uses `sslmode=require`.
```

- [ ] **Step 3: Full suite + live check (optional)**

Run:
```bash
cd /mnt/d/Documents/Code/GitHub/AWS-Admin
.venv_linux/bin/pytest -q
```
Expected: all tests PASS.

Optional live check (requires `aws-admin db set-password` to have been run by the
user first; makes one real read-only connection):
```bash
.venv_linux/bin/aws-admin db check
```
Expected: `connected as postgres@…/your_db (read-only)` — or a one-line
`DB error:` / `error:` if the password isn't set or the network blocks it.

- [ ] **Step 4: Commit**

```bash
git add README.md docs/usage.md
git commit -m "docs(db): document the db subcommand group"
```

---

## Self-Review Notes

- **Spec coverage:** psycopg dep + DB config (Task 1); vault password storage (Task 2);
  reusable shredded edit buffer (Task 3) consumed by placeholder collection (Task 4);
  connection with sslmode=require + vault password (Task 5); fake-DB test double (Task 6);
  runner with read-only/`--write` rollback/`--commit` persist + `--commit`-needs-`--write`
  guard (Task 7); results-to-0600-file + redacted summary + `--show` + clean (Task 8);
  curated read-only registry + seed `.sql` (Task 9); `set-password`/`check`/`list`/`run`/
  `clean-results` incl. curated-`--write` rejection and `{{NAME}}` binding (Task 10); CLI
  + psycopg error handling (Task 11); no-value-leak regression (Task 12); `/aws-db-*`
  slash commands (Task 13); docs + live check (Task 14). All spec sections map to tasks.
- **Type/name consistency:** `runner.Result(columns, rows, rowcount, committed)`;
  `run_sql(sql, params, *, write, commit, conn)`; `placeholders.find_placeholders/
  to_psycopg/render_values_buffer/parse_values_buffer/collect_values`;
  `queries.is_curated/list_curated/load_query`; `results.write_results/summary/
  render_inline/clean_results`; `connection.connect(read_only, _connect)`;
  `db.run(target, *, write, commit, show, conn, _collect_values)`. Used consistently
  across tasks and the CLI.
- **Placeholders:** every code step is complete; the one deliberate leftover assert in
  Task 11's test is explicitly called out and removed in Task 11 Step 4.
- **Reuse:** Task 3 extracts `edit_buffer` so the editor/shred logic has a single
  implementation shared by `env edit` and DB placeholder collection (DRY).
