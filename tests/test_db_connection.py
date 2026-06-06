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
