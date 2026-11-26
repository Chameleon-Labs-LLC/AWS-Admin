"""Snapshot lock: a second read-change-write on the same app is refused, and
writes are atomic (temp file + rename).

Astra (council review of env copy): two overlapping copies could both pass
the overwrite check, and the second would silently undo the first.
"""
import pytest

from aws_admin import config, vault
from aws_admin.commands import env


def _seed(name, app_level):
    vault.save_snapshot(name, {"app_id": "x", "branch": "main", "app_level": dict(app_level),
                               "branch_level": {}, "pulled_at": "t"})


def test_second_lock_on_the_same_app_is_refused():
    with vault.snapshot_lock("MyApp2"):
        with pytest.raises(vault.SnapshotBusy) as exc:
            with vault.snapshot_lock("MyApp2"):
                pass
        assert "MyApp2" in str(exc.value) and "No changes made" in str(exc.value)
    with vault.snapshot_lock("MyApp2"):  # released on exit
        pass


def test_locks_on_different_apps_do_not_conflict():
    with vault.snapshot_lock("MyApp2"):
        with vault.snapshot_lock("AppAlpha"):
            pass


def test_copy_while_the_destination_is_locked_writes_nothing():
    _seed("AppAlpha", {"AI_KEY": "v1"})
    _seed("MyApp2", {"OTHER": "keep"})
    with vault.snapshot_lock("MyApp2"):  # an edit or another copy in progress
        with pytest.raises(vault.SnapshotBusy):
            env.copy(["AI_KEY"], "aa", "my")
    assert vault.load_snapshot("MyApp2")["app_level"] == {"OTHER": "keep"}


def test_rotate_while_locked_is_refused_before_any_prompt():
    _seed("MyApp2", {"K": "old"})
    asked = []
    with vault.snapshot_lock("MyApp2"):
        with pytest.raises(vault.SnapshotBusy):
            env.rotate("K", ["my"], confirm=lambda *_: asked.append(1), open_editor=lambda p: asked.append(2))
    assert asked == [] and vault.load_snapshot("MyApp2")["app_level"] == {"K": "old"}


def test_write_is_atomic_and_private():
    _seed("MyApp2", {"A": "1"})
    path = config.vault_path("MyApp2")
    assert (path.stat().st_mode & 0o777) == 0o600
    assert not list(path.parent.glob(".*.tmp")), "no temp file left behind"
    _seed("MyApp2", {"A": "2"})
    assert vault.load_snapshot("MyApp2")["app_level"] == {"A": "2"}
