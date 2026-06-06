# DB Subcommand Group — Design

**Date:** 2026-05-30
**Status:** Approved (design); pending implementation plan
**Author:** Leland Green (with Claude)
**Builds on:** the `aws-admin` CLI (see `2026-05-30-secure-amplify-secrets-cli-design.md`)

## Problem

Admin database work today means `psql` against the multi-tenant RDS PostgreSQL
instance. Two exposure risks repeat the env-var problem:

1. **Query results** (from `users`, `verification_tokens`, etc.) contain PII and
   secrets (emails, tokens, password hashes); dumping them into the conversation
   re-creates the leak we built the tool to avoid.
2. **Sensitive literals in SQL** (a token to look up, a value to set) would pass
   through the prompt/shell if typed inline.

The DB password itself must also never transit a prompt.

## Goal

Add a `db` command group to `aws-admin` for PostgreSQL admin work where:
- the DB password is stored encrypted and never echoed,
- query *results* default to a local file (only a redacted summary reaches the
  conversation),
- sensitive SQL literals are supplied by the human via an editor buffer and bound
  as query parameters — never interpolated into SQL text or shown in the prompt,
- writes are gated (read-only by default).

## Key Decisions

| Decision | Choice |
|---|---|
| Operations | Curated read-only named queries + a file-fed SQL runner |
| Sensitive literals | `{{NAME}}` placeholders filled in a shredded `$EDITOR` buffer, bound as params |
| DB password | Stored Fernet-encrypted in the vault (`db set-password`) |
| Result handling | File by default (0600 CSV) + redacted summary; `--show` opt-in inline |
| Write capability | Read-only default → `--write` previews (rollback) → `--write --commit` persists |
| Structure | New `db` group in the existing `aws-admin` package |
| Driver | `psycopg` v3 (named-parameter binding) |

## Architecture & Layout

```
src/aws_admin/
  db/
    __init__.py
    connection.py     # DSN from config + password from vault; psycopg connect; sslmode=require
    runner.py         # execute SQL with bound params; read-only / --write / --commit modes
    placeholders.py   # detect {{NAME}} tokens; collect values via shredded $EDITOR buffer; bind
    results.py        # write rows to a 0600 file; build redacted summary; --show inline
    queries.py        # registry: name -> {file, description, write=False}
  queries/            # curated read-only .sql files shipped with the package
    unverified-users.sql
    verification-tokens.sql
    user-count.sql
  commands/db.py      # set-password / check / list / run
  vault.py            # + set_db_password / get_db_password (reuse encrypt + _write_private)
  config.py           # + DB_HOST/DB_PORT/DB_NAME/DB_USER, DB_SSLMODE
  cli.py              # + `db` subparser group
```

New dependency: `psycopg[binary]>=3.1` (well-established; satisfies the 7-day
minimum-age rule). DB connection facts (host `your-db.xxxxxxxxxxxx.us-east-1.rds.amazonaws.com`,
port 5432, db `your_db`, user `postgres`, `sslmode=require`) live in
`config.py` — none are secret.

## Connection & Password

- `aws-admin db set-password` — `getpass` hidden-input prompt in the user's
  terminal; stores the password Fernet-encrypted at
  `~/.config/aws-admin/db-password.enc` (written via `vault._write_private`, 0600).
  Never echoed. `/aws-db-set-password` instructs the user to run it via `!`.
- `connection.py` reads the encrypted password at connect time and connects with
  `sslmode=require`. Connection failures surface as one-line errors (no password,
  no stack trace).
- `aws-admin db check` — connectivity/auth smoke test; prints only
  `connected as postgres@<host>/<db> (read-only)`.

## Curated Named Queries

- Read-only `.sql` files in `src/aws_admin/queries/`, plus a registry in
  `db/queries.py` mapping `name -> {file, description, write=False}`.
- Seeded from existing usage:
  - `unverified-users` — `SELECT id, email, name, "emailVerified", "createdAt" FROM users WHERE "emailVerified" IS NULL ORDER BY "createdAt" DESC;`
  - `verification-tokens` — `SELECT id, email, type, "createdAt", "expiresAt", "usedAt" FROM verification_tokens ORDER BY "createdAt" DESC LIMIT 10;`
  - `user-count` — `SELECT count(*) AS users FROM users;`
