# env rotate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `aws-admin env rotate <NAME> <app...|all>` — set one shared secret to a new value across several apps' local snapshots in a single editor entry, with no AWS calls.

**Architecture:** A new `env.rotate()` command orchestrates three helpers: `config.resolve_apps()` (token list → AppRefs, with `all` expansion + dedupe), `vault.capture_value()` (blank shred-after editor buffer → the new value), and the existing `vault.backup_snapshot` / `vault.save_snapshot`. Missing-key adds are gated by an injectable confirm callable; the value is captured once and written to every level (app + branch) where the key lives. Output is key/app names only.

**Tech Stack:** Python 3.12, argparse, pytest, Fernet vault (existing). No new dependencies.

**Conventions (from the codebase):**
- Run tests with `.venv_linux/bin/python -m pytest` (pyproject sets `pythonpath=src`, `testpaths=tests`).
- The autouse `isolated_home` fixture (in `tests/conftest.py`) points `AWS_ADMIN_HOME` at a tmp dir seeded with synthetic apps: `AppAlpha`(aa), `AppBeta`(ab), `AppGamma`(ag), `ExampleOrg`(eo), `MyApp2`(my). `known_apps()` returns them case-insensitively sorted: `AppAlpha, AppBeta, AppGamma, ExampleOrg, MyApp2`.
- Command functions take injectable callables (`client=`, `_open_editor=`) so tests never touch AWS, the real DB, or real `~/.config`.

---

## File Structure

- **Modify** `src/aws_admin/config.py` — add `resolve_apps(tokens)`.
- **Modify** `src/aws_admin/vault.py` — add `capture_value(key, target_names, _open_editor=...)` and a private `_parse_single_value(text, key)`.
- **Modify** `src/aws_admin/commands/env.py` — add `rotate(...)`, a private `_default_confirm`, and a private `_format_rotate_summary(...)`.
- **Modify** `src/aws_admin/cli.py` — add the `rotate` subparser and dispatch.
- **Create** `tests/test_config_resolve_apps.py`, `tests/test_vault_capture_value.py`, `tests/test_env_rotate.py`.
- **Modify** `tests/test_cli.py` (parse + dispatch + help), `tests/test_no_value_leak.py` (rotate leak case).
- **Modify** `docs/usage.md` — document the multi-app rotate flow.

---

## Task 1: `config.resolve_apps()` — token list → AppRefs

**Files:**
- Modify: `src/aws_admin/config.py` (add after `resolve_app`, end of file)
- Test: `tests/test_config_resolve_apps.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_config_resolve_apps.py`:

```python
import pytest

from aws_admin import config

# isolated_home (autouse, conftest) seeds these synthetic apps.
_ALL = ["AppAlpha", "AppBeta", "AppGamma", "ExampleOrg", "MyApp2"]


def test_resolve_apps_expands_all():
    assert [r.name for r in config.resolve_apps(["all"])] == _ALL


def test_resolve_apps_all_is_case_insensitive():
    assert [r.name for r in config.resolve_apps(["ALL"])] == _ALL


def test_resolve_apps_all_anywhere_in_list_wins():
    assert [r.name for r in config.resolve_apps(["my", "all"])] == _ALL


def test_resolve_apps_resolves_and_dedupes_preserving_order():
    # 'my' and 'MyApp2' are the same app; 'aa' is AppAlpha.
    refs = config.resolve_apps(["my", "aa", "MyApp2"])
    assert [r.name for r in refs] == ["MyApp2", "AppAlpha"]


def test_resolve_apps_unknown_token_raises():
    with pytest.raises(config.UnknownAppError):
        config.resolve_apps(["definitely-not-an-app"])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv_linux/bin/python -m pytest tests/test_config_resolve_apps.py -v`
Expected: FAIL with `AttributeError: module 'aws_admin.config' has no attribute 'resolve_apps'`

- [ ] **Step 3: Implement `resolve_apps`**

Append to `src/aws_admin/config.py`:

