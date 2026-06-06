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
    assert "a@b.com" in content


def test_summary_is_key_and_count_only():
    out = results.summary("q", ["id", "email"], [(1, "secret@x.com")], "/p/q.csv")
    assert "1 row" in out
    assert "columns: [id, email]" in out
    assert "/p/q.csv" in out
    assert "secret@x.com" not in out


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


def test_render_inline_quotes_value_with_comma():
    out = results.render_inline(["id", "name"], [(1, "Smith, Jr.")])
    assert '"Smith, Jr."' in out  # properly CSV-quoted, not split


def test_write_results_guards_formula_injection(monkeypatch, tmp_path):
    monkeypatch.setenv("AWS_ADMIN_HOME", str(tmp_path))
    path = results.write_results("q", ["email"], [("=cmd|calc",)])
    assert "'=cmd|calc" in path.read_text()


def test_sanitize_leaves_negative_numbers_intact(monkeypatch, tmp_path):
    monkeypatch.setenv("AWS_ADMIN_HOME", str(tmp_path))
    path = results.write_results("q", ["n"], [(-5,)])
    content = path.read_text()
    assert "-5" in content and "'-5" not in content
