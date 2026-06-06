# Implementation Summary — Secure Amplify Secrets CLI

**Date:** 2026-05-30
**Branch merged:** `feat/secure-amplify-secrets-cli` → `master` (merge `7a74e6d`)
**Status:** Complete. 54 tests passing. Live smoke test verified against AWS.

## What was built
A Python CLI (`aws-admin`) that manages AWS Amplify environment variables by **app name + action**, so secret *values* never enter a prompt, transcript, or inline shell command.

- **Design spec:** `docs/superpowers/specs/2026-05-30-secure-amplify-secrets-cli-design.md`
- **Implementation plan:** `docs/superpowers/plans/2026-05-30-secure-amplify-secrets-cli.md`

## Architecture
- `src/aws_admin/config.py` — paths, constants, app-alias resolution (`my`, `ab`, `aa`, `ag`, `my` + full names + app IDs; never guesses).
- `src/aws_admin/redact.py` — the single output chokepoint: key-only diffs. No function returns a raw value.
- `src/aws_admin/vault.py` — Fernet-encrypted per-app snapshots in `~/.config/aws-admin/`; atomic `0600` writes; interactive `$EDITOR` buffer (RAM-backed `/dev/shm`, shredded in a `finally` even on editor crash); `VaultError` on corrupt/tampered vault.
- `src/aws_admin/aws_client.py` — boto3 Amplify client (default `~/.aws` profile).
- `src/aws_admin/commands/env.py` — `pull` / `diff` / `push` / `redeploy` / `edit`.
- `src/aws_admin/cli.py` — argparse dispatch; friendly one-line errors (no stack traces, no values).

## Commands
```
aws-admin env pull <app>                     # mirror remote env vars into encrypted snapshot
aws-admin env diff <app>                      # key-only diff: local snapshot vs live
aws-admin env edit <app>                      # edit values in $EDITOR (you type them, not the model)
aws-admin env push <app>                      # DRY-RUN (key-only diff) by default
aws-admin env push <app> --apply [--redeploy] # send full set (REPLACE-not-merge) + optional redeploy
aws-admin env redeploy <app>                  # start a RELEASE job
```
Global slash commands: `/aws-env-pull|diff|push|edit` (in `~/.claude/commands/`, reference copies in `slash-commands/`).

## Security properties (regression-tested)
- Secret values never appear in any command output — `tests/test_no_value_leak.py` drives the real command paths and asserts the secret never surfaces; vault files on disk are opaque Fernet ciphertext.
- No `--set KEY=VALUE` flags (would leak into shell history). New values enter only via the interactive `edit` buffer.
- `push` is dry-run unless `--apply`; every `--apply` backs up the prior **remote** state to `~/.config/aws-admin/backups/<app>-<ts>.enc` first.
- REPLACE-not-merge honored; branch-level overrides detected, updated, and cleared with a dry-run warning.

## Live verification (2026-05-30)
`aws-admin env pull my` → "MyApp2: 26 app-level keys, 0 branch-level keys";
`pull eo` → "ExampleOrg: 62 app-level keys" — only counts surfaced, values encrypted to disk.

## Out of scope / next
- **DB functions** — planned as a later `db` subcommand group (will reuse `config.ACCOUNT_ID`, the vault, and the redaction chokepoint).
- Tightening `~/.aws/credentials` perms (currently `0777`) and removing stray plaintext backups (`credentials.backup-*`, `credentials.nick-backup`) — flagged, not done.
- A future `env show <app> <KEY>` could re-introduce a salted value-digest renderer (removed as dead code; recoverable from git history before `1ab8b23`).