- `aws-admin db list` → names + descriptions. `aws-admin db run <name>` executes.
- Curated queries are always read-only; their results follow the result-handling
  policy (so `unverified-users`/`verification-tokens`, which return PII, write to
  file by default).

## File-Fed Runner & Placeholders

- `aws-admin db run <file.sql>` runs an arbitrary SQL file.
- **Placeholder syntax is `{{NAME}}`** (chosen to avoid clashing with SQL `%`,
  `:`, and `::type` casts). When a query or file contains `{{NAME}}` tokens:
  1. the tool opens a values buffer in `$EDITOR` (RAM-backed `/dev/shm` when
     available, mode 0600, shredded in a `finally` — the same machinery as
     `env edit`), pre-populated with one `NAME=` line per distinct placeholder;
  2. the user fills values and saves;
  3. the runner translates each `{{NAME}}` to psycopg's `%(name)s` and executes
     with a bound-params dict — values never enter the SQL text, the shell, the
     conversation, or DB server logs.
- Files with no placeholders run directly. Because a placeholder run opens the
  editor, it happens in the user's terminal (like `env edit`); a no-placeholder
  run is automatable.
- Placeholder names: `[A-Za-z_][A-Za-z0-9_]*`. A `{{NAME}}` left unfilled (blank
  value) aborts the run with an error (no implicit NULL binding).

## Read-Only / Write Modes

Three levels (mirrors `env push`'s dry-run/`--apply`):
- **default** — runs inside a `READ ONLY` transaction (psycopg `conn.read_only =
  True`); any write statement fails safely.
- **`--write`** — runs in a normal transaction, reports affected-row counts per
  statement, then **rolls back**: a real preview of an `UPDATE`/`DELETE` without
  persisting.
- **`--write --commit`** — persists the transaction.

Curated queries are always read-only (registry `write=False`); `--write` is only
honored for file-fed runs. `--commit` without `--write` is rejected.

## Result Handling

- Rows write to `~/.config/aws-admin/results/<name-or-file>-<ts>.csv`, mode 0600,
  on the ext4 filesystem (real perms), and the `results/` dir is gitignored.
- Default stdout is a **redacted summary**: `<N> rows, columns: [a, b, c],
  written to <path>`. No row values reach the conversation.
- `--show` prints rows inline when the user knows the result is non-sensitive
  (e.g. `user-count`).
- `aws-admin db clean-results` purges the results directory.
- At-rest: result files are plaintext-but-0600 (the user opens them directly).
  The primary threat addressed is prompt exposure; encryption-at-rest for results
  is a possible future toggle, out of scope here.

## CLI & Slash Commands

- CLI: `aws-admin db set-password | check | list | run`. `run` takes a name or a
  file path, plus `--write`, `--commit`, `--show`.
- Slash commands (thin wrappers, same redaction rules):
  - `/aws-db-list` → `aws-admin db list`
  - `/aws-db-run <name-or-file>` → `aws-admin db run …` (reports the redacted summary)
  - `/aws-db-set-password` → instructs the user to run `! aws-admin db set-password`

## Testing

- A fake psycopg connection/cursor (like `FakeAmplify`) records calls and returns
  canned rows — no live DB in the suite.
- Coverage: read-only enforcement (a write in read-only mode raises), `--write`
  rollback vs `--write --commit` persist (verified via recorded commit/rollback),
  `{{NAME}}` → `%(name)s` translation + params dict, unfilled-placeholder abort,
  results-to-file + redacted summary, `--show` inline, and the **no-value-leak**
  property (summary/logs never contain row values, placeholder values, or the
  password).
- One opt-in live `db check` (`--run-live`, off by default).

## Out of Scope

- Schema migrations / DDL management.
- Per-tenant abstractions — queries stay global; tenant filters can be added
  per-query later via `{{ }}` placeholders.
- Encryption-at-rest for result files (possible future toggle).
- Connection pooling (single short-lived connection per command is fine).
