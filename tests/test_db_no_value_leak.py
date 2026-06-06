from aws_admin.commands import db

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
