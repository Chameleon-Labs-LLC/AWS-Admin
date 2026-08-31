# AWS-Admin — agent rules

`aws-admin` is a local CLI for AWS admin tasks (Amplify env vars, RDS queries, cost reports). Its entire reason for existing: **secret values must never appear in command output, prompts, transcripts, or shell history.** Every design decision serves that invariant. Do not add any flag, log line, error message, or return value that emits a secret.

## Hard rules

- `tests/test_no_value_leak.py` and `tests/test_db_no_value_leak.py` assert no command output ever contains a secret value. Any new command or output path needs equivalent coverage.
- No `--set KEY=VALUE`-style flags ever; new values enter only via `env edit` (editor) or hidden prompts.
- `env push` is dry-run unless `--apply`; every apply backs up prior remote state to `backups/<app>-<ts>.enc` first. Pushes are REPLACE-not-merge: the full var set is always sent, app-level and branch-level handled separately.
- `db run` is read-only by default; writes require `--write` (preview, rolls back) then `--write --commit`.
- `src/aws_admin/redact.py` is the **single chokepoint** for rendering secret-bearing data. All diffs/summaries route through here and emit key names only. Never format env-var data for output anywhere else.
- Sensitive deployment values (account ID, app IDs, DB host) load at runtime from `$AWS_ADMIN_HOME/config.toml`; nothing sensitive is hardcoded.
- No test touches real AWS, the real DB, or the real `~/.config/aws-admin/`. The autouse `isolated_home` fixture points `AWS_ADMIN_HOME` at a tmp dir with a synthetic `config.toml`; keep every test inside it.

## Gotchas

- Venv naming: public docs (README, `docs/usage.md`, anything an outside user reads) always say `.venv`. `.venv_linux` is Leland's local WSL name for local commands and local tooling (`slash-commands/`) only. The one exception is a doc that covers Linux and Windows with platform-dependent branches.
- A new curated query needs two steps: drop the `.sql` file in `src/aws_admin/queries/` and register it in the `CURATED` dict in `src/aws_admin/db/queries.py`. The file alone does nothing.
- `bin/aws-admin` is a self-healing launcher: it creates the venv and reinstalls only when `pyproject.toml` changes. Run the CLI through it, not through a bare `python -m`.

## Conventions

- Command functions take an injectable `client=` / connection and return user-facing strings (key names only). Follow that pattern for new code; test with the fakes in `tests/conftest.py`.
- Keep `cli.py` as argparse dispatch only; logic lives in `src/aws_admin/commands/`.

## Pointers

- Usage and operator runbook: `docs/usage.md`.
- ChameleonLabs AWS rules this tool enforces (env vars, RDS, schema-change approval): `~/.claude/instructions/aws-chameleonlabs.md`.
