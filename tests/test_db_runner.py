import pytest
from aws_admin.db import runner


def test_read_query_returns_rows_and_rolls_back(fake_db):
    conn = fake_db(description=["id", "email"], rows=[(1, "a@b.com")], rowcount=1)
    result = runner.run_sql("SELECT id, email FROM users", {}, conn=conn)
    assert result.columns == ["id", "email"]
    assert result.rows == [(1, "a@b.com")]
    assert result.committed is False
    assert "rollback" in conn.calls and "commit" not in conn.calls
    assert "close" not in conn.calls


def test_passes_params_to_cursor(fake_db):
    conn = fake_db(description=["id"], rows=[(1,)], rowcount=1)
    runner.run_sql("SELECT id FROM t WHERE x = %(V)s", {"V": "secret"}, conn=conn)
    assert conn.cursor().executed[0] == ("SELECT id FROM t WHERE x = %(V)s", {"V": "secret"})


def test_write_preview_rolls_back(fake_db):
    conn = fake_db(description=None, rowcount=3)
    result = runner.run_sql("UPDATE users SET x = 1", {}, write=True, conn=conn)
    assert result.columns == []
    assert result.rowcount == 3
    assert result.committed is False
    assert "rollback" in conn.calls and "commit" not in conn.calls


def test_write_commit_persists(fake_db):
    conn = fake_db(description=None, rowcount=2)
    result = runner.run_sql("DELETE FROM spam", {}, write=True, commit=True, conn=conn)
    assert result.committed is True
    assert "commit" in conn.calls and "rollback" not in conn.calls


def test_commit_without_write_rejected(fake_db):
    conn = fake_db(description=None, rowcount=0)
    with pytest.raises(ValueError) as exc:
        runner.run_sql("DELETE FROM t", {}, write=False, commit=True, conn=conn)
    assert "--write" in str(exc.value)


# --- multi-statement scripts (seeds/migrations) ------------------------------

def test_split_statements_basic_and_drops_empties():
    sql = "INSERT INTO a VALUES (1); INSERT INTO b VALUES (2);\nSELECT 1;\n\n;"
    assert runner.split_statements(sql) == [
        "INSERT INTO a VALUES (1)",
        "INSERT INTO b VALUES (2)",
        "SELECT 1",
    ]


def test_split_statements_quote_aware():
    assert runner.split_statements("INSERT INTO t VALUES ('a;b', 'it''s;ok');") == [
        "INSERT INTO t VALUES ('a;b', 'it''s;ok')",
    ]
    assert runner.split_statements('UPDATE "we;ird" SET x = 1;') == [
        'UPDATE "we;ird" SET x = 1',
    ]
    assert runner.split_statements("SELECT $$a;b$$; SELECT $tag$c;d$tag$;") == [
        "SELECT $$a;b$$",
        "SELECT $tag$c;d$tag$",
    ]


def test_split_statements_comment_aware():
    assert runner.split_statements("SELECT 1 -- not a split; here\n+ 2;") == [
        "SELECT 1 -- not a split; here\n+ 2",
    ]
    assert runner.split_statements("SELECT /* ; */ 1;") == [
        "SELECT /* ; */ 1",
    ]


def test_multi_statement_script_executes_each_in_one_transaction(fake_db):
    conn = fake_db(description=["h"], rows=[("x",)], rowcount=2)
    res = runner.run_sql(
        "INSERT INTO a VALUES ('s;1'); SELECT h FROM r;",
        {"K": "v"}, write=True, commit=True, conn=conn,
    )
    executed = conn.cursor().executed
    assert executed == [
        ("INSERT INTO a VALUES ('s;1')", {"K": "v"}),
        ("SELECT h FROM r", {"K": "v"}),
    ]
    assert res.rowcount == 4  # summed across statements
    assert res.columns == ["h"]  # from the last result-returning statement
    assert res.committed is True
    assert conn.calls.count("commit") == 1
