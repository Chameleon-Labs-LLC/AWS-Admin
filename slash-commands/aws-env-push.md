---
description: Dry-run a push of the local snapshot to Amplify (key-only diff)
argument-hint: <app: my|eo|ab|aa|ag or name/id>
allowed-tools: Bash
---

Dry-run the push for `$ARGUMENTS` (this sends nothing):

!`/mnt/d/Documents/Code/GitHub/AWS-Admin/bin/aws-admin env push $ARGUMENTS`

Report the key-only diff. To actually apply, the user must explicitly confirm; only then
run `aws-admin env push $ARGUMENTS --apply` (add `--redeploy` if they want a redeploy).
