"""Encrypted local store for Amplify env-var snapshots.

Fernet (AES-128-CBC + HMAC). The key lives at config.key_path() with mode 0600.
Decryption is in-memory only; the sole plaintext that touches disk is the
transient edit buffer (see edit_app_buffer), which is shredded.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from . import config


class VaultError(RuntimeError):
    """Raised when the vault cannot be read (corrupt data or wrong key)."""


def _write_private(path: Path, data: bytes) -> None:
    """Write bytes to `path` created with mode 0600 from the start (no world-readable window)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    path.chmod(0o600)  # also fix perms if the file pre-existed with looser mode


def ensure_key() -> bytes:
    """Return the Fernet key, generating a 0600 key file on first use."""
    path = config.key_path()
    if not path.exists():
        _write_private(path, Fernet.generate_key())
    return path.read_bytes()


def _fernet() -> Fernet:
    return Fernet(ensure_key())


def encrypt(data: dict) -> bytes:
    return _fernet().encrypt(json.dumps(data).encode("utf-8"))


def decrypt(token: bytes) -> dict:
    try:
        return json.loads(_fernet().decrypt(token).decode("utf-8"))
    except InvalidToken as exc:
        raise VaultError(
            "Vault snapshot is corrupt or the encryption key changed."
        ) from exc


def load_snapshot(app_name: str) -> dict | None:
    path = config.vault_path(app_name)
    if not path.exists():
        return None
    return decrypt(path.read_bytes())


def save_snapshot(app_name: str, data: dict) -> None:
    path = config.vault_path(app_name)
    _write_private(path, encrypt(data))


def backup_snapshot(app_name: str, data: dict) -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    path = config.backup_path(app_name, ts)
    _write_private(path, encrypt(data))
    return path


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
    for line in text.splitlines():
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
        section[key.strip()] = value.rstrip("\r")
    return app_level, branch_level


def _default_open_editor(path: Path) -> None:
    editor = os.environ.get("EDITOR", "nano")
    subprocess.run([editor, str(path)], check=True)


def _malformed_line_numbers(text: str) -> list[int]:
    """1-based line numbers of in-section lines lacking '=' (content NOT returned, to avoid leaks)."""
    section_started = False
    bad: list[int] = []
    for i, line in enumerate(text.splitlines(), start=1):
        s = line.strip()
        if s in (_APP_HEADER, _BRANCH_HEADER):
            section_started = True
            continue
        if not s or s.startswith("#") or not section_started:
            continue
        if "=" not in line:
            bad.append(i)
    return bad


def _shred(path: Path) -> None:
    """Best-effort overwrite then unlink of a plaintext temp file.

    The overwrite is defense-in-depth and is NOT guaranteed on COW/journaling/SSD/tmpfs
    filesystems, which is why /dev/shm (never hits disk) is preferred for the buffer.
    """
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


def _parse_single_value(text: str, key: str) -> str:
    """Return the value on the first non-comment ``key=...`` line, else "".

    Splits on the first '=' (so values may contain '='); strips a trailing CR
    only, matching parse_buffer's value handling. Comment/blank lines are skipped.
    """
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if "=" not in line:
            continue
        k, value = line.split("=", 1)
        if k.strip() == key:
            return value.rstrip("\r")
    return ""


def capture_value(key: str, target_names: list[str], _open_editor=_default_open_editor) -> str:
    """Capture a single new value for `key` via the RAM-backed shred-after editor.

    The buffer is blank (no old value is shown). Returns the value typed after
    'key=' , or "" if left empty. The temp buffer is shredded by edit_buffer.
    """
    template = (
        f"# Enter the new value for {key} after '='. Save & exit.\n"
        f"# Writes to local snapshots only: {', '.join(target_names)}\n"
        f"# Empty value = abort, no changes.\n"
        f"{key}=\n"
    )
    text = edit_buffer(template, _open_editor=_open_editor)
    return _parse_single_value(text, key)


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
