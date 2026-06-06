import pytest
from aws_admin.db import queries


def test_list_curated_has_seed_queries():
    names = dict(queries.list_curated())
    assert "unverified-users" in names
    assert "verification-tokens" in names
    assert "user-count" in names
    assert all(desc for desc in names.values())


def test_is_curated():
    assert queries.is_curated("user-count") is True
    assert queries.is_curated("./some/file.sql") is False


def test_load_query_returns_sql_text():
    sql = queries.load_query("user-count")
    assert "count(*)" in sql.lower()


def test_load_unknown_raises():
    with pytest.raises(KeyError):
        queries.load_query("does-not-exist")
