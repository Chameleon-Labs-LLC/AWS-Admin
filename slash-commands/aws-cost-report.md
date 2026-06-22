---
description: AWS cost & free-tier-expiry report (read-only; account ID redacted)
argument-hint: "[--months N] [--out path]"
allowed-tools: Bash
---

Generate the AWS cost report (read-only Cost Explorer + Free Tier APIs; the
account ID is shown redacted to its last four digits):

!`/mnt/d/Documents/Code/GitHub/AWS-Admin/bin/aws-admin cost report $ARGUMENTS`

Summarize for the user: the spend trend, the current-month projection, the top
cost drivers, and the estimated monthly increase when the 12-month free tier
ends. Call out any active free trials (they bill $0 now but start charging when
the trial ends). The dollar figures are estimates, not an invoice.
