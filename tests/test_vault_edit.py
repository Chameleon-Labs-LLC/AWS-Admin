from aws_admin import vault


def test_parse_buffer_round_trips_sections():
    text = (
        "# ===== APP-LEVEL =====\n"
        "FOO=bar\n"
        "STRIPE=sk_live_xyz\n"
        "# ===== BRANCH-LEVEL =====\n"
        "BRANCH_ONLY=1\n"
    )
    app_level, branch_level = vault.parse_buffer(text)
    assert app_level == {"FOO": "bar", "STRIPE": "sk_live_xyz"}
    assert branch_level == {"BRANCH_ONLY": "1"}


def test_parse_buffer_value_with_equals():
    text = "# ===== APP-LEVEL =====\nURL=postgres://u:p@h/db?x=1\n# ===== BRANCH-LEVEL =====\n"
    app_level, branch_level = vault.parse_buffer(text)
    assert app_level == {"URL": "postgres://u:p@h/db?x=1"}
    assert branch_level == {}


def test_render_buffer_has_both_sections():
    text = vault.render_buffer({"A": "1"}, {"B": "2"})
    assert "# ===== APP-LEVEL =====" in text
    assert "# ===== BRANCH-LEVEL =====" in text
    assert "A=1" in text
    assert "B=2" in text


def test_edit_app_buffer_shreds_temp(monkeypatch, tmp_path):
    monkeypatch.setenv("AWS_ADMIN_HOME", str(tmp_path))
    snap = {"app_id": "x", "branch": "main",
            "app_level": {"A": "1"}, "branch_level": {}, "pulled_at": "t"}
    vault.save_snapshot("MyApp2", snap)

    captured = {}

    def fake_editor(path):
        captured["path"] = path
        # Simulate the user changing A and adding NEW.
        path.write_text(vault.render_buffer({"A": "2", "NEW": "x"}, {}))

    changed = vault.edit_app_buffer("MyApp2", _open_editor=fake_editor)
    assert changed is True
    updated = vault.load_snapshot("MyApp2")
    assert updated is not None
    assert updated["app_level"] == {"A": "2", "NEW": "x"}
    # Temp buffer no longer exists (shredded + unlinked).
    assert not captured["path"].exists()


def test_edit_app_buffer_shreds_even_when_editor_raises(monkeypatch, tmp_path):
    monkeypatch.setenv("AWS_ADMIN_HOME", str(tmp_path))
    snap = {"app_id": "x", "branch": "main",
            "app_level": {"A": "1"}, "branch_level": {}, "pulled_at": "t"}
    vault.save_snapshot("MyApp2", snap)
    captured = {}

    def boom(path):
        captured["path"] = path
        raise RuntimeError("editor crashed")

    import pytest
    with pytest.raises(RuntimeError):
        vault.edit_app_buffer("MyApp2", _open_editor=boom)
    assert not captured["path"].exists()  # shredded despite the crash


def test_parse_buffer_strips_crlf_value():
    text = "# ===== APP-LEVEL =====\r\nKEY=val\r\n# ===== BRANCH-LEVEL =====\r\n"
    app_level, branch_level = vault.parse_buffer(text)
    assert app_level == {"KEY": "val"}
