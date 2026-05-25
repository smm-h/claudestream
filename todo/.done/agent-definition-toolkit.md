# Agent definition toolkit

## Context

A downstream project needs to define multiple distinct agent types — each with its own system prompt, permitted tools, custom tool definitions, budget constraints, and behavioral restrictions — then invoke them programmatically via library API, CLI, or HTTP. Today this requires manually assembling `AsyncSession(system_prompt=..., policy=..., extra_args=[...])` with fragile, ad-hoc wiring.

The existing todos for custom tool registration and sandbox profiles solve pieces of this: custom tools give the agent new capabilities, sandbox profiles restrict its scope. This todo is the composition layer that bundles them into a single declarative artifact.

## Problem

There is no first-class concept of a reusable "agent definition" in claudestream. Every consumer manually constructs sessions with raw parameters. This leads to:

1. **Duplication**: every consumer writes the same boilerplate — policy selection, system prompt assembly, tool registration, budget tracking, error handling.
2. **No portability**: an agent definition lives in application code, not in a shareable format. You can't send an agent definition to someone else, version it, or inspect it without reading the source.
3. **No composition**: you can't say "take the base shop-assistant agent and add a cart tool." You rebuild the entire session config from scratch.
4. **No runtime management**: once a session is started, there's no standard way to inspect what agent definition it's running, what tools it has, what its budget is, or what restrictions apply.

## Proposed solution

A declarative `AgentDefinition` that bundles:

- **identity**: name, version, description
- **prompt**: system prompt template with variable substitution (e.g., `{merchant_name}`, `{voice_profile}`)
- **tools**: list of custom tool schemas (name, description, input JSON schema) — references the custom tool registration system
- **builtin_tools**: which Claude Code built-in tools are allowed (e.g., `["Read", "Bash"]`) — references the sandbox profile system
- **restrictions**: directory scopes, file patterns, command allowlists — references sandbox profiles
- **budget**: max cost per session (USD), max turns, max tokens
- **model**: preferred model (can be overridden at invocation)

### Usage patterns

**Library (Python)**:
```python
from claudestream import AgentDefinition, invoke_agent

shop_agent = AgentDefinition(
    name="shop-assistant",
    prompt="You are a shop assistant for {merchant_name}. ...",
    tools=[search_products_tool, get_product_tool, cart_tool],
    builtin_tools=["Read"],
    budget=Budget(max_cost_usd=0.50, max_turns=20),
)

async with invoke_agent(shop_agent, profile="work", variables={"merchant_name": "Must Have Milano"}) as session:
    async for event in session.send("Do you have Gucci bags?"):
        ...
```

**CLI**:
```bash
claudestream agent run shop-assistant.agent.json --var merchant_name="Must Have Milano" --profile work
```

**File format** (`.agent.json` or `.agent.toml`):
```json
{
  "name": "shop-assistant",
  "version": "1.0",
  "prompt_template": "You are a shop assistant for {merchant_name}. ...",
  "tools": [...],
  "builtin_tools": ["Read"],
  "restrictions": {"directories": ["{data_dir}"]},
  "budget": {"max_cost_usd": 0.50, "max_turns": 20}
}
```

## Relationship to existing todos

- **custom-tool-registration.md**: provides the mechanism for registering custom tools with Claude. AgentDefinition uses this to declare its tools.
- **sandbox-profile.md**: provides the restriction mechanism. AgentDefinition uses this to declare its scope.
- **pre-execution-tool-interception.md**: provides the enforcement layer. AgentDefinition relies on this to enforce restrictions.

This todo is the composition layer on top of those three.

## Affected areas

- `AsyncSession` and `SyncSession` constructors (accept `AgentDefinition` as an alternative to raw params)
- New module: `claudestream.agent` with `AgentDefinition`, `Budget`, `invoke_agent`
- CLI: `claudestream agent run` command
- File format: `.agent.json` / `.agent.toml` loader

## Effort

Large. Depends on custom-tool-registration and sandbox-profile being implemented first. The composition layer itself is moderate, but the underlying features are prerequisites.

## Consumer needs (concrete)

The downstream project defines these agent types:
- **crawler agent**: explores a website via Playwright tools, takes screenshots, extracts text/styles. Needs: Bash, Read, Write. Budget: ~$5/run. Timeout: 30 min.
- **extraction agent**: analyzes crawled data, classifies APIs, normalizes products, extracts design tokens and voice profile. Needs: Read only. Budget: ~$1/run.
- **shop assistant agent**: answers customer questions, searches products, manages cart. Needs: custom tools (search_products, get_product, cart_add, etc.) + Read. Budget: $0.50/session, 20 turns max.
- **admin assistant agent**: helps merchant customize their shop via natural language. Needs: custom tools (update_design_tokens, update_voice_profile, get_analytics). Budget: $1/session.

Each of these should be a declarative definition, not ad-hoc session construction.
