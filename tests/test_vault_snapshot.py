from aws_admin import vault, config


def test_load_missing_returns_none(monkeypatch, tmp_path):
    monkeypatch.setenv("AWS_ADMIN_HOME", str(tmp_path))
    assert vault.load_snapshot("MyApp2") is None


def test_save_then_load(monkeypatch, tmp_path):
    monkeypatch.setenv("AWS_ADMIN_HOME", str(tmp_path))
    snap = {"app_id": "d0000000000my0", "branch": "main",
            "app_level": {"K": "v"}, "branch_level": {}, "pulled_at": "t"}
    vault.save_snapshot("MyApp2", snap)
    assert vault.load_snapshot("MyApp2") == snap
    # File on disk is ciphertext.
    assert b"app_id" not in config.vault_path("MyApp2").read_bytes()


def test_backup_writes_timestamped_file(monkeypatch, tmp_path):
    monkeypatch.setenv("AWS_ADMIN_HOME", str(tmp_path))
    snap = {"app_id": "x", "app_level": {"K": "v"}, "branch_level": {}}
    path = vault.backup_snapshot("MyApp2", snap)
    assert path.exists()
    assert path.parent == tmp_path / "backups"
    assert path.name.startswith("MyApp2-")
    assert path.name.endswith(".enc")
