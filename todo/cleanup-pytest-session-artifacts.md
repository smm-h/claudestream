# Clean up orphaned session dirs from integration tests

## Problem

The `test_file_write_tracking` integration test in `tests/test_integration.py` creates real Claude Code sessions with `cwd=str(tmp_path)`. When pytest cleans up the temp directory, the session data in `~/.claudewheel/shared/projects/-tmp-pytest-of-m-pytest-*-test_file_write_tracking0/` becomes orphaned. Over time, 44 of these accumulated (~16K each).

## Proposed fix

Either:
1. Use a dedicated test profile that writes session data to a temp location (not the shared store)
2. Or clean up the orphaned project dir in the shared store as part of test teardown
3. Or set an env var that tells Claude Code not to persist the session (if such a mechanism exists)
