# discover_agents: support custom search paths

## Problem

`discover_agents()` only searches `.claudestream/agents/` relative to cwd. Consumers who ship agent definitions inside their Python packages (e.g., `src/myapp/agents/definitions/`) can't use discovery — they must call `load_agent()` with explicit paths.

This is a common pattern: the agent definitions are versioned with the package and installed alongside the code. Forcing them into `.claudestream/agents/` means they live outside the package, can't be installed via pip/uv, and diverge from the source of truth.

## Proposed API

```python
# Current (unchanged)
discover_agents()  # searches .claudestream/agents/ relative to cwd

# New: additional search paths
discover_agents(paths=["src/dijkstra/agents/definitions/"])

# New: package resource support
discover_agents(packages=["dijkstra.agents.definitions"])
```

- `paths`: list of directories to search (in addition to the default `.claudestream/agents/`). Relative paths resolved against cwd.
- `packages`: list of Python package paths. Uses `importlib.resources` to locate `.agent.json` files within installed packages.
- Both are optional. Default behavior is unchanged (backward compatible).
- Results from all sources are merged and deduplicated by name (first occurrence wins, with a warning on conflicts).

## Concrete consumer

gamehome (Dijkstra game generation orchestrator) ships 7 agent definitions (planner, coder, critic, refiner, expander, playtester, optimizer) inside `src/dijkstra/agents/definitions/`. These are part of the package and should be discoverable without symlinking to `.claudestream/agents/`.

## Affected files

- `_agent.py`: `discover_agents()` signature and implementation
- Tests for path and package resolution

## Effort

Small. The file scanning logic already exists — just needs to accept additional root paths.
