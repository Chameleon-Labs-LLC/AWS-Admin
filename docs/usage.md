# aws-admin usage

`aws-admin` is a command-line tool for two jobs:

1. **Amplify environment variables** — view, edit, and deploy the env vars of an
   AWS Amplify app *without the secret values ever being printed to your
   terminal* (where they could end up in shell history, logs, or an AI-chat
   transcript).
2. **Database admin** — run read-only (and carefully gated read-write) queries
   against your RDS PostgreSQL database, with results written to private files
   instead of your screen.

If you've never used it before, work through this page top to bottom.

## Prerequisites

Before anything else, you need:

- **Python 3.12 or newer** — check with `python3 --version`.
- **An AWS account** with at least one Amplify app (and optionally an RDS
  PostgreSQL instance for the `db` commands).
- **AWS credentials on this machine.** `aws-admin` uses the same credentials
  as the official AWS CLI (the files in `~/.aws/`). If you've never set those
  up:
  1. Install the AWS CLI: <https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html>
  2. Create an access key: AWS Console → click your account name (top right) →
     **Security credentials** → **Create access key**. (If your organization
     uses IAM Identity Center / SSO, use `aws configure sso` instead and ask
     your admin which profile to use.)
  3. Run `aws configure` and paste the key ID and secret when prompted. Set
     the default region to the one your apps live in (e.g. `us-east-1`).
  4. Verify: `aws sts get-caller-identity` should print your account ID, not
     an error.