```python
def resolve_apps(tokens: list[str]) -> list[AppRef]:
    """Resolve a list of app tokens to AppRefs.

    If any token is 'all' (case-insensitive), returns every known app (sorted).
    Otherwise resolves each token via resolve_app() and dedupes by canonical
    name, preserving first-seen order. Unknown tokens raise UnknownAppError.
    """
    if any(t.strip().lower() == "all" for t in tokens):
        return known_apps()
    seen: dict[str, AppRef] = {}
    for token in tokens:
        ref = resolve_app(token)
        seen.setdefault(ref.name, ref)
    return list(seen.values())
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv_linux/bin/python -m pytest tests/test_config_resolve_apps.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add src/aws_admin/config.py tests/test_config_resolve_apps.py
git commit -m "feat(config): resolve_apps() for token lists with 'all' expansion"
```

---

## Task 2: `vault.capture_value()` — capture one new value via the shred-after editor

**Files:**
- Modify: `src/aws_admin/vault.py` (add after `edit_buffer`, before `edit_app_buffer`)
- Test: `tests/test_vault_capture_value.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_vault_capture_value.py`:

```python
from aws_admin import vault


def test_capture_value_reads_value_after_equals():
    def editor(path):
        # User types the new value after the 'KEY=' on the key line.
        path.write_text(path.read_text().replace("AI_SECRET=", "AI_SECRET=new_val"))

    assert vault.capture_value("AI_SECRET", ["cl", "hc"], _open_editor=editor) == "new_val"


def test_capture_value_empty_when_left_blank():
    def editor(path):
        pass  # leave the template's empty 'AI_SECRET=' as-is

    assert vault.capture_value("AI_SECRET", ["cl"], _open_editor=editor) == ""


def test_capture_value_preserves_equals_in_value():
    def editor(path):
        path.write_text("AI_SECRET=a=b=c\n")

    assert vault.capture_value("AI_SECRET", ["cl"], _open_editor=editor) == "a=b=c"


def test_capture_value_ignores_comment_and_blank_lines():
    def editor(path):
        path.write_text("# a comment with AI_SECRET= in it\n\nAI_SECRET=real\n")

    assert vault.capture_value("AI_SECRET", ["cl"], _open_editor=editor) == "real"


def test_capture_value_shreds_buffer():
    captured = {}

    def editor(path):
        captured["path"] = path
        path.write_text("AI_SECRET=x\n")

    vault.capture_value("AI_SECRET", ["cl"], _open_editor=editor)
    assert not captured["path"].exists()  # shredded + unlinked
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv_linux/bin/python -m pytest tests/test_vault_capture_value.py -v`
Expected: FAIL with `AttributeError: module 'aws_admin.vault' has no attribute 'capture_value'`

- [ ] **Step 3: Implement `capture_value` + `_parse_single_value`**

In `src/aws_admin/vault.py`, add these two functions immediately after `edit_buffer` (around line 167) and before `edit_app_buffer`:

```python
def _parse_single_value(text: str, key: str) -> str:
    """Return the value on the first non-comment ``key=...`` line, else "".

    Splits on the first '=' (so values may contain '='); strips a trailing CR
    only, matching parse_buffer's value handling. Comment/blank lines are skipped.
    """
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if "=" not in line:
            continue
        k, value = line.split("=", 1)
        if k.strip() == key:
            return value.rstrip("\r")
    return ""


def capture_value(key: str, target_names: list[str], _open_editor=_default_open_editor) -> str:
    """Capture a single new value for `key` via the RAM-backed shred-after editor.

    The buffer is blank (no old value is shown). Returns the value typed after
    'key=' , or "" if left empty. The temp buffer is shredded by edit_buffer.
    """
    template = (
        f"# Enter the new value for {key} after '='. Save & exit.\n"
        f"# Writes to local snapshots only: {', '.join(target_names)}\n"
        f"# Empty value = abort, no changes.\n"
        f"{key}=\n"
    )
    text = edit_buffer(template, _open_editor=_open_editor)
    return _parse_single_value(text, key)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv_linux/bin/python -m pytest tests/test_vault_capture_value.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add src/aws_admin/vault.py tests/test_vault_capture_value.py
git commit -m "feat(vault): capture_value() — single new value via shred-after editor"
```

---

## Task 3: `env.rotate()` — the command

**Files:**
- Modify: `src/aws_admin/commands/env.py` (add at end)
- Test: `tests/test_env_rotate.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_env_rotate.py`:

