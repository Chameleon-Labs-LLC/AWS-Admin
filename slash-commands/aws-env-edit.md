---
description: Edit Amplify env-var values locally in your editor (bypasses the model)
argument-hint: <app: my|eo|ab|aa|ag or name/id>
allowed-tools: Bash
---

Editing secret values must happen in YOUR terminal so values never reach the model.
Tell the user to run this themselves (the `!` prefix runs it in their session):

    ! /mnt/d/Documents/Code/GitHub/AWS-Admin/.venv_linux/bin/aws-admin env edit $ARGUMENTS

After they save, suggest `/aws-env-push $ARGUMENTS` to review the key-only diff.
Do NOT run the edit command yourself and never ask the user to paste values into chat.
