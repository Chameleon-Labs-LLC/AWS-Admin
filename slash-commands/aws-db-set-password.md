---
description: Store the PostgreSQL password in the encrypted vault (hidden prompt)
allowed-tools: Bash
---

This must run in YOUR terminal so the password is never seen by the model. Tell the
user to run (the `!` prefix runs it in their session):

    ! /mnt/d/Documents/Code/GitHub/AWS-Admin/.venv_linux/bin/aws-admin db set-password

Do NOT run this yourself and never ask the user to type the password into chat.
