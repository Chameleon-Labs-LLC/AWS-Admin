@AGENTS.md

# Claude Code extras

- `slash-commands/` holds the `/aws-env-*`, `/aws-db-*`, and `/aws-cost-report` command definitions. They call `bin/aws-admin` so the model sees only redacted output. Use them instead of raw `aws` calls.
- Admin-agent DB/env-var authorization: `~/.claude/instructions/aws-chameleonlabs.md`.
