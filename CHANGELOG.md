# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.11.2] - 2026-11-08

### Security
- Ownership is now proven by markers, not inferred. A role is touched only if it carries the `aws-admin testsite` marker comment and is not superuser, createrole or createdb. A database is adopted only if the marked role owns it, and `down` drops it only if the database carries its own marker (set by `up`).
- The isolation check also catches sequence privileges and column-level grants (`has_sequence_privilege`, `has_any_column_privilege`) in every reachable database, alongside table privileges, schema `CREATE` and role memberships. Not covered, by design: function `EXECUTE` (granted to `PUBLIC` by Postgres), large objects, foreign servers.

### Fixed
- `down`: if `DROP ROLE` fails after the database has been dropped, the error names the cause and the recovery (`DROP OWNED BY <role>;` in each database the role can reach, then `down --yes` again).
- `status`: when the role is missing but the database still exists, it says to check the database's owner instead of suggesting `up`, which would refuse it.
- CHANGELOG 0.11.1 entries now state what that release did and did not cover.

## [0.11.1] - 2026-11-08

### Security
- `testsite` verifies ownership before changing anything: the app must be tagged `managed-by=aws-admin-testsite` (or be the app id recorded locally when the tool created it) and must not be a configured production app id; the database must be owned by the managed role. (0.11.1 still adopted a role that owned the test database without carrying the tool's marker; 0.11.2 requires the marker on the role and on the database.)
- The isolation check now covers role memberships (including `SET ROLE` targets) and every database the role can reach through `PUBLIC`, failing on table-level privileges and schema `CREATE`. (0.11.1 did not check sequences or column-level grants; 0.11.2 does.)
- Compute roles are refused at branch level as well as app level.

### Fixed
- A failed or timed-out build and a failed smoke check now exit with status 4 instead of 0.
- `deploy` keeps polling while a job is `CREATED`.
- `ALTER ROLE` no longer sets `NOSUPERUSER`, which the RDS admin user cannot change.
- `smoke` reports signed-in checks as SKIP, with the reason, when the app pins its session cookie to a domain the test host cannot hold.
- `status` reports a missing role instead of failing.
- `down` is no longer blocked by a failed isolation check. `DROP ROLE` can still fail if the role holds something in another database; the database is already gone by then (see 0.11.2 for the recovery message).

### Changed
- Docstrings on every `testsite` function. `docs/usage.md` gains a "Test site" section.

## [0.11.0] - 2026-11-08

### Added
- feat(testsite): exec -- run a command with the test DATABASE_URL in its env

## [0.10.0] - 2026-11-08

### Added
- feat(testsite): agent-managed test copy of an Amplify SSR app

## [0.9.0] - 2026-06-15

### Added
- feat(cli): include rotate in the 'all' epilog hint

### Changed
- test(env): no-value-leak coverage for rotate; docs: multi-app rotate

## [0.8.0] - 2026-06-15

### Added
- feat(cli): wire 'env rotate' subcommand

## [0.7.0] - 2026-06-15

### Added
- feat(env): rotate one shared secret across apps' local snapshots

## [0.6.0] - 2026-06-15

### Added
- feat(vault): capture_value() — single new value via shred-after editor

## [0.5.0] - 2026-06-15

### Added
- feat(config): resolve_apps() for token lists with 'all' expansion

## [0.4.0] - 2026-06-15

### Added
- feat(cli): advertise known apps + aliases in -h; docs refresh

### Changed
- docs(plans): implementation plan for env rotate
- docs(plans): add design for env rotate command

## [0.3.0] - 2026-06-07

### Added
- feat(cli): self-healing launcher bin/aws-admin; slash commands use it

## [0.2.0] - 2026-06-07

### Added
- feat(db): multi-statement .sql scripts in run_sql (single transaction)

## [0.1.0] - 2026-06-06

### Changed
- Initial commit: secure local CLI for AWS admin tasks
