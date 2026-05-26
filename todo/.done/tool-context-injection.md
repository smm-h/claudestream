# Tool context injection

## Problem

The `@tool` decorator registers tool functions at definition time, but tools often need per-session runtime context — a database connection, a tenant ID, a crawl ID, a user session. There's no mechanism to inject this context into tool functions when they're invoked during a session.

## Current workarounds

Consumers use one of:
- **Module-level globals**: set `_current_conn = conn` before invoking the agent, tools read it. Not thread-safe.
- **Closure factory**: `make_tools(conn, crawl_id)` returns new tool functions with context captured in closures. Thread-safe but verbose — every tool must be wrapped.
- **contextvars**: store context in ContextVar, tools read from it. Thread-safe, implicit. Works but couples tools to a specific context management pattern.

All three are workarounds for a missing feature.

## Proposed solution

A `tool_context` parameter on `SessionConfig` (or `invoke_agent`) that makes a context object available to all tools during the session:

```python
@tool(name="search_products")
def search_products(query: str, limit: int = 10, ctx: ToolContext = None) -> list[dict]:
    products = db.search_products(ctx.conn, ctx.crawl_id, query, limit)
    return [p.model_dump() for p in products]

# At invocation
config = SessionConfig(
    ...,
    tool_context={"conn": conn, "crawl_id": crawl_id},
)
```

Or via a typed context class:

```python
class ShopToolContext:
    conn: Connection
    crawl_id: str
    session_id: str

config = SessionConfig(
    ...,
    tool_context=ShopToolContext(conn=conn, crawl_id=crawl_id, session_id=sid),
)
```

The context is injected as a special parameter (by name or type annotation) into tool functions when they're called. Tools that don't declare the parameter don't receive it.

## Alternative: tool factories

Instead of injecting context into existing tools, support tool factories that create tool instances with bound context:

```python
def make_search_tool(conn, crawl_id):
    @tool(name="search_products")
    def search_products(query: str, limit: int = 10) -> list[dict]:
        return db.search_products(conn, crawl_id, query, limit)
    return search_products

tools = [make_search_tool(conn, crawl_id), make_detail_tool(conn, crawl_id)]
config = SessionConfig(..., tools=tools)
```

This works today but requires wrapping every tool in a factory function.

## Consumer needs

A downstream project defines 4 catalog tools (search, detail, availability, cart totals) that all need: a SQLite connection, a crawl_id, and a session_id. These change per user session. Currently they use a closure factory to bind context, but this is 30 lines of boilerplate per agent invocation.

## Effort

Small-medium. The tool invocation path already inspects function signatures for parameter types. Adding context injection is extending the same mechanism.
