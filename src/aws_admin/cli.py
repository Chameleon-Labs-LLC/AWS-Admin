"""Command-line dispatch for aws-admin."""
from __future__ import annotations

import argparse
import sys

import psycopg
from botocore.exceptions import BotoCoreError, ClientError

from . import config, vault
from .commands import env as env_cmd
from .commands import db as db_cmd


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aws-admin",
        description="Secure AWS admin — manage Amplify env vars without exposing values.",
    )
    groups = parser.add_subparsers(dest="group", required=True)

    env_p = groups.add_parser("env", help="Amplify environment variables")
    actions = env_p.add_subparsers(dest="action", required=True)

    for name, help_text in [
        ("pull", "Fetch app+branch env vars into the encrypted local snapshot"),
        ("diff", "Show key-only diff between local snapshot and remote"),
        ("edit", "Open the local snapshot in $EDITOR (values stay local)"),
        ("redeploy", "Start a RELEASE job on the branch"),
    ]:
        sp = actions.add_parser(name, help=help_text)
        sp.add_argument("app", help="App acronym, name, or id (e.g. my, eo, MyApp2)")

    push_p = actions.add_parser("push", help="Push local snapshot to Amplify")
    push_p.add_argument("app", help="App acronym, name, or id")
    push_p.add_argument("--apply", action="store_true",
                        help="Actually send changes (default is dry-run)")
    push_p.add_argument("--redeploy", action="store_true",
                        help="Start a RELEASE job after a successful --apply")

    db_p = groups.add_parser("db", help="PostgreSQL admin")
    db_actions = db_p.add_subparsers(dest="action", required=True)
    db_actions.add_parser("set-password", help="Store the DB password (hidden prompt)")
    db_actions.add_parser("check", help="Connectivity/auth smoke test")
    db_actions.add_parser("list", help="List curated read-only queries")
    db_actions.add_parser("clean-results", help="Delete saved result files")
    run_p = db_actions.add_parser("run", help="Run a curated query name or a .sql file")
    run_p.add_argument("target", help="Curated query name or path to a .sql file")
    run_p.add_argument("--write", action="store_true",
                       help="Allow writes (rolls back unless --commit)")
    run_p.add_argument("--commit", action="store_true",
                       help="Persist a --write run")
    run_p.add_argument("--show", action="store_true",
                       help="Print result rows inline (non-sensitive results only)")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.group == "env":
            if args.action == "pull":
                print(env_cmd.pull(args.app))
            elif args.action == "diff":
                print(env_cmd.diff(args.app))
            elif args.action == "edit":
                print(env_cmd.edit(args.app))
            elif args.action == "redeploy":
                print(env_cmd.redeploy(args.app))
            elif args.action == "push":
                print(env_cmd.push(args.app, apply=args.apply, redeploy=args.redeploy))
        elif args.group == "db":
            if args.action == "set-password":
                db_cmd.set_password()
                print("DB password stored.")
            elif args.action == "check":
                print(db_cmd.check())
            elif args.action == "list":
                print(db_cmd.list_queries())
            elif args.action == "clean-results":
                print(f"Removed {db_cmd.clean_results()} result file(s).")
            elif args.action == "run":
                print(db_cmd.run(args.target, write=args.write,
                                 commit=args.commit, show=args.show))
    except (config.UnknownAppError, FileNotFoundError, vault.VaultError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except (ClientError, BotoCoreError) as e:
        # Surface AWS failures (bad app id, access denied, network) as a one-line
        # message instead of a raw boto stack trace. Boto error strings do not
        # contain our secret values.
        print(f"AWS error: {e}", file=sys.stderr)
        return 2
    except psycopg.Error as e:
        print(f"DB error: {e}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