```python
import pytest

from aws_admin import config, vault
from aws_admin.commands import env


def _seed(name, app_level, branch_level=None):
    vault.save_snapshot(name, {
        "app_id": "x", "branch": "main",
        "app_level": dict(app_level), "branch_level": dict(branch_level or {}),
        "pulled_at": "t",
    })


def _types(value):
    """An injectable editor that types `value` as the new secret."""
    def editor(path):
        path.write_text(f"AI_SECRET={value}\n")
    return editor


def _backups_for(name):
    d = config.state_dir() / "backups"
    return list(d.glob(f"{name}-*.enc")) if d.exists() else []


YES = lambda prompt: True
NO = lambda prompt: False


def test_rotate_updates_existing_app_level_key():
    _seed("MyApp2", {"AI_SECRET": "old", "X": "1"})
    out = env.rotate("AI_SECRET", ["my"], confirm=NO, open_editor=_types("new"))
    snap = vault.load_snapshot("MyApp2")
    assert snap["app_level"]["AI_SECRET"] == "new"
    assert snap["app_level"]["X"] == "1"  # untouched
    assert "Rotated AI_SECRET in 1 app(s)" in out
    assert "MyApp2: app-level updated" in out


def test_rotate_updates_both_levels():
    _seed("MyApp2", {"AI_SECRET": "old"}, {"AI_SECRET": "oldbr"})
    out = env.rotate("AI_SECRET", ["my"], confirm=NO, open_editor=_types("new"))
    snap = vault.load_snapshot("MyApp2")
    assert snap["app_level"]["AI_SECRET"] == "new"
    assert snap["branch_level"]["AI_SECRET"] == "new"
    assert "app-level + branch-level updated" in out


def test_rotate_aborts_when_any_snapshot_missing():
    _seed("MyApp2", {"AI_SECRET": "old"})  # AppAlpha has none
    with pytest.raises(FileNotFoundError) as ei:
        env.rotate("AI_SECRET", ["my", "aa"], confirm=YES, open_editor=_types("new"))
    assert "AppAlpha" in str(ei.value)
    assert vault.load_snapshot("MyApp2")["app_level"]["AI_SECRET"] == "old"  # untouched
    assert _backups_for("MyApp2") == []  # nothing written


def test_rotate_declines_add_leaves_app_untouched():
    _seed("MyApp2", {"AI_SECRET": "old"})
    _seed("AppAlpha", {"OTHER": "1"})  # missing AI_SECRET
    out = env.rotate("AI_SECRET", ["my", "aa"], confirm=NO, open_editor=_types("new"))
    assert "AI_SECRET" not in vault.load_snapshot("AppAlpha")["app_level"]
    assert "Skipped (declined add): AppAlpha" in out


def test_rotate_accepts_add_creates_app_level_key():
    _seed("MyApp2", {"AI_SECRET": "old"})
    _seed("AppAlpha", {"OTHER": "1"})
    out = env.rotate("AI_SECRET", ["my", "aa"], confirm=YES, open_editor=_types("new"))
    assert vault.load_snapshot("AppAlpha")["app_level"]["AI_SECRET"] == "new"
    assert "Added (was missing): AppAlpha (app-level)" in out


def test_rotate_empty_value_aborts_no_writes():
    _seed("MyApp2", {"AI_SECRET": "old"})
    out = env.rotate("AI_SECRET", ["my"], confirm=NO, open_editor=_types(""))
    assert vault.load_snapshot("MyApp2")["app_level"]["AI_SECRET"] == "old"
    assert _backups_for("MyApp2") == []
    assert "aborted" in out.lower()


def test_rotate_unchanged_when_same_value_no_backup():
    _seed("MyApp2", {"AI_SECRET": "same"})
    out = env.rotate("AI_SECRET", ["my"], confirm=NO, open_editor=_types("same"))
    assert "Unchanged (same value): MyApp2" in out
    assert _backups_for("MyApp2") == []  # no change => no backup


def test_rotate_backs_up_each_changed_app():
    _seed("MyApp2", {"AI_SECRET": "old"})
    env.rotate("AI_SECRET", ["my"], confirm=NO, open_editor=_types("new"))
    assert len(_backups_for("MyApp2")) == 1


def test_rotate_nothing_to_do_when_all_declined():
    _seed("MyApp2", {"OTHER": "1"})  # missing AI_SECRET, user declines add
    called = {"editor": False}

    def editor(path):
        called["editor"] = True

    out = env.rotate("AI_SECRET", ["my"], confirm=NO, open_editor=editor)
    assert called["editor"] is False  # never prompted for a value
    assert "nothing to rotate" in out.lower()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv_linux/bin/python -m pytest tests/test_env_rotate.py -v`
