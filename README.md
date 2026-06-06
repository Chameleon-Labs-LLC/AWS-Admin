# AWS-Admin

Secure local CLI for AWS admin tasks. First capability: managing Amplify
environment variables **without secret values ever entering a prompt, transcript,
or shell command**.

## Why
`aws amplify get-app --query environmentVariables` prints secrets straight into the
conversation. This tool keeps Amplify as the source of truth, mirrors env vars into an
encrypted local snapshot, and only ever emits key names + redacted digests.

## Install
```bash
python3 -m venv .venv_linux
.venv_linux/bin/pip install -e ".[dev]"
```

After installing, copy the example config and add your AWS values — see
[First-time setup](docs/usage.md#first-time-setup) for details.

## Workflow
```bash
aws-admin env pull my        # mirror MyApp2's env vars into the encrypted snapshot
aws-admin env edit my        # change values in $EDITOR (you type them, not the model)
aws-admin env push my        # dry-run: key-only diff of what would change
aws-admin env push my --apply --redeploy   # send full set + redeploy
aws-admin env diff my        # compare local snapshot vs live
aws-admin env redeploy my    # start a RELEASE job
```

Apps: `eo` ExampleOrg, `ab` AppBeta, `aa` AppAlpha, `ag` AppGamma, `my` MyApp2.

## Where state lives
`~/.config/aws-admin/` (outside the repo, outside Dropbox): `vault.key` (0600),
`vaults/<app>.enc`, `backups/<app>-<ts>.enc`. AWS auth uses your default `~/.aws` profile.

## Security model
- Secret values never appear in command output (single redaction chokepoint, regression-tested).
- No `--set KEY=VALUE` flags (would land in shell history); new values come only via `edit`.
- `push` is dry-run unless `--apply`; every apply backs up the prior remote state first.
- REPLACE-not-merge: pushes always send the full var set; branch-level overrides handled.

## Database (PostgreSQL)
```bash
aws-admin db set-password        # store the DB password (encrypted; hidden prompt)
aws-admin db check               # connectivity/auth smoke test
aws-admin db list                # curated read-only queries
aws-admin db run unverified-users           # results → file, redacted summary
aws-admin db run user-count --show          # opt into inline output for safe results
aws-admin db run ./my.sql                    # run your own SQL file
aws-admin db run ./change.sql --write        # preview a write (rolls back)
aws-admin db run ./change.sql --write --commit   # persist
aws-admin db clean-results       # delete saved result files
```

- Results write to `~/.config/aws-admin/results/<name>-<ts>.csv` (0600); only a
  key-only summary is printed. Use `--show` for non-sensitive results.
- SQL files may contain `{{NAME}}` placeholders; the tool opens your editor to fill
  them and binds them as query parameters (values never enter the prompt or DB logs).
- Read-only by default; writes need `--write` (preview) then `--write --commit`.
