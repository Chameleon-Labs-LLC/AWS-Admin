from aws_admin import config


def test_db_values():
    assert config.DB_HOST == "db.example.invalid"
    assert config.DB_PORT == 5432
    assert config.DB_NAME == "example_db"
    assert config.DB_USER == "postgres"
    assert config.DB_SSLMODE == "require"


def test_db_paths(monkeypatch, tmp_path):
    monkeypatch.setenv("AWS_ADMIN_HOME", str(tmp_path))
    assert config.db_password_path() == tmp_path / "db-password.enc"
    assert config.results_dir() == tmp_path / "results"


def test_env_override_wins(monkeypatch, tmp_path):
    # A fresh home (so the loader cache key differs) with its own config.
    home = tmp_path / "ovr"
    home.mkdir()
    (home / "config.toml").write_text(
        'account_id = "111111111111"\n[database]\nhost = "h"\nname = "n"\n'
    )
    monkeypatch.setenv("AWS_ADMIN_HOME", str(home))
    monkeypatch.setenv("AWS_ADMIN_DB_HOST", "override.example.invalid")
    assert config.DB_HOST == "override.example.invalid"


def test_missing_config_raises(monkeypatch, tmp_path):
    home = tmp_path / "empty"
    home.mkdir()
    monkeypatch.setenv("AWS_ADMIN_HOME", str(home))
    import pytest
    with pytest.raises(config.ConfigError):
        _ = config.ACCOUNT_ID