Expected: FAIL with `AttributeError: module 'aws_admin.commands.env' has no attribute 'rotate'`

- [ ] **Step 3: Implement `rotate` + helpers**

Append to `src/aws_admin/commands/env.py`:

```python
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
    if not new_value:
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
```

Note on the `capture_value` call: passing `_open_editor=None` would override the real default, so the code only forwards `open_editor` when the caller supplied one. Tests always supply it.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv_linux/bin/python -m pytest tests/test_env_rotate.py -v`
Expected: PASS (9 passed)

- [ ] **Step 5: Commit**

```bash
git add src/aws_admin/commands/env.py tests/test_env_rotate.py
git commit -m "feat(env): rotate one shared secret across apps' local snapshots"
```

---

## Task 4: CLI wiring — `rotate` subparser + dispatch

**Files:**
- Modify: `src/aws_admin/cli.py` (subparser block ~line 78-88; dispatch ~line 145-149)
- Test: `tests/test_cli.py` (add tests)

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_cli.py` (after `test_parse_env_push_apply_flag`, and the dispatch/help tests near the other env tests):

```python
def test_parse_env_rotate():
    args = cli.build_parser().parse_args(
        ["env", "rotate", "AI_SECRET", "cl", "hc", "qs"]
    )
    assert args.group == "env"
    assert args.action == "rotate"
    assert args.name == "AI_SECRET"
    assert args.apps == ["cl", "hc", "qs"]


def test_rotate_dispatch_calls_command(monkeypatch, capsys):
    seen = {}

    def fake_rotate(name, apps):
        seen["name"] = name
        seen["apps"] = apps
        return "ROTATED-OK"

    monkeypatch.setattr(_env, "rotate", fake_rotate)
    rc = cli.main(["env", "rotate", "AI_SECRET", "my", "aa"])
    assert rc == 0
    assert seen == {"name": "AI_SECRET", "apps": ["my", "aa"]}
    assert "ROTATED-OK" in capsys.readouterr().out


def test_rotate_help_advertises_all(capsys):
    with pytest.raises(SystemExit):
        cli.main(["env", "rotate", "-h"])
    assert "for every configured app" in _flatten(capsys.readouterr().out)
```

