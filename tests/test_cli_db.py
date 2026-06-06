from aws_admin import cli


def test_parse_db_run_flags():
    args = cli.build_parser().parse_args(["db", "run", "user-count", "--write", "--commit", "--show"])
    assert args.group == "db"
    assert args.action == "run"
    assert args.target == "user-count"
    assert args.write is True
    assert args.commit is True
    assert args.show is True


def test_parse_db_list():
    args = cli.build_parser().parse_args(["db", "list"])
    assert args.group == "db" and args.action == "list"


def test_main_db_value_error_returns_1(monkeypatch, capsys):
    from aws_admin.commands import db as db_cmd
    def boom(target, **kwargs):
        raise ValueError("curated read-only")
    monkeypatch.setattr(db_cmd, "run", boom)
    rc = cli.main(["db", "run", "user-count", "--write"])
    assert rc == 1
    assert "error:" in capsys.readouterr().err


def test_main_db_error_returns_3(monkeypatch, capsys):
    import psycopg
    from aws_admin.commands import db as db_cmd
    def boom(target, **kwargs):
        raise psycopg.OperationalError("connection refused")
    monkeypatch.setattr(db_cmd, "run", boom)
    rc = cli.main(["db", "run", "user-count"])
    assert rc == 3
    assert "DB error" in capsys.readouterr().err
