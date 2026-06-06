"""Amplify env-var commands. Values never appear in any return value or print."""
from __future__ import annotations

from datetime import datetime, timezone

from botocore.exceptions import ClientError

from .. import aws_client, config, redact, vault


def _client(client):
    return client if client is not None else aws_client.amplify_client()


def _fetch_remote(client, app_id: str, branch: str) -> tuple[dict, dict]:
    app_env = client.get_app(appId=app_id)["app"].get("environmentVariables", {}) or {}
    try:
        branch_env = (
            client.get_branch(appId=app_id, branchName=branch)["branch"]
            .get("environmentVariables", {}) or {}
        )
    except ClientError as e:
        if e.response["Error"]["Code"] == "NotFoundException":
            branch_env = {}
        else:
            raise
    return dict(app_env), dict(branch_env)


def pull(app_token: str, client=None) -> str:
    ref = config.resolve_app(app_token)
    client = _client(client)
    app_env, branch_env = _fetch_remote(client, ref.app_id, config.DEFAULT_BRANCH)

    existing = vault.load_snapshot(ref.name)
    if existing is not None:
        vault.backup_snapshot(ref.name, existing)

    snap = {
        "app_id": ref.app_id,
        "branch": config.DEFAULT_BRANCH,
        "app_level": app_env,
        "branch_level": branch_env,
        "pulled_at": datetime.now(timezone.utc).isoformat(),
    }
    vault.save_snapshot(ref.name, snap)

    bl = len(branch_env)
    return (f"{ref.name}: {len(app_env)} app-level keys, "
            f"{bl} branch-level {'key' if bl == 1 else 'keys'}")


def diff(app_token: str, client=None) -> str:
    ref = config.resolve_app(app_token)
    snap = vault.load_snapshot(ref.name)
    if snap is None:
        raise FileNotFoundError(
            f"No local snapshot for {ref.name}. Run `aws-admin env pull {app_token}` first."
        )
    client = _client(client)
    app_env, branch_env = _fetch_remote(client, ref.app_id, config.DEFAULT_BRANCH)

    app_d = redact.key_diff(snap["app_level"], app_env)
    branch_d = redact.key_diff(snap["branch_level"], branch_env)
    return (f"[app-level]\n{redact.format_diff(app_d)}\n"
            f"[branch-level]\n{redact.format_diff(branch_d)}")


def push(app_token: str, apply: bool = False, redeploy: bool = False, client=None) -> str:
    ref = config.resolve_app(app_token)
    snap = vault.load_snapshot(ref.name)
    if snap is None:
        raise FileNotFoundError(
            f"No local snapshot for {ref.name}. Run `aws-admin env pull {app_token}` first."
        )
    client = _client(client)
    remote_app, remote_branch = _fetch_remote(client, ref.app_id, config.DEFAULT_BRANCH)

    app_d = redact.key_diff(remote_app, snap["app_level"])
    branch_d = redact.key_diff(remote_branch, snap["branch_level"])
    diff_text = (f"[app-level]\n{redact.format_diff(app_d)}\n"
                 f"[branch-level]\n{redact.format_diff(branch_d)}")

    if not apply:
        warn = ""
        if snap["branch_level"]:
            warn = ("\nNOTE: branch-level vars exist and OVERRIDE app-level vars; "
                    "branch-level will be replaced on --apply.")
        elif remote_branch:
            warn = ("\nNOTE: remote has branch-level vars but the local snapshot has none; "
                    "they will be CLEARED on --apply.")
        return f"DRY RUN — {ref.name} (no changes sent):\n{diff_text}{warn}"

    # Back up the pre-change REMOTE state for rollback.
    vault.backup_snapshot(ref.name, {
        "app_id": ref.app_id, "branch": config.DEFAULT_BRANCH,
        "app_level": remote_app, "branch_level": remote_branch,
        "pulled_at": datetime.now(timezone.utc).isoformat(),
    })

    # REPLACE-not-merge: always send the FULL desired set.
    client.update_app(appId=ref.app_id, environmentVariables=snap["app_level"])
    if snap["branch_level"] or remote_branch:
        client.update_branch(
            appId=ref.app_id, branchName=config.DEFAULT_BRANCH,
            environmentVariables=snap["branch_level"],
        )

    result = f"APPLIED — {ref.name}:\n{diff_text}"
    if redeploy:
        client.start_job(appId=ref.app_id, branchName=config.DEFAULT_BRANCH, jobType="RELEASE")
        result += "\nredeploy: RELEASE job started"
    return result


def redeploy(app_token: str, client=None) -> str:
    ref = config.resolve_app(app_token)
    client = _client(client)
    client.start_job(appId=ref.app_id, branchName=config.DEFAULT_BRANCH, jobType="RELEASE")
    return f"{ref.name}: RELEASE job started on '{config.DEFAULT_BRANCH}'."


def edit(app_token: str) -> str:
    ref = config.resolve_app(app_token)
    changed = vault.edit_app_buffer(ref.name)
    if changed:
        return (f"{ref.name}: local snapshot updated. "
                f"Run `aws-admin env push {app_token}` to review, then `--apply`.")
    return f"{ref.name}: no changes."