(`_env`, `_flatten`, and `pytest` are already imported in this file.)

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv_linux/bin/python -m pytest tests/test_cli.py -k rotate -v`
Expected: FAIL — `test_parse_env_rotate` errors with `argument action: invalid choice: 'rotate'`

- [ ] **Step 3: Add the subparser**

In `src/aws_admin/cli.py`, immediately after the `push_p` block (after line 88, before `db_p = groups.add_parser(...)`), add:

```python
    rotate_p = actions.add_parser(
        "rotate",
        help="Rotate one shared secret across apps' local snapshots (no AWS; push after)",
        epilog=apps_epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    rotate_p.add_argument("name", help="Env-var key to rotate, e.g. AI_STREAM_SECRET")
    rotate_p.add_argument(
        "apps", nargs="+",
        help=f"One or more app tokens (space-separated), or 'all'. {app_help_all}",
    )
```

- [ ] **Step 4: Add the dispatch**

In `main()`, inside the `if args.group == "env":` block, after the `push` branch (after line 149, before the closing of the env block), add:

```python
            elif args.action == "rotate":
                print(env_cmd.rotate(args.name, args.apps))
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv_linux/bin/python -m pytest tests/test_cli.py -k rotate -v`
Expected: PASS (3 passed)

- [ ] **Step 6: Commit**

```bash
git add src/aws_admin/cli.py tests/test_cli.py
git commit -m "feat(cli): wire 'env rotate' subcommand"
```

---

## Task 5: No-value-leak coverage + docs

**Files:**
- Modify: `tests/test_no_value_leak.py` (add rotate case)
- Modify: `docs/usage.md` (document multi-app rotate)

- [ ] **Step 1: Write the failing leak test**

Add to `tests/test_no_value_leak.py`:

```python
def test_rotate_output_never_contains_value():
    snap = {"app_id": "x", "branch": "main",
            "app_level": {"AI_SECRET": "old"}, "branch_level": {}, "pulled_at": "t"}
    vault.save_snapshot("MyApp2", snap)

    def editor(path):
        path.write_text(f"AI_SECRET={SECRET}\n")

    out = env.rotate("AI_SECRET", ["my"], confirm=lambda p: False, open_editor=editor)
    assert SECRET not in out
    # Stored snapshot stays encrypted — value never lands as plaintext on disk.
    assert SECRET.encode() not in vault.config.vault_path("MyApp2").read_bytes()
```

- [ ] **Step 2: Run it to verify it passes**

Run: `.venv_linux/bin/python -m pytest tests/test_no_value_leak.py::test_rotate_output_never_contains_value -v`
Expected: PASS (the feature already exists from Task 3; this locks in the invariant)

- [ ] **Step 3: Document the multi-app rotate flow**

In `docs/usage.md`, replace the section header line `## Rotating a secret (e.g. a Stripe key)` and its body intro by inserting a new subsection. Specifically, find (around line 172):

```markdown
## Rotating a secret (e.g. a Stripe key)

1. `aws-admin env pull shop` — refresh the local snapshot from Amplify.
```

and insert this block immediately **after** step 4 of that numbered list (after the line ending `start a redeploy so the app picks it up. ...at build time, which is why the redeploy matters.)`) and **before** the `## Branch-level overrides` heading:

```markdown
### Rotating one shared secret across several apps

When the same secret (e.g. `AI_STREAM_SECRET`) is shared by multiple apps, use
`env rotate` to enter the new value **once** and apply it to every app's local
snapshot:

```
aws-admin env pull all                          # refresh local snapshots first
aws-admin env rotate AI_STREAM_SECRET cl hc qs  # or 'all' instead of a list
```

- `rotate` never calls AWS — it only edits your local snapshots, so pull first.
  If any listed app has no snapshot yet, it aborts and tells you which to pull.
- Your editor opens once (RAM-backed, shredded on close) for the new value; it
  is written to every level (app- and branch-level) where the key already
  exists.
- If an app doesn't have the key, you're asked whether to add it (at app-level).
- Each changed snapshot is backed up first, under
  `~/.config/aws-admin/backups/`.
- Output is key/app names only — the value is never shown.

Then push as usual, one app or all:

```
aws-admin env push cl --apply --redeploy
aws-admin env push all --apply --redeploy
```
```

- [ ] **Step 4: Run the full suite**

Run: `.venv_linux/bin/python -m pytest`
Expected: PASS — all prior tests plus the new ones (no regressions).

- [ ] **Step 5: Commit**

```bash
git add tests/test_no_value_leak.py docs/usage.md
git commit -m "test(env): no-value-leak coverage for rotate; docs: multi-app rotate"
```

---

## Self-Review

**Spec coverage:**
- Command surface `env rotate <NAME> <app...|all>` → Task 4. ✓
- No AWS / local-only, fail-fast on missing snapshot → Task 3 (`test_rotate_aborts_when_any_snapshot_missing`). ✓
- `all` expansion + dedupe → Task 1. ✓
- Update every occurrence (app + branch) → Task 3 (`test_rotate_updates_both_levels`). ✓
- Enter value once via shred-after editor → Task 2. ✓
- Interactive y/N add at app-level, injectable → Task 3 (`test_rotate_declines_add...`, `test_rotate_accepts_add...`). ✓
- Empty value aborts → Task 3 (`test_rotate_empty_value_aborts_no_writes`). ✓
- Back up each changed snapshot; skip unchanged → Task 3 (`test_rotate_backs_up_each_changed_app`, `test_rotate_unchanged_when_same_value_no_backup`). ✓
- Key-only output + push hand-off → Task 3 summary + Task 5 leak test. ✓
- Docs → Task 5. ✓

**Placeholder scan:** none — every step has concrete code/commands.

**Type/name consistency:** `resolve_apps` (Task 1) used in Task 3; `capture_value(key, target_names, _open_editor=)` (Task 2) called in Task 3; `rotate(name, app_tokens, *, confirm=, open_editor=)` (Task 3) dispatched in Task 4 as `rotate(args.name, args.apps)`; snapshot dict shape (`app_level`/`branch_level`/`app_id`/`branch`/`pulled_at`) matches `vault`/`env.pull`. Consistent.
