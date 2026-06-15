import subprocess
import sys

import pytest

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


# Synthetic apps from conftest, in case-insensitive alphabetical order.
_SORTED_APPS = ["AppAlpha", "AppBeta", "AppGamma", "ExampleOrg", "MyApp2"]


def test_env_pull_all_runs_every_app_in_order(monkeypatch, capsys):
    seen = []
    monkeypatch.setattr(_env, "pull", lambda t: seen.append(t) or f"pulled {t}")
    rc = cli.main(["env", "pull", "all"])
    assert rc == 0
    assert seen == _SORTED_APPS
    assert "pulled AppAlpha" in capsys.readouterr().out


def test_env_all_token_is_case_insensitive(monkeypatch):
    seen = []
    monkeypatch.setattr(_env, "redeploy", lambda t: seen.append(t) or "ok")
    assert cli.main(["env", "redeploy", "ALL"]) == 0
    assert seen == _SORTED_APPS


def test_env_push_all_continues_past_per_app_failure(monkeypatch, capsys):
    def push(t, apply=False, redeploy=False):
        if t == "AppBeta":
            raise FileNotFoundError("no snapshot")
        return f"pushed {t}"
    monkeypatch.setattr(_env, "push", push)
    rc = cli.main(["env", "push", "all"])
    assert rc == 1  # one app failed
    cap = capsys.readouterr()
    assert "pushed AppAlpha" in cap.out and "pushed MyApp2" in cap.out  # others still ran
    assert "AppBeta" in cap.err  # failure surfaced, not swallowed


def _flatten(text: str) -> str:
    # argparse wraps help across lines; collapse whitespace to match phrases.
    return " ".join(text.split())


def test_pull_help_advertises_all(capsys):
    with pytest.raises(SystemExit):
        cli.main(["env", "pull", "-h"])
    assert "or 'all' for every configured app" in _flatten(capsys.readouterr().out)


def test_diff_does_not_advertise_all_on_app_arg(capsys):
    with pytest.raises(SystemExit):
        cli.main(["env", "diff", "-h"])
    assert "for every configured app" not in _flatten(capsys.readouterr().out)


def test_parse_env_rotate():
    args = cli.build_parser().parse_args(
        ["env", "rotate", "AI_SECRET", "cl", "hc", "qs"]
    )
    assert args.group == "env"
    assert args.action == "rotate"
    assert args.name == "AI_SECRET"
    assert args.apps == ["cl", "hc", "qs"]


def test_rotate_dispatch_calls_command(monkeypatch, capsys):
    seen = {}

    def fake_rotate(name, apps):
        seen["name"] = name
        seen["apps"] = apps
        return "ROTATED-OK"

    monkeypatch.setattr(_env, "rotate", fake_rotate)
    rc = cli.main(["env", "rotate", "AI_SECRET", "my", "aa"])
    assert rc == 0
    assert seen == {"name": "AI_SECRET", "apps": ["my", "aa"]}
    assert "ROTATED-OK" in capsys.readouterr().out


def test_rotate_help_advertises_all(capsys):
    with pytest.raises(SystemExit):
        cli.main(["env", "rotate", "-h"])
    assert "for every configured app" in _flatten(capsys.readouterr().out)
