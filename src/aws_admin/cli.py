"""Command-line dispatch for aws-admin."""
from __future__ import annotations

import argparse
import sys

import psycopg
from botocore.exceptions import BotoCoreError, ClientError

from . import config, vault
from .commands import env as env_cmd
from .commands import db as db_cmd


def _apps_epilog() -> str:
    """Render the configured apps (names + acronyms) for -h output.

    Shown on every parser that accepts an ``app`` argument, plus the top-level
    parser, so the valid tokens are discoverable from any ``-h``. Never raises
    and never prints app IDs or other sensitive values.
    """
    try:
        apps = config.app_aliases()
    except config.ConfigError:
        return (
            "Known apps:\n"
            "  (none configured — copy config.example.toml to "
            "$AWS_ADMIN_HOME/config.toml, default ~/.config/aws-admin/)"
        )
    if not apps:
        return "Known apps:\n  (no apps defined in config.toml)"
    width = max(len(name) for name, _ in apps)
    lines = ["Known apps (pass the name or any acronym below):"]
    for name, aliases in apps:
        acronyms = ", ".join(aliases) if aliases else "(no acronym)"
        lines.append(f"  {name.ljust(width)}  {acronyms}")
    lines.append("")
    lines.append("Pass 'all' to pull, redeploy, push, or rotate across every app above.")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    apps_epilog = _apps_epilog()
    parser = argparse.ArgumentParser(
        prog="aws-admin",
        description="Secure AWS admin — manage Amplify env vars without exposing values.",
        epilog=apps_epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    groups = parser.add_subparsers(dest="group", required=True)

    env_p = groups.add_parser(
        "env",
        help="Amplify environment variables",
        epilog=apps_epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    actions = env_p.add_subparsers(dest="action", required=True)

    app_help = "App acronym, name, or id (e.g. my, eo, MyApp2)"
    app_help_all = f"{app_help}, or 'all' for every configured app"

    # name, help text, whether the command accepts the 'all' token
    for name, help_text, allows_all in [
        ("pull", "Fetch app+branch env vars into the encrypted local snapshot", True),
        ("diff", "Show key-only diff between local snapshot and remote", False),
        ("edit", "Open the local snapshot in $EDITOR (values stay local)", False),
        ("redeploy", "Start a RELEASE job on the branch", True),
    ]:
        sp = actions.add_parser(
            name,
            help=help_text,
            epilog=apps_epilog,
            formatter_class=argparse.RawDescriptionHelpFormatter,
        )
        sp.add_argument("app", help=app_help_all if allows_all else app_help)

    push_p = actions.add_parser(
        "push",
        help="Push local snapshot to Amplify",
        epilog=apps_epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    push_p.add_argument("app", help=app_help_all)
    push_p.add_argument("--apply", action="store_true",
                        help="Actually send changes (default is dry-run)")
    push_p.add_argument("--redeploy", action="store_true",
                        help="Start a RELEASE job after a successful --apply")

    rotate_p = actions.add_parser(
        "rotate",
        help="Rotate one shared secret across apps' local snapshots (no AWS; push after)",
        epilog=apps_epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    rotate_p.add_argument("name", help="Env-var key to rotate, e.g. AI_STREAM_SECRET")
    rotate_p.add_argument(
        "apps", nargs="+",
        help=f"One or more app tokens (space-separated), or 'all'. {app_help_all}",
    )

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


def _dispatch_env_app(action_fn, token: str) -> int:
    """Run an env action that supports the special 'all' token.

    Single app: call once and let any exception propagate to main()'s handler
    (unchanged behavior and exit codes). 'all': run each configured app
    independently — a failure on one app is reported to stderr and the loop
    continues — returning a non-zero exit code if any app failed.
    """
    if token.strip().lower() != "all":
        print(action_fn(token))
        return 0

    failed = 0
    for ref in config.known_apps():
        try:
            print(action_fn(ref.name))
        except (config.UnknownAppError, FileNotFoundError, vault.VaultError, ValueError) as e:
            print(f"error: {ref.name}: {e}", file=sys.stderr)
            failed += 1
        except (ClientError, BotoCoreError) as e:
            print(f"AWS error: {ref.name}: {e}", file=sys.stderr)
            failed += 1
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.group == "env":
            if args.action == "pull":
                return _dispatch_env_app(env_cmd.pull, args.app)
            elif args.action == "diff":
                print(env_cmd.diff(args.app))
            elif args.action == "edit":
                print(env_cmd.edit(args.app))
            elif args.action == "redeploy":
                return _dispatch_env_app(env_cmd.redeploy, args.app)
            elif args.action == "push":
                return _dispatch_env_app(
                    lambda t: env_cmd.push(t, apply=args.apply, redeploy=args.redeploy),
                    args.app,
                )
            elif args.action == "rotate":
                print(env_cmd.rotate(args.name, args.apps))
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
