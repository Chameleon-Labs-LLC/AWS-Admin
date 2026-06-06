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
    assert "a@b.com" not in out


def test_run_curated_show_includes_values(monkeypatch, tmp_path, fake_db):
    monkeypatch.setenv("AWS_ADMIN_HOME", str(tmp_path))
    conn = fake_db(description=["users"], rows=[(42,)], rowcount=1)
    out = db.run("user-count", show=True, conn=conn)
    assert "42" in out


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
    assert "secret@x.com" not in out


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
