---
description: Run a curated DB query or a .sql file (results saved to a file, summary only)
argument-hint: <curated-name | path/to/file.sql>
allowed-tools: Bash
---

Run the DB query/file `$ARGUMENTS` (read-only; results go to a local file, only a
redacted summary is shown):

!`/mnt/d/Documents/Code/GitHub/AWS-Admin/bin/aws-admin db run $ARGUMENTS`

Report the row count / columns / file path. Do NOT print row values. If the SQL has
`{{NAME}}` placeholders it will open the user's editor — in that case tell the user to
run it themselves with `! aws-admin db run $ARGUMENTS`. For writes, the user must add
`--write` (preview) and then `--write --commit` to persist — never add those yourself
without explicit confirmation.
