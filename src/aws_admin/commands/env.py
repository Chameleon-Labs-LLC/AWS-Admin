"""Amplify env-var commands. Values never appear in any return value or print."""
from __future__ import annotations

import re
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
    with vault.snapshot_lock(ref.name):
        return _pull_locked(ref, client)


def _pull_locked(ref, client) -> str:
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
    """Locked entry point; see _rotate_locked."""
    names = [ref.name for ref in config.resolve_apps(app_tokens)]
    with vault.snapshot_lock(*names):
        return _rotate_locked(name, app_tokens, confirm=confirm, open_editor=open_editor)


def _rotate_locked(name: str, app_tokens: list[str], *, confirm=None, open_editor=None) -> str:
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


# Amplify env-var names: letters, digits and underscores, not starting with a digit.
_KEY_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _parse_renames(keys: list[str], renames: list[str]) -> dict[str, str]:
    """Map each source key to its destination key. Raises ValueError (names only)."""
    mapping = {k: k for k in keys}
    renamed: set[str] = set()
    for spec in renames:
        if spec.count("=") != 1:
            raise ValueError(f"--rename expects SRC=DST, got '{spec}'.")
        src, dst = (part.strip() for part in spec.split("=", 1))
        if not src or not dst:
            raise ValueError(f"--rename expects SRC=DST, got '{spec}'.")
        if src not in mapping:
            raise ValueError(
                f"--rename {src}={dst}: {src} is not one of the keys being copied."
            )
        if src in renamed:
            raise ValueError(f"--rename given twice for {src}.")
        if not _KEY_NAME.match(dst):
            raise ValueError(
                f"--rename {src}={dst}: '{dst}' is not a valid env-var name "
                f"(letters, digits, underscores; not starting with a digit)."
            )
        mapping[src] = dst
        renamed.add(src)
    seen: dict[str, str] = {}
    for src, dst in mapping.items():
        if dst in seen:
            raise ValueError(
                f"{seen[dst]} and {src} would both be copied to {dst}. "
                f"Pick distinct destination names."
            )
        seen[dst] = src
    return mapping


def _source_value(snap: dict, key: str, app_name: str) -> str:
    """The value `key` has in a source snapshot: branch-level wins (as in Amplify).

    Raises ValueError, naming the key only, when the key is blank or set to two
    different values at app- and branch-level. The caller checks presence first.
    """
    in_app = key in snap["app_level"]
    in_branch = key in snap["branch_level"]
    if in_app and in_branch and snap["app_level"][key] != snap["branch_level"][key]:
        raise ValueError(
            f"{key} has different app-level and branch-level values in {app_name}; "
            f"resolve that with 'aws-admin env edit' before copying. No changes made."
        )
    value = snap["branch_level"][key] if in_branch else snap["app_level"][key]
    if not isinstance(value, str) or not value.strip():
        raise ValueError(
            f"{key} has no usable value in the {app_name} snapshot. No changes made."
        )
    return value


def _format_copy_summary(src_name, dst_name, written, unchanged) -> str:
    """Key/app-only summary. `written` is a list of (src, dst, levels, added)."""
    lines = [f"Copied {len(written)} key(s) from {src_name} to {dst_name} "
             f"(local snapshot only, nothing sent to AWS):"]
    for src, dst, levels, added in written:
        target = dst if src == dst else f"{src} -> {dst}"
        phrase = " + ".join(_LEVEL_LABEL[lvl] for lvl in levels)
        lines.append(f"  {target}: {'added' if added else 'replaced'} ({phrase})")
    if not written:
        lines.append("  (none)")
    lines.append(f"Unchanged (same value): {', '.join(unchanged) if unchanged else '(none)'}")
    if written:
        lines.append("")
        lines.append(f"Next: aws-admin env push {dst_name}   (dry run, key-only diff), "
                     f"then add --apply --redeploy.")
    return "\n".join(lines)


