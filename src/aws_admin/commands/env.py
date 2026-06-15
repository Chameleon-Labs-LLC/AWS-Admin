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


def _default_confirm(prompt: str) -> bool:
    return input(prompt).strip().lower() in ("y", "yes")


_LEVEL_LABEL = {"app_level": "app-level", "branch_level": "branch-level"}


def _format_rotate_summary(name, rotated, added, skipped, unchanged) -> str:
    """Render a key/app-only summary. `rotated` is a list of (app_name, levels)."""
    lines = [f"Rotated {name} in {len(rotated)} app(s):"]
    for app_name, levels in rotated:
        phrase = " + ".join(_LEVEL_LABEL[lvl] for lvl in levels)
        lines.append(f"  {app_name}: {phrase} updated")
    if not rotated:
        lines.append("  (none)")
    added_str = ", ".join(f"{n} (app-level)" for n in added) if added else "(none)"
    lines.append(f"Added (was missing): {added_str}")
    lines.append(f"Skipped (declined add): {', '.join(skipped) if skipped else '(none)'}")
    lines.append(f"Unchanged (same value): {', '.join(unchanged) if unchanged else '(none)'}")
    lines.append("")
    lines.append("Next: aws-admin env push <app> --apply   (or 'all') to send + redeploy.")
    return "\n".join(lines)


def rotate(name: str, app_tokens: list[str], *, confirm=None, open_editor=None) -> str:
    """Set `name` to a new value across the given apps' LOCAL snapshots. No AWS.

    Enter the value once in the editor; it is written to every level (app and/or
    branch) where the key already lives. Missing keys are added at app-level only
    if `confirm` returns truthy. Each changed snapshot is backed up first. Run
    `env push` afterward to send the change to Amplify.
    """
    confirm = confirm or _default_confirm
    refs = config.resolve_apps(app_tokens)

    # Load every snapshot up front; fail-fast if any app was never pulled.
    snaps: dict[str, dict] = {}
    missing_snapshot: list[str] = []
    for ref in refs:
        snap = vault.load_snapshot(ref.name)
        if snap is None:
            missing_snapshot.append(ref.name)
        else:
            snaps[ref.name] = snap
    if missing_snapshot:
        raise FileNotFoundError(
            f"No local snapshot for: {', '.join(missing_snapshot)}. "
            f"Run 'aws-admin env pull <app>' first."
        )

    # Plan which levels to write per app; prompt to add where the key is missing.
    plan: dict[str, list[str]] = {}
    added: list[str] = []
    skipped: list[str] = []
    for ref in refs:
        snap = snaps[ref.name]
        levels = [lvl for lvl in ("app_level", "branch_level") if name in snap[lvl]]
        if levels:
            plan[ref.name] = levels
        elif confirm(f"Add {name} to {ref.name} (app-level)? [y/N] "):
            plan[ref.name] = ["app_level"]
            added.append(ref.name)
        else:
            skipped.append(ref.name)

    if not plan:
        return f"{name}: nothing to rotate (no app has it and no add was confirmed)."

    target_names = [ref.name for ref in refs if ref.name in plan]
    new_value = vault.capture_value(name, target_names, _open_editor=open_editor) \
        if open_editor is not None else vault.capture_value(name, target_names)
    # A whitespace-only entry is almost certainly a slip; treat it as empty so we
    # never silently rotate a shared secret to blanks. Values that merely contain
    # spaces (e.g. " abc ") are preserved as typed below.
    if not new_value.strip():
        return f"{name}: empty value entered — aborted, no changes."

    added_set = set(added)
    rotated: list[tuple[str, list[str]]] = []
    newly_added: list[str] = []
    unchanged: list[str] = []
    for ref in refs:
        if ref.name not in plan:
            continue
        snap = snaps[ref.name]
        levels = plan[ref.name]
        if ref.name not in added_set and all(
            snap[lvl].get(name) == new_value for lvl in levels
        ):
            unchanged.append(ref.name)
            continue
        vault.backup_snapshot(ref.name, snap)
        for lvl in levels:
            snap[lvl][name] = new_value
        vault.save_snapshot(ref.name, snap)
        if ref.name in added_set:
            newly_added.append(ref.name)
        else:
            rotated.append((ref.name, levels))

    return _format_rotate_summary(name, rotated, newly_added, skipped, unchanged)
