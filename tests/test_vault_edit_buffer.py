from pathlib import Path
from aws_admin import vault


def test_edit_buffer_round_trip_and_shred(monkeypatch, tmp_path):
    monkeypatch.setenv("AWS_ADMIN_HOME", str(tmp_path))
    captured = {}

    def fake_editor(path):
        captured["path"] = path
        assert path.read_text() == "SEED\n"
        path.write_text("EDITED\n")

    out = vault.edit_buffer("SEED\n", _open_editor=fake_editor)
    assert out == "EDITED\n"
    assert not captured["path"].exists()  # shredded


def test_edit_buffer_shreds_on_editor_exception(monkeypatch, tmp_path):
    monkeypatch.setenv("AWS_ADMIN_HOME", str(tmp_path))
    captured = {}

    def boom(path):
        captured["path"] = path
        raise RuntimeError("crash")

    import pytest
    with pytest.raises(RuntimeError):
        vault.edit_buffer("X", _open_editor=boom)
    assert not captured["path"].exists()
