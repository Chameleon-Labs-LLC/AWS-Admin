"""`env copy` through the real CLI entry point: no value, length or hash ever printed."""
import hashlib

import pytest

from aws_admin import cli, config, vault

# Distinctive values with unusual lengths, so a leaked length is detectable too.
SECRET = "sk-ant-DO_NOT_LEAK_c0ffee_5ecret_value_for_tests_only_zz"  # 55 chars
OTHER = "sk-proj-ALSO_SECRET_9f8e7d6c5b4a_value_qq"                     # 40 chars
OLD = "old-destination-secret-DO_NOT_LEAK_77"                           # 37 chars


def _seed(name, app_level, branch_level=None):
    vault.save_snapshot(name, {
        "app_id": "x", "branch": "main",
        "app_level": dict(app_level), "branch_level": dict(branch_level or {}),
        "pulled_at": "t",
    })


def _forbidden():
    out = []
    for v in (SECRET, OTHER, OLD):
        out.append(v)
        out.append(v[:8])       # a prefix
        out.append(v[-8:])      # a suffix
        out.append(str(len(v)))  # its length
        for algo in (hashlib.sha256, hashlib.sha1, hashlib.md5):
            digest = algo(v.encode()).hexdigest()
            out.append(digest[:8])
    return out


def _assert_clean(text):
    for needle in _forbidden():
        assert needle not in text, f"leaked fragment {needle!r}"


def _run(capsys, argv):
    rc = cli.main(argv)
    cap = capsys.readouterr()
    return rc, cap.out + cap.err


@pytest.fixture
def seeded():
    _seed("AppAlpha", {"ANTHROPIC_API_KEY": SECRET, "OPENAI_API_KEY": OTHER})
    _seed("MyApp2", {"OPENAI_API_KEY": OLD})


def test_copy_success_output_has_no_value(capsys, seeded):
    rc, text = _run(capsys, ["env", "copy", "ANTHROPIC_API_KEY", "--from", "aa", "--to", "my"])
    assert rc == 0
    assert "ANTHROPIC_API_KEY: added" in text
    _assert_clean(text)


def test_copy_overwrite_refusal_output_has_no_value(capsys, seeded):
    rc, text = _run(capsys, ["env", "copy", "OPENAI_API_KEY", "--from", "aa", "--to", "my"])
    assert rc == 1
    assert "OPENAI_API_KEY" in text and "--overwrite" in text
    _assert_clean(text)


def test_copy_overwrite_success_output_has_no_value(capsys, seeded):
    rc, text = _run(capsys, ["env", "copy", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
                             "--from", "aa", "--to", "my", "--overwrite",
                             "--rename", "ANTHROPIC_API_KEY=AI_ANTHROPIC_KEY"])
    assert rc == 0
    assert "OPENAI_API_KEY: replaced (app-level)" in text
    assert "ANTHROPIC_API_KEY -> AI_ANTHROPIC_KEY: added (app-level)" in text
    _assert_clean(text)
    dst = vault.load_snapshot("MyApp2")
    assert dst is not None
    assert dst["app_level"] == {"OPENAI_API_KEY": OTHER, "AI_ANTHROPIC_KEY": SECRET}


def test_copy_unchanged_output_has_no_value(capsys, seeded):
    _run(capsys, ["env", "copy", "ANTHROPIC_API_KEY", "--from", "aa", "--to", "my"])
    rc, text = _run(capsys, ["env", "copy", "ANTHROPIC_API_KEY", "--from", "aa", "--to", "my"])
    assert rc == 0
    assert "Unchanged (same value): ANTHROPIC_API_KEY" in text
    _assert_clean(text)


@pytest.mark.parametrize("argv", [
    ["env", "copy", "NOPE", "--from", "aa", "--to", "my"],
    ["env", "copy", "ANTHROPIC_API_KEY", "--from", "aa", "--to", "aa"],
    ["env", "copy", "ANTHROPIC_API_KEY", "--from", "aa", "--to", "ag"],  # never pulled
    ["env", "copy", "ANTHROPIC_API_KEY", "--from", "aa", "--to", "my",
     "--rename", "ANTHROPIC_API_KEY=bad-name"],
])
def test_copy_error_output_has_no_value(capsys, seeded, argv):
    rc, text = _run(capsys, argv)
    assert rc == 1
    assert text.startswith("error: ")
    _assert_clean(text)


def test_copy_conflicting_source_levels_output_has_no_value(capsys):
    _seed("AppAlpha", {"K": SECRET}, {"K": OTHER})
    _seed("MyApp2", {})
    rc, text = _run(capsys, ["env", "copy", "K", "--from", "aa", "--to", "my"])
    assert rc == 1
    _assert_clean(text)


def test_copied_value_never_lands_as_plaintext(capsys, seeded):
    _run(capsys, ["env", "copy", "ANTHROPIC_API_KEY", "--from", "aa", "--to", "my"])
    blob = config.vault_path("MyApp2").read_bytes()
    assert SECRET.encode() not in blob
    for backup in (config.state_dir() / "backups").glob("*.enc"):
        assert OLD.encode() not in backup.read_bytes()
