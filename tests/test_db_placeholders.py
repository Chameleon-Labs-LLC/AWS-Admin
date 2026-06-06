import pytest
from aws_admin.db import placeholders


def test_find_placeholders_distinct_in_order():
    sql = "SELECT * FROM t WHERE a = {{EMAIL}} AND b = {{TOKEN}} OR c = {{EMAIL}}"
    assert placeholders.find_placeholders(sql) == ["EMAIL", "TOKEN"]


def test_find_placeholders_none():
    assert placeholders.find_placeholders("SELECT 1") == []


def test_to_psycopg_translates_tokens():
    sql = "WHERE a = {{EMAIL}} AND b = {{TOKEN}}"
    assert placeholders.to_psycopg(sql) == "WHERE a = %(EMAIL)s AND b = %(TOKEN)s"


def test_to_psycopg_leaves_casts_untouched():
    sql = "SELECT id::text FROM t WHERE x = {{V}}"
    assert placeholders.to_psycopg(sql) == "SELECT id::text FROM t WHERE x = %(V)s"


def test_render_and_parse_values_buffer_round_trip():
    text = placeholders.render_values_buffer(["EMAIL", "TOKEN"])
    assert "EMAIL=" in text and "TOKEN=" in text
    filled = text.replace("EMAIL=", "EMAIL=a@b.com").replace("TOKEN=", "TOKEN=xyz")
    assert placeholders.parse_values_buffer(filled) == {"EMAIL": "a@b.com", "TOKEN": "xyz"}


def test_collect_values_binds_filled():
    def fake_editor(path):
        path.write_text(placeholders.render_values_buffer(["TOKEN"]).replace("TOKEN=", "TOKEN=secret"))
    vals = placeholders.collect_values(["TOKEN"], _open_editor=fake_editor)
    assert vals == {"TOKEN": "secret"}


def test_collect_values_unfilled_raises():
    def fake_editor(path):
        pass  # leaves TOKEN= blank
    with pytest.raises(ValueError) as exc:
        placeholders.collect_values(["TOKEN"], _open_editor=fake_editor)
    assert "TOKEN" in str(exc.value)
