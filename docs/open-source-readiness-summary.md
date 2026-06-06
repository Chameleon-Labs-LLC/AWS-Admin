# Open-Source Readiness — Completion Summary

**Date:** 2026-06-01
**Branch:** merged to `master` (feature branch `feat/open-source-readiness` deleted)
**Spec:** `docs/superpowers/specs/2026-06-01-open-source-readiness-design.md`
**Plan:** `docs/superpowers/plans/2026-06-01-open-source-readiness.md`

## Why

Audit question: "Is there any PII in this repo? Can we open-source it?"

- **PII:** None. No credentials, no customer data. Secrets live in `~/.config/aws-admin/`
  and are gitignored; the only email-like strings in the tree are synthetic test
  fixtures (`a@b.com`).
- **Security scan** (GuardDog / Trivy / OSV / Semgrep): zero first-party findings.
  All GuardDog indicators were benign false positives inside the gitignored
  `.venv_linux/` dependency tree.
- **Blocker:** the tracked source hardcoded a live AWS infrastructure inventory —
  account ID, RDS endpoint host, DB name, and five Amplify app IDs — in
  `config.py`, docs, and tests, and in git history. Not credentials, but a precise
  recon map. Product *names* (ExampleOrg, AppBeta, AppAlpha, AppGamma,
  MyApp2) are public and were intentionally kept.

## What changed

1. **Config externalized** (`src/aws_admin/config.py`): account ID, RDS host, DB
   name, and the Amplify app map now load at runtime from
   `$AWS_ADMIN_HOME/config.toml` (default `~/.config/aws-admin/config.toml`) via
   `tomllib`, with per-key env overrides (`AWS_ADMIN_ACCOUNT_ID`,
   `AWS_ADMIN_DB_HOST`, `AWS_ADMIN_DB_NAME`). PEP 562 `__getattr__` keeps every call
   site unchanged. Generic non-sensitive constants (`REGION`, `DEFAULT_BRANCH`,
   `DB_PORT`, `DB_USER`, `DB_SSLMODE`) stay in-module.
2. **`config.example.toml`** shipped with placeholders; `config.toml` and
   `.scan-reports/` added to `.gitignore`; setup step documented in
   `docs/usage.md` and `README.md`.
3. **Tests:** an autouse fixture seeds each test's `AWS_ADMIN_HOME` with a synthetic
   `config.toml` (real public app names, fake IDs/account/host/db-name). Five test
   files updated; no real identifier remains in `tests/`.
4. **Docs scrubbed:** four `2026-05-30` plan/spec docs had real identifiers replaced
   with placeholders.
5. **Git history rewritten** with `git filter-repo --replace-text`: all 55 commits,
   the 8 real literals → `REDACTED_*` tokens. Verified: 0 real values remain in blob
   contents or commit messages across all refs (was 390 blob hits). The replacements
   file held the only copy of the real literals, lived outside the repo, and was
   `shred`-deleted; dangling pre-rewrite objects pruned.

## Verification

- `pytest`: 105 passed (before and after the history rewrite).
- Working tree: zero real identifiers in any tracked file.
- pyright: 11 errors — all pre-existing `Optional`-subscript patterns in test files
  on `master` (unrelated to this work); zero new errors introduced.

## The maintainer's local config

Your real values live in `~/.config/aws-admin/config.toml` (gitignored, outside the
repo). Daily slash commands (`/aws-env-*`, `/aws-db-*`) keep working unchanged.
Backup of pre-rewrite history: `../AWS-Admin-prebackup-<timestamp>.bundle`.

## Remaining manual step (force-push)

`filter-repo` removed the `origin` remote and rewrote every commit SHA. To publish
the cleaned history to the (currently private) GitHub repo:

```bash
git remote add origin git@github.com:your-org/AWS-Admin.git
git push --force origin master
```

### Caveats before going public
- **Other clones** still contain the old history (e.g. any second working copy, or
  the private GitHub repo before the force-push). Re-clone or hard-reset them.
- **GitHub retains unreachable commits** for a while after a force-push (old SHAs may
  remain reachable by direct URL until GitHub gc's them). The repo is private, so the
  exposure is limited to org members. For maximum assurance before flipping to
  public, consider deleting and recreating the GitHub repo from the cleaned local
  history rather than relying on force-push + gc.
- The exposed identifiers are **not credentials**, so no rotation is required.
