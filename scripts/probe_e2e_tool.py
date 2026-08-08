#!/usr/bin/env python3
"""Manual probe: custom tool registration end to end through the claudestream API.

This is an operator script, not a test. Running it drives the real Claude Code
binary against the named profile and SPENDS REAL MONEY -- which is why it is
named ``probe_*`` rather than ``test_*``: nothing about it should ever be
collected by pytest.

Run it by hand::

    scripts/probe_e2e_tool.py
"""

from claudestream import (
    tool, SessionConfig, SyncSession, AssistantText, ToolUse, Result,
)


@tool("test_server")
def greet(name: str) -> str:
    """Greet someone by name.

    Args:
        name: Name to greet.
    """
    return f"Hello, {name}!"


def main():
    config = SessionConfig(
        model="sonnet",
        profile="work",
        tools=[greet._tool],
    )

    print("Starting session with custom tool...")
    tool_called = False

    with SyncSession(config) as session:
        print(f"Session started. Tools: {session.tools}")

        for event in session.send("Use the greet tool to greet Alice. Just call the tool, nothing else."):
            if isinstance(event, AssistantText):
                print(f"Text: {event.text[:100]}")
            elif isinstance(event, ToolUse):
                print(f"ToolUse: {event.name} input={event.input}")
                tool_called = True
            elif isinstance(event, Result):
                print(f"Result: cost=${event.total_cost_usd:.4f}, turns={event.num_turns}")

    if tool_called:
        print("\nSUCCESS: Custom tool was called through the claudestream API!")
    else:
        print("\nFAILURE: Custom tool was NOT called.")

    return 0 if tool_called else 1


if __name__ == "__main__":
    exit(main())
