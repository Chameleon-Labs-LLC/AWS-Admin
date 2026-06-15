import pytest

from aws_admin import config, vault
from aws_admin.commands import env


def _seed(name, app_level, branch_level=None):
    vault.save_snapshot(name, {
        "app_id": "x", "branch": "main",
        "app_level": dict(app_level), "branch_level": dict(branch_level or {}),
        "pulled_at": "t",
    })


def _types(value):
    """An injectable editor that types `value` as the new secret."""
    def editor(path):
        path.write_text(f"AI_SECRET={value}\n")
    return editor


def _backups_for(name):
    d = config.state_dir() / "backups"
    return list(d.glob(f"{name}-*.enc")) if d.exists() else []


def _load(name):
    """Load a snapshot, asserting it exists (keeps the type-checker happy)."""
    snap = vault.load_snapshot(name)
    assert snap is not None
    return snap


YES = lambda prompt: True
NO = lambda prompt: False


def test_rotate_updates_existing_app_level_key():
    _seed("MyApp2", {"AI_SECRET": "old", "X": "1"})
    out = env.rotate("AI_SECRET", ["my"], confirm=NO, open_editor=_types("new"))
    snap = _load("MyApp2")
    assert snap["app_level"]["AI_SECRET"] == "new"
    assert snap["app_level"]["X"] == "1"  # untouched
    assert "Rotated AI_SECRET in 1 app(s)" in out
    assert "MyApp2: app-level updated" in out


def test_rotate_updates_both_levels():
    _seed("MyApp2", {"AI_SECRET": "old"}, {"AI_SECRET": "oldbr"})
    out = env.rotate("AI_SECRET", ["my"], confirm=NO, open_editor=_types("new"))
    snap = _load("MyApp2")
    assert snap["app_level"]["AI_SECRET"] == "new"
    assert snap["branch_level"]["AI_SECRET"] == "new"
    assert "app-level + branch-level updated" in out


def test_rotate_aborts_when_any_snapshot_missing():
    _seed("MyApp2", {"AI_SECRET": "old"})  # AppAlpha has none
    with pytest.raises(FileNotFoundError) as ei:
        env.rotate("AI_SECRET", ["my", "aa"], confirm=YES, open_editor=_types("new"))
    assert "AppAlpha" in str(ei.value)
    assert _load("MyApp2")["app_level"]["AI_SECRET"] == "old"  # untouched
    assert _backups_for("MyApp2") == []  # nothing written


def test_rotate_declines_add_leaves_app_untouched():
    _seed("MyApp2", {"AI_SECRET": "old"})
    _seed("AppAlpha", {"OTHER": "1"})  # missing AI_SECRET
    out = env.rotate("AI_SECRET", ["my", "aa"], confirm=NO, open_editor=_types("new"))
    assert "AI_SECRET" not in _load("AppAlpha")["app_level"]
    assert "Skipped (declined add): AppAlpha" in out


def test_rotate_accepts_add_creates_app_level_key():
    _seed("MyApp2", {"AI_SECRET": "old"})
    _seed("AppAlpha", {"OTHER": "1"})
    out = env.rotate("AI_SECRET", ["my", "aa"], confirm=YES, open_editor=_types("new"))
    assert _load("AppAlpha")["app_level"]["AI_SECRET"] == "new"
    assert "Added (was missing): AppAlpha (app-level)" in out


def test_rotate_empty_value_aborts_no_writes():
    _seed("MyApp2", {"AI_SECRET": "old"})
    out = env.rotate("AI_SECRET", ["my"], confirm=NO, open_editor=_types(""))
    assert _load("MyApp2")["app_level"]["AI_SECRET"] == "old"
    assert _backups_for("MyApp2") == []
    assert "aborted" in out.lower()


def test_rotate_whitespace_only_value_aborts_no_writes():
    _seed("MyApp2", {"AI_SECRET": "old"})
    out = env.rotate("AI_SECRET", ["my"], confirm=NO, open_editor=_types("   "))
    assert _load("MyApp2")["app_level"]["AI_SECRET"] == "old"  # not rotated to blanks
    assert _backups_for("MyApp2") == []
    assert "aborted" in out.lower()


def test_rotate_unchanged_when_same_value_no_backup():
    _seed("MyApp2", {"AI_SECRET": "same"})
    out = env.rotate("AI_SECRET", ["my"], confirm=NO, open_editor=_types("same"))
    assert "Unchanged (same value): MyApp2" in out
    assert _backups_for("MyApp2") == []  # no change => no backup


def test_rotate_backs_up_each_changed_app():
    _seed("MyApp2", {"AI_SECRET": "old"})
    env.rotate("AI_SECRET", ["my"], confirm=NO, open_editor=_types("new"))
    assert len(_backups_for("MyApp2")) == 1


def test_rotate_nothing_to_do_when_all_declined():
    _seed("MyApp2", {"OTHER": "1"})  # missing AI_SECRET, user declines add
    called = {"editor": False}

    def editor(path):
        called["editor"] = True

    out = env.rotate("AI_SECRET", ["my"], confirm=NO, open_editor=editor)
    assert called["editor"] is False  # never prompted for a value
    assert "nothing to rotate" in out.lower()
