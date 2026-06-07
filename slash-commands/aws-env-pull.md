---
description: Pull Amplify env vars into the encrypted local snapshot (no values shown)
argument-hint: <app: my|eo|ab|aa|ag or name/id>
allowed-tools: Bash
---

Run the secure AWS admin tool to pull env vars for the app `$ARGUMENTS`:

!`/mnt/d/Documents/Code/GitHub/AWS-Admin/bin/aws-admin env pull $ARGUMENTS`

Report the redacted summary back. Never ask for or echo secret values.
