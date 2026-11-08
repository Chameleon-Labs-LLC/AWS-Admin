# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
