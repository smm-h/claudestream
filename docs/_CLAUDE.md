---
title: CLAUDE.md
---
# claudestream

:-: var key="project.description"

## Architecture

claudestream has four layers, each building on the one below:

- **Process** (`_process.py`): Spawns and manages the Claude Code subprocess. `ProcessConfig` maps configuration to CLI flags. `ProcessManager` handles lifecycle (start, graceful shutdown, kill) with atexit cleanup.
- **Protocol** (`_protocol.py`): Reads NDJSON lines from the subprocess stdout and decodes them into typed `Event` objects. Writes `Message` objects as NDJSON to stdin. Handles event flattening (expanding `AssistantMessage` into individual `AssistantText`, `ToolUse`, `Thinking` events) and derives `FileWrite`/`FileEdit` events from tool calls.
- **Session** (`_async_session.py`, `_sync_session.py`): Manages turn-based conversation state on top of the protocol layer. `AsyncSession` is the primary implementation; `SyncSession` wraps it with a dedicated event loop thread. Sessions handle permission interception via sandbox policies, MCP tool serving, lifecycle hooks, and event callbacks.
- **CLI** (`_cli.py`): strictcli-based commands (`send`, `stream`, `ask`, `repl`, `events`, `agent`, `doctor`, `config`) that build `SessionConfig` from flags and run sessions.

The `@tool` decorator (`_tools.py`) and agent definitions (`_agent.py`) are cross-cutting: tools are served via MCP to the subprocess, and agents compose config + sandbox + budget + tools into reusable definitions.

## Modules

:-: list-modules path="claudestream/"

## Commands

:-: table-commands path="claudestream/"

## SessionConfig

The unified configuration object. Both `model` and `profile` are required; everything else is optional.

:-: table-schema path="claudestream/_options.py" target="SessionConfig"

## Budget

:-: table-schema path="claudestream/_options.py" target="Budget"

## Sandbox

:-: table-schema path="claudestream/policy.py" target="Sandbox"

## AgentDefinition

:-: table-schema path="claudestream/_agent.py" target="AgentDefinition"

## ToolSchema

:-: table-schema path="claudestream/_options.py" target="ToolSchema"

## ProcessConfig

Internal struct mapping SessionConfig to subprocess CLI flags. Not part of the public API but useful for understanding how configuration flows to the process.

:-: table-schema path="claudestream/_process.py" target="ProcessConfig"

## Code patterns

### Send and iterate events

```python
from claudestream import SessionConfig, SyncSession, AssistantText, ToolUse, Result

config = SessionConfig(model="sonnet", profile="default")
with SyncSession(config) as session:
    for event in session.send("prompt"):
        if isinstance(event, AssistantText):
            print(event.text, end="")
        elif isinstance(event, ToolUse):
            print(f"[tool: {event.name}]")
        elif isinstance(event, Result):
            print(f"cost=${event.total_cost_usd:.4f}")
```

### One-shot ask

```python
config = SessionConfig(model="sonnet", profile="default")
with SyncSession(config) as session:
    result = session.ask("prompt")
    print(result.text)
```

### Async session

```python
async with AsyncSession(config) as session:
    async for event in session.send("prompt"):
        ...
```

### Register a tool

```python
from claudestream import tool

@tool("my_server")
def search(query: str) -> str:
    """Search for something.

    Args:
        query: The search query.
    """
    return "result"

config = SessionConfig(model="sonnet", profile="default", tools=[search._tool])
```

### Load and invoke an agent

```python
from claudestream import load_agent, invoke_agent_sync

agent = load_agent("agent_name")
config = SessionConfig(model="sonnet", profile="default")
with invoke_agent_sync(agent, config, variables={"key": "value"}) as session:
    for event in session.send("prompt"):
        ...
```

### Set up a sandbox

```python
from claudestream import create_sandbox

sandbox = create_sandbox(tools=["Read", "Bash"], write_paths=["/project"])
config = SessionConfig(model="sonnet", profile="default", sandbox=sandbox)
```

### Handle permissions manually

```python
from claudestream import SessionConfig, SyncSession, PermissionRequest

config = SessionConfig(model="sonnet", profile="default")
with SyncSession(config) as session:
    for event in session.send("prompt", raw=True):
        if isinstance(event, PermissionRequest):
            session.respond_allow(event.request_id, event.tool_input)
```

### Lifecycle hooks

```python
def on_done(session, result):
    print(f"Turn complete: {result.num_turns} turns, ${result.total_cost_usd:.4f}")

session.on_turn_complete(on_done)
```

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