- **A terminal text editor.** Several commands open your `$EDITOR` so that
  secret values go from your keyboard straight into an encrypted file, never
  through a command line. Check what yours is set to:

  ```bash
  echo $EDITOR
  ```

  If that prints nothing, pick one and add it to your shell profile, e.g.:

  ```bash
  echo 'export EDITOR=nano' >> ~/.bashrc && source ~/.bashrc
  ```

  (`nano` is the friendliest if you don't already know `vim`.)

## Install

From the repository root:

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

This creates a private Python environment in the `.venv` folder and
installs the `aws-admin` command into it. The command lives at
`.venv/bin/aws-admin`. To call it as just `aws-admin`, either activate
the environment first (`source .venv/bin/activate`) or use the full
path — the examples below assume it's on your PATH.

## First-time setup

Copy the example config and fill in your AWS values (the file is gitignored and
never leaves your machine):

```bash
mkdir -p ~/.config/aws-admin
cp config.example.toml ~/.config/aws-admin/config.toml
$EDITOR ~/.config/aws-admin/config.toml
```

`aws-admin` reads `account_id`, the `[database]` host/name, and one
`[apps.<Name>]` table per Amplify app from this file. Generic settings (region,
default branch, DB port/user/sslmode) have built-in defaults.

A filled-in config looks like this:

```toml
account_id = "123456789012"

[database]
host = "mydb.abc123xyz456.us-east-1.rds.amazonaws.com"
name = "myapp_production"

[apps.MyShop]
app_id = "d1a2b3c4d5e6f7"
aliases = ["shop", "ms"]
```

- The table name (`MyShop` above) is the app's display name — use the name
  shown in the Amplify console.
- `aliases` are short nicknames **you invent** so you can type
  `aws-admin env pull shop` instead of the full name. Any of the alias, the
  full name, or the raw app ID works on the command line.
- The reserved token `all` runs `pull`, `redeploy`, or `push` against every
  configured app in turn (each app's result prints as it completes; one app
  failing doesn't stop the rest). `all` is only accepted by those three
  commands — `diff` and `edit` work on a single app.
- Run any command with `-h` (e.g. `aws-admin env pull -h`, or just
  `aws-admin -h`) to see the list of configured apps and their aliases.

### Where to find the required values

You need three things: your **AWS account ID**, the **RDS endpoint + database
name**, and the **Amplify app ID** for each app you want to manage. There are
two ways to look them up — use whichever you're more comfortable with.

#### Option A: AWS Console (browser)

| Value | Where |
|-------|-------|
| `account_id` | Click your account name in the top-right corner of any console page — the 12-digit Account ID is shown in the dropdown (also on the [account page](https://console.aws.amazon.com/billing/home#/account)). |
| `[database] host` | [RDS console](https://console.aws.amazon.com/rds/home) → **Databases** → click your instance → **Connectivity & security** tab → **Endpoint** (looks like `name.xxxxxxxxxxxx.us-east-1.rds.amazonaws.com`). |
| `[database] name` | Same instance page → **Configuration** tab → **DB name**. If blank, it's whatever database your app created — check your app's `DATABASE_URL` (the path segment after the host/port). |
| `[apps.<Name>] app_id` | [Amplify console](https://console.aws.amazon.com/amplify/home) → click the app → **App settings** → **General settings** → **App ARN** ends in `apps/<app_id>` (a `d`-prefixed 14-character ID). It's also in the app's default domain: `https://<branch>.<app_id>.amplifyapp.com`. |

#### Option B: AWS CLI

These are all read-only calls using your default `~/.aws` profile (the same
auth `aws-admin` itself uses):

```bash
# account_id — 12-digit account ID
aws sts get-caller-identity --query Account --output text

# [apps] — every Amplify app's name and ID in one shot
aws amplify list-apps --query 'apps[].{name:name,appId:appId}' --output table

# [database] host and name — endpoint and DB name per RDS instance
aws rds describe-db-instances \
  --query 'DBInstances[].{endpoint:Endpoint.Address,dbname:DBName}' --output table
```

> **Do not** run `aws amplify get-app` / `get-branch` without a `--query` that
> excludes `environmentVariables` — the whole point of this tool is keeping
> those values out of your terminal and shell history. The `list-apps` query
> above is safe as written.

If `DBName` comes back empty (common when the database was created by the
application rather than at instance creation), get the name from your app's
`DATABASE_URL` — it's the path segment after the host and port.

### Verify it works

Pull one app's env vars into the local encrypted snapshot:

```bash
aws-admin env pull shop
```

You should see something like `MyShop: 26 app-level keys, 0 branch-level keys`
— counts only, never values. If you get an auth error, re-check the
**Prerequisites** section; if you get "unknown app", re-check the
`[apps.<Name>]` table and aliases in your config.

## Key concepts (30 seconds)

- **Snapshot** — an encrypted local copy of an app's env vars, stored in
  `~/.config/aws-admin/vaults/`. `pull` refreshes it from AWS; `edit` changes
  it locally; `push` sends it back to AWS. Amplify itself remains the source
  of truth.
- **Dry run** — `push` *without* `--apply` changes nothing; it only shows
  which keys would be added/removed/changed. This is the default on purpose:
  you always get to review before anything happens.
- **Key-only output** — every command prints key *names* only (e.g.
  `changed: STRIPE_SECRET_KEY`), never the values.

## Rotating a secret (e.g. a Stripe key)

1. `aws-admin env pull shop` — refresh the local snapshot from Amplify.
2. `aws-admin env edit shop` — your editor opens with `KEY=value` lines;
   change the value, save, and close the editor. The temp buffer is RAM-backed
   (`/dev/shm`) and shredded on close, so the plaintext never persists on disk.
3. `aws-admin env push shop` — dry run: review the key-only diff (should show
   `changed: <KEY>` and nothing you didn't intend).
4. `aws-admin env push shop --apply --redeploy` — actually apply the change and
   start a redeploy so the app picks it up. (Amplify apps only read env vars
   at build time, which is why the redeploy matters.)

## Branch-level overrides

Amplify lets you set env vars at two levels: app-wide, and per-branch
(per-deployed-branch overrides that win over the app-wide value). `pull`
captures both; `edit` shows branch-level vars under a
`# ===== BRANCH-LEVEL =====` divider; `push --apply` updates branch-level
whenever the snapshot or remote has any. Best practice: keep branch-level
empty unless you have a specific reason.

## Recovery (undo a push)

Before every `--apply`, the tool saves the *previous* remote state to
`~/.config/aws-admin/backups/<app>-<timestamp>.enc`. If a push broke
something, the fastest recovery is usually: fix the value with
`env edit` + `env push --apply --redeploy` again. The backups are encrypted
with the same key as the vaults (`~/.config/aws-admin/vault.key`) if you ever
need to inspect one.

## Database admin

The `db` commands talk to the PostgreSQL database from the `[database]`
section of your config. Connections always use TLS (`sslmode=require`) and
are **read-only unless you explicitly say otherwise**.

### One-time: store the password

```bash
aws-admin db set-password
```

You'll get a hidden prompt (nothing echoes while you type). The password is
stored encrypted in `~/.config/aws-admin/` — you only do this once, and never
type the password on a command line.

Then confirm connectivity:

```bash
aws-admin db check
```

### Curated queries

The tool ships with a small set of pre-written, read-only queries:

```bash
aws-admin db list                 # see what's available
aws-admin db run user-count --show
```

Results are written to a private file in `~/.config/aws-admin/results/` and
only a summary is printed. Add `--show` to print rows inline **only when the
result is not sensitive** (counts are fine; emails or tokens are not).

### Looking up a user by a sensitive value

Suppose you need to find a user by email without the email address landing in
your shell history:

1. Write a file `lookup.sql` with a `{{PLACEHOLDER}}` where the value goes:
   ```sql
   SELECT id, email, "emailVerified" FROM users WHERE email = {{EMAIL}};
   ```
2. `aws-admin db run ./lookup.sql` — your editor opens with an `EMAIL=` line;
   fill in `EMAIL=the@address`, save, close. The value is bound as a query
   parameter (it never appears in the SQL text, your shell history, or the
   database logs) and the temp buffer is shredded.
3. Read the saved result file (its path is printed); or add `--show` only if
   the result is non-sensitive.

### Making a change safely

Writes are double-gated so you can't change data by accident:

1. `aws-admin db run ./change.sql --write` — runs your statement in a
   transaction, prints the affected row count, then **rolls back**. Nothing is
   changed yet; this is a preview.
2. If the row count is what you expected:
   `aws-admin db run ./change.sql --write --commit` — same statement, but this
   time it's kept.

### Cleaning up result files

```bash
aws-admin db clean-results
```

deletes the saved CSVs in `~/.config/aws-admin/results/`.
