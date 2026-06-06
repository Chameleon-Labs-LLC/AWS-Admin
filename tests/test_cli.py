import subprocess
import sys
from aws_admin import cli


def test_cli_help_runs():
    out = subprocess.run(
        [sys.executable, "-m", "aws_admin.cli", "--help"],
        capture_output=True, text=True,
    )
    assert out.returncode == 0
    assert "env" in out.stdout


def test_parse_env_pull():
    args = cli.build_parser().parse_args(["env", "pull", "my"])
    assert args.group == "env"
    assert args.action == "pull"
    assert args.app == "my"


def test_parse_env_push_apply_flag():
    args = cli.build_parser().parse_args(["env", "push", "my", "--apply", "--redeploy"])
    assert args.apply is True
    assert args.redeploy is True


from botocore.exceptions import ClientError as _ClientError
from aws_admin.commands import env as _env


def test_main_reports_aws_error_friendly(monkeypatch, capsys):
    def boom(app):
        raise _ClientError(
            {"Error": {"Code": "NotFoundException", "Message": "app gone"}},
            "GetApp",
        )
    monkeypatch.setattr(_env, "diff", boom)
    rc = cli.main(["env", "diff", "my"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "AWS error" in err


def test_main_reports_unknown_app(capsys):
    rc = cli.main(["env", "pull", "definitely-not-an-app"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "error:" in err
