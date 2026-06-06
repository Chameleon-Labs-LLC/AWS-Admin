import pytest
from aws_admin import vault, config


def test_set_then_get_db_password(monkeypatch, tmp_path):
    monkeypatch.setenv("AWS_ADMIN_HOME", str(tmp_path))
    vault.set_db_password("s3cr3t-pw")
    assert vault.get_db_password() == "s3cr3t-pw"
    assert b"s3cr3t-pw" not in config.db_password_path().read_bytes()


def test_get_missing_db_password_raises(monkeypatch, tmp_path):
    monkeypatch.setenv("AWS_ADMIN_HOME", str(tmp_path))
    with pytest.raises(FileNotFoundError) as exc:
        vault.get_db_password()
    assert "set-password" in str(exc.value)
