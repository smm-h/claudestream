---
title: CLAUDE.md
---
# claudestream

:-: var key="project.description"

## Commands

:-: table-commands path="claudestream/"

## Release workflow

This project uses [rlsbl](https://github.com/smm-h/rlsbl) for release orchestration.

- Update CHANGELOG.md with a `## X.Y.Z` entry describing changes
- Run `rlsbl release init` to scaffold the release file, set the bump type, then `rlsbl release run`
- CI handles publishing automatically via the publish workflow
- Never publish manually — always use `rlsbl release run`
- Configure Trusted Publishing on pypi.org for automated PyPI releases
- Use `rlsbl --dry-run release run` to preview a release without making changes

## Conventions

- No tokens or secrets in command-line arguments (use env vars or config files)
- All file writes to shared state should be atomic (write to tmp, then rename)
- External calls (APIs, CLI tools) must have timeouts and graceful fallbacks
- Use `npm link` (npm) or `uv pip install -e .` (Python) for local development
- CI runs smoke tests on every push; manual testing for UI/UX changes
