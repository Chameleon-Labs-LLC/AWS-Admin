# Secure Amplify Secrets CLI — Design

**Date:** 2026-05-30
**Status:** Approved (design); pending implementation plan
**Author:** Leland Green (with Claude)

## Problem

AWS admin tasks (currently performed ad hoc by Claude) occasionally risk exposing
secrets. The leak vector is **prompt/transcript exposure**: when Amplify environment
variables are read with `aws amplify get-app ... --query environmentVariables`, the
secret *values* (Stripe keys, `AUTH_SECRET`, SES creds, AI API keys) print directly
into the conversation. Updating them inline puts values into shell history and the
transcript too.

The AWS IAM access keys themselves are **not** the primary concern — the AWS CLI reads
them from `~/.aws/credentials` and they never transit a prompt. (Separate cleanup item:
that file is mode `0777` and has stray plaintext backups beside it; flagged, out of
scope for this build.)

## Goal

**Primary:** secret *values* must never appear in any prompt, transcript, or inline
command. Claude (and the slash commands it runs) orchestrate strictly by **app name +
action**. A local tool does all reading, editing, and writing of secret values.

**Secondary:** encryption at rest for the local store.

## Key Decisions

| Decision | Choice |
|---|---|
| Primary goal | Keep secret values out of the conversation (orchestrate-by-reference) |
| Language/runtime | Python (matches `.venv_linux`/python3 stack; boto3; good crypto libs) |
| Source of truth | Amplify is truth; local encrypted snapshot is an edit/backup buffer |
| Unlock model | Local key file (mode 0600, outside Dropbox), fully automatable by Claude |
| New/changed values | Entered via the tool's own interactive `$EDITOR` buffer (shredded after) |
| Structure | Single Python CLI package + thin global slash-command wrappers |
| MVP scope | Amplify env vars only; DB functions are a later subcommand group |

## Architecture & Layout

Python package in this repo, installed editable into `.venv_linux`, exposing one
console entry point `aws-admin`.

```
AWS-Admin/
  pyproject.toml
  src/aws_admin/
    __init__.py
    cli.py              # dispatch: `aws-admin <group> <action>`
    config.py           # app-name→app-id alias map (incl. acronyms), paths, region
    vault.py            # encrypted store: load/save/edit, Fernet, key-file mgmt
    redact.py           # the ONLY way values are rendered; key-name/length/hash diffs
    aws_client.py       # boto3 session wrapper (uses ~/.aws default profile)
    commands/
      env.py            # pull / diff / edit / push / redeploy  (MVP)
      # db.py           # later
  tests/                # unit tests, AWS calls mocked (moto / botocore Stubber)
  Docs/
  Notes/
  .gitignore            # blocks vault, keys, temp buffers
```

State (vault, key, backups) lives **outside the repo and outside Dropbox**, at
`~/.config/aws-admin/`:

```
~/.config/aws-admin/
  vault.key             # mode 0600, Fernet key (the automatable unlock)
  vaults/<app>.enc      # encrypted snapshot per app
  backups/<app>-<ts>.enc
```

### App resolution

`config.py` holds an alias map seeded from the known apps. Input is matched
case-insensitively against acronyms, full names, and raw app IDs. Ambiguous/unknown
input → list valid choices and exit (never guess).

| Input (any case) | Resolves to | App ID |
|---|---|---|
| `eo`, `exampleorg` | ExampleOrg | `d0000000000000` |
| `ab`, `appbeta` | AppBeta | `d0000000000000` |
| `aa`, `appalpha` | AppAlpha | `d0000000000000` |
| `ag`, `appgamma` | AppGamma | `d0000000000000` |
| `my`, `myapp2` | MyApp2 | `d0000000000000` |

Map is refreshable via `aws amplify list-apps`. Region: `us-east-1`. Account: `123456789012`.

## Vault & Crypto

- **Cipher:** `cryptography` Fernet (AES-128-CBC + HMAC). Key = 32 url-safe bytes in
  `~/.config/aws-admin/vault.key`, mode `0600`, generated on first run.
