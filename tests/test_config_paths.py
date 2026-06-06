from pathlib import Path
from aws_admin import config


def test_state_dir_uses_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("AWS_ADMIN_HOME", str(tmp_path))
    assert config.state_dir() == tmp_path


def test_state_dir_default(monkeypatch):
    monkeypatch.delenv("AWS_ADMIN_HOME", raising=False)
    assert config.state_dir() == Path.home() / ".config" / "aws-admin"


def test_path_helpers(monkeypatch, tmp_path):
    monkeypatch.setenv("AWS_ADMIN_HOME", str(tmp_path))
    assert config.key_path() == tmp_path / "vault.key"
    assert config.vault_path("MyApp2") == tmp_path / "vaults" / "MyApp2.enc"
    assert config.backup_path("MyApp2", "20260530-084800") == (
        tmp_path / "backups" / "MyApp2-20260530-084800.enc"
    )


def test_plain_constants():
    assert config.REGION == "us-east-1"
    assert config.DEFAULT_BRANCH == "main"


def test_account_id_comes_from_config():
    assert config.ACCOUNT_ID == "000000000000"
