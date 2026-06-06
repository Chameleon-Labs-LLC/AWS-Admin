# aws-admin usage

## First-time setup

Copy the example config and fill in your AWS values (the file is gitignored and
never leaves your machine):

```bash
cp config.example.toml ~/.config/aws-admin/config.toml
$EDITOR ~/.config/aws-admin/config.toml
```

`aws-admin` reads `account_id`, the `[database]` host/name, and one
`[apps.<Name>]` table per Amplify app from this file. Generic settings (region,
default branch, DB port/user/sslmode) have built-in defaults.

## Rotating a secret (e.g. a Stripe key)
1. `aws-admin env pull eo` — refresh the local snapshot from Amplify.
2. `aws-admin env edit eo` — your editor opens; change the value, save, close.
   The temp buffer is RAM-backed (/dev/shm) and shredded on close.
3. `aws-admin env push eo` — review the key-only diff (should show `changed: <KEY>`).
4. `aws-admin env push eo --apply --redeploy` — apply and redeploy.

## Branch-level overrides
Amplify branch-level vars override app-level ones. `pull` captures both; `edit` shows both
under `# ===== BRANCH-LEVEL =====`; `push --apply` updates branch-level whenever the
snapshot or remote has any. Best practice: keep branch-level empty.

## Recovery
Pre-change remote state is saved to `~/.config/aws-admin/backups/<app>-<ts>.enc` before
every `--apply`. To inspect a backup, decrypt it with the same key
(`~/.config/aws-admin/vault.key`).

## Database admin

### Looking up a user by a sensitive value
1. Write a file `lookup.sql`:
   ```sql
   SELECT id, email, "emailVerified" FROM users WHERE email = {{EMAIL}};
   ```
2. `aws-admin db run ./lookup.sql` — your editor opens; fill `EMAIL=the@address`,
   save, close. The value is bound as a parameter and the buffer is shredded.
3. Read the saved result file (path is printed); or add `--show` only if the result
   is non-sensitive.

### Making a change safely
1. `aws-admin db run ./change.sql --write` — runs in a transaction, prints affected
   row count, then rolls back (a preview).
2. If the count is what you expect: `aws-admin db run ./change.sql --write --commit`.

### Password
`aws-admin db set-password` stores it Fernet-encrypted in `~/.config/aws-admin/`.
The connection uses `sslmode=require`.
