"""The CLI's effect classification, pinned so a change has to be deliberate.

strictcli requires every command to declare ``effect="read_only"`` or
``effect="mutating"``; there is no default and a missing declaration is a
registration-time hard error. The classification answers exactly one question:
*should a dry run record this operation rather than perform it?*

Separately, a command may declare itself ``consequential``, which is what the
framework's confirm protocol keys on. It is NOT inferred from ``mutating`` --
that inference was measured at a ~1:10 signal-to-noise ratio across the fleet
and removed, because a guardrail that fires on two thirds of a CLI's commands
trains the reflex that hollows it out.

This file pins both tables in both directions. A new command shows up as an
unexpected entry; a reclassified one shows up as a mismatch. Either way the
edit has to come here, which is the point.
"""

from typing import Any

from claudestream._cli import app

# Every command claudestream declares, with the reviewed classification.
#
# The five session commands are `mutating` for the same reason: each spawns a
# real Claude Code subprocess, which reads and writes files in --cwd, runs
# shell commands, calls the network, spends money, and persists a session
# transcript to disk. What the model chooses to do is not knowable in advance,
# which is precisely why a preview must record the spawn rather than perform
# it.
#
# `agent run` is the sixth: it resolves an agent definition and then runs the
# same session machinery under it.
#
# The four inspection commands read and print. `agent list`, `agent info` and
# `agent validate` parse .agent.json files off disk. `doctor` and `config`
# locate the claude binary, run it with --version, and resolve a claudewheel
# profile to its env vars -- a version probe changes nothing, and neither does
# reading a profile.
EFFECTS = {
    "send": "mutating",
    "stream": "mutating",
    "events": "mutating",
    "repl": "mutating",
    "ask": "mutating",
    "doctor": "read_only",
    "config": "read_only",
    "agent.run": "mutating",
    "agent.list": "read_only",
    "agent.info": "read_only",
    "agent.validate": "read_only",
}

# Empty, deliberately.
#
# `consequential` means "this act is worth interrupting someone for". Running
# Claude is not an incidental side effect of these commands -- it is the entire
# point of every one of them, and it is what a user typing `claudestream ask`
# has already asked for. claudestream is also invoked overwhelmingly from
# scripts, hooks and other agents, for whom the framework's non-TTY refusal is
# not a safety net but a hard stop on the tool's normal use.
#
# The sharpest hazard here is `--skip-permissions`, which hands the subprocess
# unrestricted tool access. It is deliberately NOT addressed by declaring the
# command consequential: `consequential` is per-command, so declaring `ask`
# would put a blind `Proceed? [y/N]` in front of every safe `ask` too, which
# is the exact noise the declaration replaced. A flag-granular seam belongs
# inside a handler if one is ever wanted; it is not this table's job.
CONSEQUENTIAL: set[str] = set()

# strictcli owns these four names at every level -- command flags, flag-set
# flags, mutex-group flags and app globals alike. `yes` names no framework flag
# any more (the skip flag is --approve-consequential) but stays banned so a
# consumer cannot restate it in the spelling the rename removed.
RESERVED_FLAG_NAMES = {
    "dry-run",
    "approve-consequential",
    "quiet",
    "verbose",
    "yes",
}


def _walk() -> dict[str, Any]:
    """Map dotted command path -> Command for every registered command."""
    found: dict[str, Any] = {}

    def visit(container: Any, prefix: str) -> None:
        registry = getattr(container, "_commands", None) or container.commands
        for name, cmd in registry.items():
            found[prefix + name] = cmd
        for name, group in container._groups.items():
            visit(group, prefix + name + ".")

    visit(app, "")
    return found


def test_every_command_is_classified_exactly_as_reviewed() -> None:
    declared = {path: cmd.effect for path, cmd in _walk().items()}
    assert declared == EFFECTS


def test_consequential_declarations_match_the_reviewed_set() -> None:
    """Both directions matter.

    A missing declaration removes a prompt somebody decided was owed; a stray
    one puts a blind ``Proceed? [y/N]`` in front of routine work and hangs
    every script that calls it.
    """
    declared = {path for path, cmd in _walk().items() if cmd.consequential}
    assert declared == CONSEQUENTIAL


def test_no_command_redeclares_a_framework_reserved_flag_name() -> None:
    """A collision is a registration-time error, so reaching here means it built.

    Pinning the absence keeps a future flag from reintroducing one under a
    spelling that would break the CLI at import time.
    """
    assert not ({f.name for f in app._global_flags} & RESERVED_FLAG_NAMES)
    for path, cmd in _walk().items():
        names = {f.name for f in cmd.flags}
        collisions = names & RESERVED_FLAG_NAMES
        assert not collisions, f"'{path}' declares reserved flag(s) {sorted(collisions)}"


def test_the_reserved_quartet_reaches_the_context_not_the_handler() -> None:
    """--quiet is framework-owned and never lands in a handler's kwargs.

    `config` takes one flag of its own; passing a quartet member alongside it
    must not become an unknown-flag error and must not be forwarded, which is
    what guard v2 would reject.
    """
    result = app.test(["config", "--quiet"])
    assert result.exit_code == 0
