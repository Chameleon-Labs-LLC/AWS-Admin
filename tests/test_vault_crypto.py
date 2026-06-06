import os
import stat
from aws_admin import vault, config


def test_ensure_key_creates_0600_file(monkeypatch, tmp_path):
    monkeypatch.setenv("AWS_ADMIN_HOME", str(tmp_path))
    key = vault.ensure_key()
    assert isinstance(key, bytes) and len(key) > 0
    mode = stat.S_IMODE(os.stat(config.key_path()).st_mode)
    assert mode == 0o600


def test_ensure_key_is_stable(monkeypatch, tmp_path):
    monkeypatch.setenv("AWS_ADMIN_HOME", str(tmp_path))
    assert vault.ensure_key() == vault.ensure_key()


def test_encrypt_decrypt_round_trip(monkeypatch, tmp_path):
    monkeypatch.setenv("AWS_ADMIN_HOME", str(tmp_path))
    data = {"app_id": "x", "app_level": {"K": "v"}}
    token = vault.encrypt(data)
    assert b"app_id" not in token  # ciphertext, not plaintext
    assert vault.decrypt(token) == data


def test_decrypt_corrupt_raises_vaulterror(monkeypatch, tmp_path):
    monkeypatch.setenv("AWS_ADMIN_HOME", str(tmp_path))
    import pytest
    with pytest.raises(vault.VaultError):
        vault.decrypt(b"not-a-valid-fernet-token")
