# AWS-Admin

Secure local CLI for AWS admin tasks. First capability: managing the
environment variables of [AWS Amplify](https://aws.amazon.com/amplify/) apps
**without secret values ever entering a prompt, transcript, or shell command**.

## Why

Amplify apps keep their configuration — including secrets like API keys and
database URLs — in environment variables. The usual ways of managing those
leak the values everywhere: `aws amplify get-app` prints every secret straight
into your terminal (and your shell history, and any AI-assistant conversation
you ran it in), and `update-app --environment-variables KEY=secret` puts the
secret on the command line itself.

This tool keeps Amplify as the source of truth, mirrors env vars into an
encrypted local snapshot, and only ever prints key *names* — never values. You
type new values directly into your text editor; they go from your keyboard
into an encrypted file and up to AWS without passing through a command line.

## Requirements

- Python 3.12+
- AWS credentials configured on this machine (the same `~/.aws` setup the AWS
  CLI uses — see [Prerequisites](docs/usage.md#prerequisites) if you haven't
  done this before)

## Install

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

After installing, copy the example config and add your AWS values (account ID,
Amplify app IDs, optional database endpoint). **New here? Follow
[First-time setup](docs/usage.md#first-time-setup)** — it shows exactly where
to find each value in the AWS Console or via the AWS CLI.

## Workflow

App names and their short aliases come from *your* config file — the examples
below assume an app with the alias `shop`:

```bash
aws-admin env pull shop        # mirror the app's env vars into the encrypted snapshot
aws-admin env edit shop        # change values in your $EDITOR (you type them, not the model)
aws-admin env push shop        # dry-run: key-only diff of what would change (changes nothing)
aws-admin env push shop --apply --redeploy   # actually apply + redeploy
aws-admin env diff shop        # compare local snapshot vs live
aws-admin env redeploy shop    # start a RELEASE job (rebuild/redeploy)
```

## Where state lives

Everything stays in `~/.config/aws-admin/` (outside the repo, outside any
synced folder): `vault.key` (file mode 0600 — owner-only), encrypted snapshots
in `vaults/<app>.enc`, and automatic pre-change backups in
`backups/<app>-<timestamp>.enc`. AWS auth uses your default `~/.aws` profile.

## Security model

- Secret values never appear in command output (single redaction chokepoint in
  the code, regression-tested).
- No `--set KEY=VALUE` flags (they would land in shell history); new values
  come only via `edit`, which uses a RAM-backed temp buffer that is shredded.
- `push` is a dry run unless you pass `--apply`; every apply backs up the
  prior remote state first, so you can always see what was there before.
- REPLACE-not-merge: pushes always send the full var set, so the snapshot you
  reviewed is exactly what ends up live; branch-level overrides are handled.

## Database (PostgreSQL)

Optional second capability: run queries against an RDS PostgreSQL database
with the same no-leak discipline (results go to private files, sensitive
values are entered via your editor, writes are gated behind two flags).

```bash
aws-admin db set-password        # one-time: store the DB password (encrypted; hidden prompt)
aws-admin db check               # connectivity/auth smoke test
aws-admin db list                # show the built-in, read-only queries
aws-admin db run unverified-users           # results → private file, summary only
aws-admin db run user-count --show          # opt into inline output for safe results
aws-admin db run ./my.sql                    # run your own SQL file
aws-admin db run ./change.sql --write        # preview a write (runs, then rolls back)
aws-admin db run ./change.sql --write --commit   # actually persist the write
aws-admin db clean-results       # delete saved result files
```

- Results are written to `~/.config/aws-admin/results/<name>-<timestamp>.csv`
  (owner-only file mode); only a summary is printed. Use `--show` for
  non-sensitive results like counts.
- SQL files may contain `{{NAME}}` placeholders; the tool opens your editor to
  fill them in and binds them as query parameters, so the values never enter
  your shell history or the database logs.
- Connections are read-only by default; writes need `--write` (a rolled-back
  preview) and then `--write --commit` to take effect.

See [docs/usage.md](docs/usage.md) for the full beginner-friendly walkthrough.
