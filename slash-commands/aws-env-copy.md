---
description: Copy env-var keys between two apps' local snapshots (no AWS, values never shown)
argument-hint: <KEY...> --from <app> --to <app> [--rename SRC=DST]
allowed-tools: Bash
---

Copy the named keys from one app's encrypted local snapshot to another's. This sends
nothing to AWS and prints key/app names only, never values:

!`/mnt/d/Documents/Code/GitHub/AWS-Admin/bin/aws-admin env copy $ARGUMENTS`

Report the key-only result. If it was refused because the destination already has a
different value for some keys, tell the user which keys and ask before re-running with
`--overwrite`; never add `--overwrite` on your own. If a snapshot was missing, suggest
`/aws-env-pull <app>` for that app first.

After a successful copy, suggest `/aws-env-push <to-app>` to review the key-only diff.
Never ask the user to paste a value into chat.
