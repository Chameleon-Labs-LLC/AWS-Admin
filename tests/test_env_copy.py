"""`env copy`: move values between two apps' LOCAL snapshots, never showing them."""
import pytest

from aws_admin import aws_client, config, vault
from aws_admin.commands import env


def _seed(name, app_level, branch_level=None):
    vault.save_snapshot(name, {
        "app_id": "x", "branch": "main",
        "app_level": dict(app_level), "branch_level": dict(branch_level or {}),
        "pulled_at": "t",
    })


def _load(name):
    snap = vault.load_snapshot(name)
    assert snap is not None
    return snap


def _backups_for(name):
    d = config.state_dir() / "backups"
    return list(d.glob(f"{name}-*.enc")) if d.exists() else []


@pytest.fixture(autouse=True)
def no_aws(monkeypatch):
    """copy must never build an AWS client."""
    def boom(*a, **k):
        raise AssertionError("env copy must not call AWS")
    monkeypatch.setattr(aws_client, "amplify_client", boom)


# --- happy paths ---------------------------------------------------------------

def test_copy_adds_missing_keys_at_app_level():
    _seed("AppAlpha", {"AI_KEY": "v1", "OTHER": "o"})
    _seed("MyApp2", {"X": "1"})
    out = env.copy(["AI_KEY"], "aa", "my")
    dst = _load("MyApp2")
    assert dst["app_level"] == {"X": "1", "AI_KEY": "v1"}
    assert dst["branch_level"] == {}
    assert "Copied 1 key(s) from AppAlpha to MyApp2" in out
    assert "AI_KEY: added (app-level)" in out
    assert "env push MyApp2" in out


def test_copy_never_modifies_source():
    _seed("AppAlpha", {"AI_KEY": "v1"}, {"BR": "b"})
    before = _load("AppAlpha")
    before_bytes = config.vault_path("AppAlpha").read_bytes()
    _seed("MyApp2", {})
    env.copy(["AI_KEY", "BR"], "aa", "my")
    assert _load("AppAlpha") == before
    assert config.vault_path("AppAlpha").read_bytes() == before_bytes
    assert _backups_for("AppAlpha") == []


def test_copy_multiple_keys():
    _seed("AppAlpha", {"A": "1", "B": "2", "C": "3"})
    _seed("MyApp2", {})
    out = env.copy(["A", "B"], "aa", "my")
    assert _load("MyApp2")["app_level"] == {"A": "1", "B": "2"}
    assert "Copied 2 key(s)" in out


def test_copy_reads_branch_level_value_from_source():
    _seed("AppAlpha", {}, {"AI_KEY": "branch-val"})
    _seed("MyApp2", {})
    env.copy(["AI_KEY"], "aa", "my")
    assert _load("MyApp2")["app_level"]["AI_KEY"] == "branch-val"


def test_copy_same_value_at_both_source_levels_is_fine():
    _seed("AppAlpha", {"AI_KEY": "v"}, {"AI_KEY": "v"})
    _seed("MyApp2", {})
    env.copy(["AI_KEY"], "aa", "my")
    assert _load("MyApp2")["app_level"]["AI_KEY"] == "v"


def test_copy_with_rename():
    _seed("AppAlpha", {"OPENAI_API_KEY": "v1"})
    _seed("MyApp2", {})
    out = env.copy(["OPENAI_API_KEY"], "aa", "my", renames=["OPENAI_API_KEY=AI_OPENAI_KEY"])
    dst = _load("MyApp2")["app_level"]
    assert dst == {"AI_OPENAI_KEY": "v1"}
    assert "OPENAI_API_KEY -> AI_OPENAI_KEY: added (app-level)" in out


def test_copy_accepts_names_ids_and_aliases_case_insensitively():
    _seed("AppAlpha", {"K": "v"})
    _seed("MyApp2", {})
    env.copy(["K"], "AppAlpha", "D0000000000MY0")
    assert _load("MyApp2")["app_level"]["K"] == "v"


def test_copy_dedupes_repeated_keys():
    _seed("AppAlpha", {"K": "v"})
    _seed("MyApp2", {})
    out = env.copy(["K", "K"], "aa", "my")
    assert "Copied 1 key(s)" in out


def test_copy_backs_up_destination_once():
    _seed("AppAlpha", {"A": "1", "B": "2"})
    _seed("MyApp2", {"X": "1"})
    env.copy(["A", "B"], "aa", "my")
    backups = _backups_for("MyApp2")
    assert len(backups) == 1
    assert vault.decrypt(backups[0].read_bytes())["app_level"] == {"X": "1"}


def test_copy_same_value_is_unchanged_without_overwrite_and_no_backup():
    _seed("AppAlpha", {"K": "same"})
    _seed("MyApp2", {"K": "same"})
    out = env.copy(["K"], "aa", "my")
    assert "Unchanged (same value): K" in out
    assert "Copied 0 key(s)" in out
    assert _backups_for("MyApp2") == []


# --- overwrite -----------------------------------------------------------------

