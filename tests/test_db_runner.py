import pytest
from aws_admin.db import runner


def test_read_query_returns_rows_and_rolls_back(fake_db):
    conn = fake_db(description=["id", "email"], rows=[(1, "a@b.com")], rowcount=1)
    result = runner.run_sql("SELECT id, email FROM users", {}, conn=conn)
    assert result.columns == ["id", "email"]
    assert result.rows == [(1, "a@b.com")]
    assert result.committed is False
    assert "rollback" in conn.calls and "commit" not in conn.calls
    assert "close" not in conn.calls


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
