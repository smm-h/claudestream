#!/usr/bin/env bash
# Pre-release validation hook.
# Runs before rlsbl creates a release. Exit non-zero to abort.
# Detects project type and runs appropriate checks automatically.

set -euo pipefail

echo "Running pre-release checks..."

if [ -f go.mod ]; then
  echo "  Go: vet + build + test"
  go vet ./...
  go build ./...
  go test ./... -race -short -count=1
fi

if [ -f pyproject.toml ]; then
  echo "  Python: pytest"
  if command -v uv &>/dev/null; then
    uv run pytest
  elif command -v pytest &>/dev/null; then
    pytest
  fi
fi

if [ -f package.json ] && node -e "process.exit(require('./package.json').scripts?.test ? 0 : 1)" 2>/dev/null; then
  echo "  npm: test"
  npm test
fi

# Library lint (monorepo only)
if command -v rlsbl &>/dev/null; then
  if rlsbl doctor --check library-lint 2>/dev/null; then
    :
  else
    echo "  Library lint check failed"
    exit 1
  fi
fi

echo "Pre-release checks passed."