def test_copy_refuses_different_existing_value_without_overwrite():
    _seed("AppAlpha", {"K": "new", "N": "n"})
    _seed("MyApp2", {"K": "old"})
    before = config.vault_path("MyApp2").read_bytes()
    with pytest.raises(ValueError) as ei:
        env.copy(["K", "N"], "aa", "my")
    msg = str(ei.value)
    assert "already has a different value for: K" in msg
    assert "--overwrite" in msg
    # Refusal is all-or-nothing: the new key N was not written either.
    assert config.vault_path("MyApp2").read_bytes() == before
    assert _backups_for("MyApp2") == []


def test_copy_overwrite_replaces_at_every_existing_level():
    _seed("AppAlpha", {"K": "new"})
    _seed("MyApp2", {"K": "old"}, {"K": "oldbr"})
    out = env.copy(["K"], "aa", "my", overwrite=True)
    dst = _load("MyApp2")
    assert dst["app_level"]["K"] == "new"
    assert dst["branch_level"]["K"] == "new"
    assert "K: replaced (app-level + branch-level)" in out


def test_copy_overwrite_only_branch_level_where_key_lives():
    _seed("AppAlpha", {"K": "new"})
    _seed("MyApp2", {}, {"K": "old"})
    env.copy(["K"], "aa", "my", overwrite=True)
    dst = _load("MyApp2")
    assert "K" not in dst["app_level"]
    assert dst["branch_level"]["K"] == "new"


def test_copy_partial_level_mismatch_counts_as_conflict():
    _seed("AppAlpha", {"K": "new"})
    _seed("MyApp2", {"K": "new"}, {"K": "old"})
    with pytest.raises(ValueError, match="different value for: K"):
        env.copy(["K"], "aa", "my")


# --- refusals ------------------------------------------------------------------

def test_copy_refuses_same_app():
    _seed("MyApp2", {"K": "v"})
    with pytest.raises(ValueError, match="same app"):
        env.copy(["K"], "my", "MyApp2")


@pytest.mark.parametrize("src,dst", [("all", "my"), ("aa", "all"), ("ALL", "all")])
def test_copy_refuses_all_token(src, dst):
    with pytest.raises(ValueError, match="'all' is not supported"):
        env.copy(["K"], src, dst)


def test_copy_refuses_unknown_app():
    with pytest.raises(config.UnknownAppError):
        env.copy(["K"], "nope", "my")


def test_copy_refuses_missing_key_in_source_and_writes_nothing():
    _seed("AppAlpha", {"A": "1"})
    _seed("MyApp2", {})
    before = config.vault_path("MyApp2").read_bytes()
    with pytest.raises(ValueError) as ei:
        env.copy(["A", "MISSING1", "MISSING2"], "aa", "my")
    assert "MISSING1, MISSING2" in str(ei.value)
    assert config.vault_path("MyApp2").read_bytes() == before
    assert _backups_for("MyApp2") == []


@pytest.mark.parametrize("missing", ["AppAlpha", "MyApp2"])
def test_copy_refuses_missing_snapshot(missing):
    other = "MyApp2" if missing == "AppAlpha" else "AppAlpha"
    _seed(other, {"K": "v"})
    with pytest.raises(FileNotFoundError) as ei:
        env.copy(["K"], "aa", "my")
    assert missing in str(ei.value)
    assert "env pull" in str(ei.value)


def test_copy_refuses_conflicting_source_levels():
    _seed("AppAlpha", {"K": "a"}, {"K": "b"})
    _seed("MyApp2", {})
    with pytest.raises(ValueError, match="different app-level and branch-level"):
        env.copy(["K"], "aa", "my")
    assert "K" not in _load("MyApp2")["app_level"]


@pytest.mark.parametrize("blank", ["", "   "])
def test_copy_refuses_blank_source_value(blank):
    _seed("AppAlpha", {"K": blank})
    _seed("MyApp2", {})
    with pytest.raises(ValueError, match="no usable value"):
        env.copy(["K"], "aa", "my")
    assert _backups_for("MyApp2") == []


@pytest.mark.parametrize("renames,fragment", [
    (["K"], "expects SRC=DST"),
    (["K=A=B"], "expects SRC=DST"),
    (["=A"], "expects SRC=DST"),
    (["K="], "expects SRC=DST"),
    (["OTHER=A"], "not one of the keys"),
    (["K=A", "K=B"], "given twice"),
    (["K=1BAD"], "not a valid env-var name"),
    (["K=has-dash"], "not a valid env-var name"),
    (["K=J"], "would both be copied to J"),
])
def test_copy_rejects_bad_renames(renames, fragment):
    _seed("AppAlpha", {"K": "1", "J": "2"})
    _seed("MyApp2", {})
    with pytest.raises(ValueError, match=fragment):
        env.copy(["K", "J"], "aa", "my", renames=renames)
    assert _load("MyApp2")["app_level"] == {}


def test_copy_rename_swap_is_allowed():
    _seed("AppAlpha", {"K": "1", "J": "2"})
    _seed("MyApp2", {})
    env.copy(["K", "J"], "aa", "my", renames=["K=J", "J=K"])
    assert _load("MyApp2")["app_level"] == {"J": "1", "K": "2"}
