# `env rotate` — design

Date: 2026-06-15
Status: Approved (ready for implementation plan)

## Purpose

Rotate a single shared secret (e.g. `AI_STREAM_SECRET`) across several Amplify
apps in one step, entering the new value exactly **once**, without ever exposing
the value in output. The command operates **entirely on local encrypted
snapshots and never calls AWS** — the operator pulls beforehand and pushes
afterward.

Motivating workflow:

```
aws-admin env pull all                       # operator does this beforehand
aws-admin env rotate AI_STREAM_SECRET cl hc qs
aws-admin env push <app> --apply             # operator does this afterward (or 'all')
```

## Command surface

```
aws-admin env rotate <NAME> <app...|all>
```

- `<NAME>` — the env-var key to rotate, e.g. `AI_STREAM_SECRET`.
- `<app...>` — one or more app tokens (acronym / name / app id), **or** the
  single token `all` to target every configured app.

Implemented as a new `rotate` subparser in `cli.py`:

- positional `name`
- positional `apps` with `nargs="+"`
- shares the `apps_epilog` help block used by the other env subcommands
- **no** `--apply` flag and **no** AWS client — this command never touches AWS.

`rotate` does **not** go through `_dispatch_env_app` (that helper is for
single-token commands). It owns its own list/`all` expansion.

## Flow (all local, no AWS)

1. **Resolve targets.** If any token is `all`, expand to `config.known_apps()`;
   otherwise `config.resolve_app()` each token. Dedupe by canonical name,
   preserving first-seen order. An unknown token raises `UnknownAppError`
   (existing behavior, exit 1).

2. **Load snapshots, fail-fast.** Load each targeted app's snapshot. If **any**
   targeted app has no snapshot, abort the whole command before making changes:

   ```
   No local snapshot for: hc, qs. Run 'aws-admin env pull <app>' first.
   ```

   (Raised as `FileNotFoundError`, caught by `main()` → exit 1.) Rationale: a
   missing file means the operator has not pulled yet; per "if the local file
   exists, assume it's current," we never half-rotate.

3. **Plan per app.** For each app, find every occurrence of `NAME` across both
   `app_level` and `branch_level`. For apps where `NAME` is **missing**,
   interactively prompt (default No):

   ```
   Add AI_STREAM_SECRET to qs (app-level)? [y/N]
   ```

   The confirm function is injectable for testing (mirrors the injectable
   `_open_editor` / `client=` pattern already in the codebase). Yes → planned as
   a new **app-level** key. No → app recorded as skipped (snapshot untouched).

4. **Capture value once.** Open the RAM-backed, shred-after editor buffer
   (`vault.edit_buffer`) with a blank template:

   ```
   # Enter the new value for AI_STREAM_SECRET after '='. Save & exit.
   # Writes to local snapshots only: cl, hc, qs
   # Empty value = abort, no changes.
   AI_STREAM_SECRET=
   ```

   A small single-key parser reads the substring after the first `=` on the
   `NAME=` line. **Empty value → abort with zero writes.**

5. **Apply.** For each planned app whose value would actually change: back up the
   pre-rotation snapshot (`vault.backup_snapshot`), set `NAME` to the new value
   at **every** planned level (so a stale branch-level override can't survive),
   and save. Apps where the new value equals the current value are left
   untouched (no backup, no save).

6. **Report & hand off.** Print a key-only summary and remind the operator to
   push (see below). `rotate` never pushes or redeploys.

## Output (value never appears)

```
Rotated AI_STREAM_SECRET in 2 app(s):
  cl: app-level updated
  hc: app-level + branch-level updated
Added (was missing): qs (app-level)
Skipped (declined add): (none)
Unchanged (same value): (none)

Next: aws-admin env push <app> --apply   (or 'all') to send + redeploy.
```

All lines are composed from key names and app names only. The entered value
exists only in (a) the RAM-backed edit buffer, which is shredded in a `finally`,
and (b) the in-memory snapshot dict, which is written back Fernet-encrypted.

## Security invariants

- The rotated value must never appear in any return value, print, prompt, or
  error message — consistent with the project's core invariant.
- `tests/test_no_value_leak.py` (and/or a sibling) gains a `rotate` case
  asserting the entered value never appears in the command's return string.

## Testing

Unit tests (using the existing `isolated_home` fixture, injected confirm +
editor callables, no AWS / no real `~/.config`):

- `all` expands to every configured app; explicit token lists resolve + dedupe.
- Missing snapshot for any targeted app aborts the whole command (no writes).
- Decline-add leaves that app's snapshot untouched and lists it under "Skipped".
- Accept-add creates `NAME` at app-level in the previously-missing app.
- Empty value entry aborts with zero writes and zero backups.
- A key present at both app-level and branch-level is updated at both levels.
- A backup file is written for each app that actually changes; apps with an
  identical value get no backup and no save.
- No-value-leak: the entered value never appears in the return string.

## Alternatives considered (rejected)

- **Editor-first, then prompt for adds.** Rejected: prompting for adds *before*
  the single value entry means the target set is fully known when the operator
  types the value.
- **Skip missing snapshots and rotate the rest.** Rejected in favor of
  fail-fast, so the operator never gets a half-done rotation to reason about.