- **One encrypted file per app** holding JSON:
  `{ app_id, branch, app_level: {k:v}, branch_level: {k:v}, pulled_at }`.
- **Decryption is in-memory only.** The only plaintext that ever touches disk is the
  transient edit buffer (below), which is shredded.
- **Every push is preceded by a timestamped encrypted backup** of the pre-change remote
  state → `backups/<app>-<ts>.enc`, for instant rollback.

## Env-Var Workflow (MVP)

Subcommands under `aws-admin env`:

- **`pull <app>`** — fetch app-level *and* branch-level env vars via boto3; write the
  encrypted snapshot + a backup. Print only a redacted summary
  (`18 app-level keys, 1 branch-level key`).
- **`diff <app>`** — re-fetch remote, compare to local snapshot; print a **key-only**
  diff: `added: [...]`, `removed: [...]`, `changed: [...]` (changed determined by hash).
  Never prints values.
- **`edit <app>`** — decrypt the snapshot to a temp buffer (mode `0600`, prefer
  `/dev/shm`), open `$EDITOR`. User types/pastes new values directly. On save:
  re-encrypt, then shred the buffer (overwrite + unlink). This is the only path by which
  new secret values enter, and it bypasses Claude entirely.
- **`push <app>`** — **dry-run by default**: show the redacted key-diff of what would
  change and a branch-level-override reminder. `--apply` actually calls `update-app`
  (and `update-branch` when branch-level vars exist). Always sends the **full** var set
  (honors Amplify's REPLACE-not-merge). Writes a backup first.
- **`redeploy <app>`** — trigger `start-job ... RELEASE` on the branch.
  `push --apply` *offers* redeploy but does not force it (separate, explicit step).

**Branch-level handling** is automatic: pull captures both levels; push warns when
branch-level vars would shadow app-level ones and updates both.

## Redaction Guarantees (core security invariant)

- **Single chokepoint:** all CLI stdout/stderr goes through `redact.py`. No code path
  prints a raw value.
- **Value rendering:** key name + length + short salted hash prefix, e.g.
  `STRIPE_SECRET_KEY = <set, 107 chars, sha256:9f3a…>`.
- **Diffs are key-only.** "changed" comes from comparing hashes; only the key name shows.
- **No value-bearing flags.** There is deliberately no `--set KEY=VALUE` (would land in
  shell history + transcript). New values come only through `edit`'s interactive buffer.
- **Test-enforced:** a unit test scans all command output against known secret fixtures
  and fails if any raw value appears — the guarantee is a regression-tested property.

## Global Slash Commands (thin wrappers)

Installed to `~/.claude/commands/`; each shells out to `aws-admin` by app name + action.
App alias passed as `$ARGUMENTS`. No secrets, no logic — all guarantees live in the
tested package.

- `/aws-env-pull <app>` → `aws-admin env pull <app>`
- `/aws-env-diff <app>` → `aws-admin env diff <app>`
- `/aws-env-push <app>` → `aws-admin env push <app>` (dry-run; Claude reports key-diff,
  user decides to `--apply`)
- `/aws-env-edit <app>` → instructs the user to run `! aws-admin env edit <app>` so the
  editor attaches to their terminal

## Testing

- `pytest`; AWS mocked with `botocore.Stubber` (or `moto`) — no live calls by default.
- Coverage: app-alias resolution (acronyms + ambiguity), vault encrypt/decrypt
  round-trip, backup-before-push, REPLACE-not-merge full-set push, branch-level shadow
  detection, edit-buffer shredding, and the redaction leak test.
- One opt-in live smoke test (`--run-live`, off by default) doing a real `pull` of one
  app to confirm boto3 wiring.

## Out of Scope (this build)

- DB functions (planned as a later `db` subcommand group).
- Tightening `~/.aws/credentials` perms and removing stray plaintext backups (flagged
  separately as a quick cleanup).
- Re-storing the AWS IAM keys (AWS CLI already manages them; they never hit a prompt).