def copy(keys: list[str], from_token: str, to_token: str, *,
         renames: list[str] | None = None, overwrite: bool = False) -> str:
    """Copy env-var values from one app's LOCAL snapshot to another's. No AWS.

    Values move between the two encrypted snapshots in memory and are never
    returned, printed or measured. Every check runs before any write, so a
    refusal changes nothing:

    - the two apps must differ, and both must have been pulled;
    - every key must be in the source snapshot with a usable value;
    - a destination key that already holds a DIFFERENT value is replaced only
      with ``overwrite=True``; otherwise the copy is refused and those keys
      are named.

    A destination key is written at every level where it already lives, or at
    app-level when it is new. The destination snapshot is backed up first.
    Run ``env push <to>`` afterward to send the change to Amplify.
    """
    for token in (from_token, to_token):
        if token.strip().lower() == "all":
            raise ValueError("copy takes one source app and one destination app; "
                             "'all' is not supported.")
    src_ref = config.resolve_app(from_token)
    dst_ref = config.resolve_app(to_token)
    if src_ref.name == dst_ref.name:
        raise ValueError(
            f"--from and --to are the same app ({src_ref.name}); nothing to copy."
        )

    keys = list(dict.fromkeys(k.strip() for k in keys))
    if not keys or not all(keys):
        raise ValueError("Name at least one key to copy.")
    mapping = _parse_renames(keys, list(renames or []))

    # The destination is read, checked and written under its lock, so two
    # copies (or a copy and an edit) can't both pass the overwrite check.
    with vault.snapshot_lock(dst_ref.name):
        return _copy_locked(keys, src_ref, dst_ref, mapping, overwrite)


def _copy_locked(keys, src_ref, dst_ref, mapping, overwrite) -> str:
    # Load both snapshots up front; fail fast if either app was never pulled.
    src_snap = vault.load_snapshot(src_ref.name)
    dst_snap = vault.load_snapshot(dst_ref.name)
    missing_snapshot = [ref.name for ref, snap in ((src_ref, src_snap), (dst_ref, dst_snap))
                        if snap is None]
    if src_snap is None or dst_snap is None:
        raise FileNotFoundError(
            f"No local snapshot for: {', '.join(missing_snapshot)}. "
            f"Run 'aws-admin env pull <app>' first."
        )

    missing_keys = [k for k in keys
                    if k not in src_snap["app_level"] and k not in src_snap["branch_level"]]
    if missing_keys:
        raise ValueError(
            f"Not in the {src_ref.name} snapshot: {', '.join(missing_keys)}. "
            f"No changes made. (Pull {src_ref.name} again if the key was added recently.)"
        )
    values = {k: _source_value(src_snap, k, src_ref.name) for k in keys}

    # Plan each destination key: (src, dst, levels, added); same value = unchanged.
    plan: list[tuple[str, str, list[str], bool]] = []
    unchanged: list[str] = []
    conflicts: list[str] = []
    for src in keys:
        dst = mapping[src]
        levels = [lvl for lvl in ("app_level", "branch_level") if dst in dst_snap[lvl]]
        if not levels:
            plan.append((src, dst, ["app_level"], True))
        elif all(dst_snap[lvl][dst] == values[src] for lvl in levels):
            unchanged.append(dst)
        else:
            conflicts.append(dst)
            plan.append((src, dst, levels, False))

    if conflicts and not overwrite:
        raise ValueError(
            f"{dst_ref.name} already has a different value for: {', '.join(conflicts)}. "
            f"Re-run with --overwrite to replace them. No changes made."
        )

    if plan:
        vault.backup_snapshot(dst_ref.name, dst_snap)
        for src, dst, levels, _added in plan:
            for lvl in levels:
                dst_snap[lvl][dst] = values[src]
        vault.save_snapshot(dst_ref.name, dst_snap)

    return _format_copy_summary(src_ref.name, dst_ref.name, plan, unchanged)
