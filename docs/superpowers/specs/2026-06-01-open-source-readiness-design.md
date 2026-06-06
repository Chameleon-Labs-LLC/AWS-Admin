# Open-Source Readiness — Design

**Date:** 2026-06-01
**Author:** Leland Green (with Claude)
**Status:** Proposed — awaiting review

## Problem

`aws-admin` should be publishable as open source. It contains **no credentials and
no customer PII** (secrets live in `~/.config/aws-admin/`, guarded by `.gitignore`;
the only email-like strings in the tree are synthetic test fixtures such as
`a@b.com`). However, the tracked source hardcodes a **live AWS infrastructure
inventory** that must not be public:

| Identifier | Location |
|---|---|
| AWS account ID | `src/aws_admin/config.py`, docs, `tests/` |
| RDS endpoint hostname | `src/aws_admin/config.py`, docs |
| Database name | `src/aws_admin/config.py`, docs |
| Amplify app IDs (5 apps) | `src/aws_admin/config.py`, docs, `tests/` |

These are not credentials, but they are a precise attack/recon map (a resolvable DB
host plus `postgres` user and DB name; account ID for IAM ARN guessing and social
engineering). They are also present in **committed git history**, so scrubbing the
working tree alone is insufficient.

A security scan (GuardDog / Trivy / OSV / Semgrep) found **no first-party findings**:
all GuardDog indicators were benign false positives inside the gitignored
`.venv_linux/` dependency tree (e.g. boto3 `getattr` dispatch). No malware, no
first-party secrets.

### Product-name decision
`MyApp2` is a publicly-announced product (as are AppAlpha, AppBeta,
AppGamma, ExampleOrg). Therefore **only app IDs are sensitive** — product
names are not redacted.

## Goals

1. Remove the live infrastructure inventory from all tracked code, docs, and tests.
2. Keep the tool working **identically** for daily use via the existing slash
   commands (zero behavior change for the maintainer).
3. Make the tool **more reusable**: any user can point it at their own AWS account
   via local config.
4. Scrub the sensitive identifiers from git history before the repo goes public.

## Non-Goals

- No change to the secrets/vault model (already correct).
- No change to command behavior, flags, or output.
- No redaction of product names (they are public).

## Design

### Part 1 — Externalize config (`src/aws_admin/config.py`)

**Stays as plain module constants** (non-sensitive generics — no reason to
externalize): `REGION`, `DEFAULT_BRANCH`, `DB_PORT`, `DB_USER`, `DB_SSLMODE`.

**Moves to a loaded settings file**: `account_id`, `db.host`, `db.name`, and the
app-alias map (`_APPS`).

**Loader.** A cached `_load_settings()` reads `state_dir()/config.toml` (Python
3.12 `tomllib`, stdlib — no new dependency). It honors `AWS_ADMIN_HOME` (so it lives
beside the vault and tests stay isolated) and applies per-key environment overrides:
`AWS_ADMIN_ACCOUNT_ID`, `AWS_ADMIN_DB_HOST`, `AWS_ADMIN_DB_NAME`. A missing file
raises a clear error naming `config.example.toml`. The cache is keyed on the resolved
`state_dir()` path so that per-test `AWS_ADMIN_HOME` overrides are not cross-cached.

**Backward-compatible access via PEP 562.** A module-level `__getattr__` resolves
`config.ACCOUNT_ID`, `config.DB_HOST`, and `config.DB_NAME` lazily from the loaded
settings, so **every existing call site is unchanged** (`aws_client.py`,
`db/connection.py`, `commands/db.py`, `commands/env.py`). `resolve_app()` and
`known_apps()` read the loaded app map instead of a module-level dict.

Note: `ACCOUNT_ID` is not consumed anywhere in `src/` today; only `DB_HOST`,
`DB_NAME`, and the app IDs (via `resolve_app`) are. This keeps the change small.

`config.toml` schema (the shipped `config.example.toml` uses placeholders):

```toml
account_id = "123456789012"

[database]
host = "your-db.xxxxxxxx.us-east-1.rds.amazonaws.com"
name = "your_db"

[apps.MyApp]
app_id = "d0000000000000"
aliases = ["my"]

[apps.AnotherApp]
app_id = "d0000000000001"
aliases = ["other"]
```

### Part 2 — New tracked file: `config.example.toml`

Generic placeholders only (the block above). No real business identifiers. The
README/usage docs gain a one-line "copy `config.example.toml` to
`~/.config/aws-admin/config.toml` and fill in your values" step.

### Part 3 — `.gitignore`

Add `config.toml` (currently only `config.json` is listed). The encrypted state
patterns already cover the vault.

### Part 4 — Maintainer's real local config

Create `~/.config/aws-admin/config.toml` (gitignored, outside the repo) holding the
real account ID, RDS host, DB name, and full app inventory, so the maintainer's
daily slash commands keep working with zero behavior change. This file is never
committed.

### Part 5 — Scrub tracked tests and docs

- `tests/conftest.py`: the autouse `isolated_home` fixture additionally writes a
  **synthetic** `config.toml` into each test's `AWS_ADMIN_HOME` (fake account
  `000000000000`, host `db.example.invalid`, name `example_db`, generic apps
  `AppOne`/`a1`, `AppTwo`/`a2`). No real identifier appears anywhere in `tests/`.
- Rewrite `tests/test_config_paths.py`, `tests/test_config_db.py`, and
  `tests/test_config_resolve.py` to assert the synthetic values and generic apps.
- Replace real identifiers with placeholders in tracked docs:
  `docs/implementation-summary.md`, the plan/spec markdown under
  `docs/superpowers/`. This design doc uses placeholders only.

### Part 6 — History rewrite (maintainer runs the force-push)

After Parts 1–5 are committed and the suite is green:

1. Write `replacements.txt` mapping each sensitive literal to a redaction token:
   ```
   <ACCOUNT_ID>==>REDACTED_ACCOUNT_ID
   <RDS_HOST>==>REDACTED_DB_HOST
   <APP_ID_1>==>REDACTED_APP_ID
   ... (all 5 app IDs)
   ```
   (Product names are not redacted.)
2. `git filter-repo --replace-text replacements.txt` rewrites every commit
   (including HEAD).
3. Verify: `git log --all -p | grep -F` finds zero occurrences of any real value.
4. Hand the maintainer the force-push and re-add-remote steps (`filter-repo` removes
   `origin` by design; old refs are discarded).

## Verification

- **Part 1–5:** full `pytest` suite green; `git grep` over the tracked tree finds no
  real account ID, RDS host, or app ID.
- **Part 6:** `git log --all -p | grep -F <each value>` returns nothing.

## Risks & Mitigations

- **Tool breaks for maintainer** → mitigated by creating the real local
  `config.toml` (Part 4) and running the suite before any history rewrite.
- **`filter-repo` is destructive / drops remote** → run only after Parts 1–5 land;
  document the re-add-remote + force-push steps; the maintainer executes them.
- **Stale config cache across tests** → cache keyed on resolved `state_dir()`.
- **Missing config at runtime** → explicit error pointing at `config.example.toml`.
