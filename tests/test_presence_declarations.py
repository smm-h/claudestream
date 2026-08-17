"""The presence declaration on every flag and arg, pinned so a change is deliberate.

strictcli requires every flag and every positional argument to declare exactly
one of ``presence="required"``, ``presence="optional"`` or ``default=<value>``.
There is no inference and no default; declaring none, or declaring two, is a
registration-time hard error. Separately, a command declaring
``effect="mutating"`` may not declare a *value* default at all -- absence must
never resolve to a value the invocation did not state, because on a mutating
command a value the framework picked is a value the framework writes. An empty
collection (``default=[]``) is the one legal default there: it declares *no
elements*, so nothing framework-chosen reaches a write through it.

claudestream's five session commands plus ``agent run`` are all mutating, so
the ban reaches nearly every declaration in the CLI. The switches those
commands used to default (``--footer``, ``--color``, ``--raw``, ``--stdin``,
``--skip-permissions``, ``--json-output``) now declare ``presence="optional"``
and name their fallback in their own help text; ``_opt`` in ``_cli`` is the one
place that turns absence into that fallback.

The string flags that used to carry ``default=""`` were triaged the same way:
every one of them was tested for falsiness downstream (``cwd or None``,
``if profile:``), which is the empty-string sentinel this declaration retires.
They deliver ``None`` now, and it is passed straight through.

This file pins all of it in both directions. A new flag that forgets its
presence, a default that creeps back onto a mutating command, or a triage
decision that silently flips shows up here.
"""

from io import StringIO
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from claudestream._cli import (
    _opt,
    _resolve_prompt,
    app,
    cmd_events,
    cmd_send,
    cmd_stream,
)
from claudestream._color import Colorizer


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


def _declarations() -> list[tuple[str, str, str, Any]]:
    """(command path, kind, name, declaration) for every flag and arg."""
    out = []
    for path, cmd in sorted(_walk().items()):
        for a in cmd.args:
            out.append((path, "arg", a.name, a))
        for f in cmd.flags:
            out.append((path, "flag", f.name, f))
    return out


def _presence(decl: Any) -> str:
    """Normalize a declaration to 'required', 'optional' or 'default'."""
    p = getattr(decl, "presence", None)
    if p in ("required", "optional"):
        return p
    return "default"


# The reviewed triage, one row per declaration. `default` rows carry the
# declared value so an empty-collection default cannot silently become a
# value default.
EXPECTED = {
    ("agent.info", "arg", "name"): "required",
    ("agent.list", "flag", "cwd"): "optional",
    ("agent.run", "arg", "definition"): "required",
    ("agent.run", "arg", "prompt"): "required",
    ("agent.run", "flag", "color"): "optional",
    ("agent.run", "flag", "cwd"): "optional",
    ("agent.run", "flag", "footer"): "optional",
    ("agent.run", "flag", "model"): "optional",
    ("agent.run", "flag", "profile"): "required",
    ("agent.run", "flag", "var"): "default",
    ("agent.validate", "arg", "name"): "required",
    ("ask", "arg", "prompt"): "optional",
    ("ask", "flag", "color"): "optional",
    ("ask", "flag", "cwd"): "optional",
    ("ask", "flag", "from-pr"): "optional",
    ("ask", "flag", "json-output"): "optional",
    ("ask", "flag", "model"): "required",
    ("ask", "flag", "profile"): "required",
    ("ask", "flag", "skip-permissions"): "optional",
    ("ask", "flag", "stdin"): "optional",
    ("ask", "flag", "system-prompt"): "optional",
    ("config", "flag", "profile"): "optional",
    ("doctor", "flag", "profile"): "optional",
    ("events", "arg", "prompt"): "optional",
    ("events", "flag", "color"): "optional",
    ("events", "flag", "cwd"): "optional",
    ("events", "flag", "footer"): "optional",
    ("events", "flag", "from-pr"): "optional",
    ("events", "flag", "model"): "required",
    ("events", "flag", "profile"): "required",
    ("events", "flag", "resume"): "optional",
    ("events", "flag", "skip-permissions"): "optional",
    ("events", "flag", "stdin"): "optional",
    ("events", "flag", "system-prompt"): "optional",
    ("repl", "flag", "color"): "optional",
    ("repl", "flag", "cwd"): "optional",
    ("repl", "flag", "footer"): "optional",
    ("repl", "flag", "from-pr"): "optional",
    ("repl", "flag", "model"): "required",
    ("repl", "flag", "profile"): "required",
    ("repl", "flag", "resume"): "optional",
    ("repl", "flag", "skip-permissions"): "optional",
    ("repl", "flag", "system-prompt"): "optional",
    ("send", "arg", "prompt"): "optional",
    ("send", "flag", "color"): "optional",
    ("send", "flag", "cwd"): "optional",
    ("send", "flag", "footer"): "optional",
    ("send", "flag", "from-pr"): "optional",
    ("send", "flag", "json-output"): "optional",
    ("send", "flag", "model"): "required",
    ("send", "flag", "profile"): "required",
    ("send", "flag", "raw"): "optional",
    ("send", "flag", "resume"): "optional",
    ("send", "flag", "skip-permissions"): "optional",
    ("send", "flag", "stdin"): "optional",
    ("send", "flag", "system-prompt"): "optional",
    ("stream", "arg", "prompt"): "optional",
    ("stream", "flag", "color"): "optional",
    ("stream", "flag", "cwd"): "optional",
    ("stream", "flag", "footer"): "optional",
    ("stream", "flag", "from-pr"): "optional",
    ("stream", "flag", "model"): "required",
    ("stream", "flag", "profile"): "required",
    ("stream", "flag", "resume"): "optional",
    ("stream", "flag", "skip-permissions"): "optional",
    ("stream", "flag", "stdin"): "optional",
    ("stream", "flag", "system-prompt"): "optional",
}


def test_every_declaration_carries_the_reviewed_presence() -> None:
    declared = {
        (path, kind, name): _presence(decl)
        for path, kind, name, decl in _declarations()
    }
    assert declared == EXPECTED


def test_no_mutating_command_declares_a_value_default() -> None:
    """The mutating-default ban, pinned in the repo that has to live with it.

    strictcli refuses these at registration, so reaching this assertion means
    the CLI built. Pinning the absence keeps a future flag from reintroducing
    one under a command that gets reclassified to ``read_only`` and back.
    """
    offenders = []
    for path, cmd in _walk().items():
        if cmd.effect != "mutating":
            continue
        for kind, decl in [("arg", a) for a in cmd.args] + [("flag", f) for f in cmd.flags]:
            if _presence(decl) != "default":
                continue
            if decl.default in ([], {}):
                continue  # an empty collection declares no elements
            offenders.append(f"{path}: {kind} '{decl.name}' declares default={decl.default!r}")
    assert not offenders, offenders


def test_the_only_default_left_is_an_empty_collection() -> None:
    """`--var` is the single surviving default, and it is `[]`."""
    defaults = {
        (path, name): decl.default
        for path, kind, name, decl in _declarations()
        if _presence(decl) == "default"
    }
    assert defaults == {("agent.run", "var"): []}


def test_no_declaration_defaults_to_the_empty_string() -> None:
    """The sentinel this migration retired, pinned as gone.

    Every ``default=""`` in the CLI was a flag whose absence was reconstructed
    downstream by testing the value for falsiness. They declare optionality
    now, so no handler ever has to guess.
    """
    sentinels = [
        f"{path}: {kind} '{name}'"
        for path, kind, name, decl in _declarations()
        if _presence(decl) == "default" and decl.default == ""
    ]
    assert not sentinels, sentinels


def test_every_optional_flag_names_its_fallback_or_its_absence_in_help() -> None:
    """An optional declaration owes the reader what absence means.

    strictcli's ban leaves three remedies for a site that wanted a default, and
    the one this CLI took -- apply the fallback in the handler -- is legal only
    when the help says so. This asserts the help of every optional declaration
    talks about the omitted case.
    """
    silent = [
        f"{path}: {kind} '{name}'"
        for path, kind, name, decl in _declarations()
        if _presence(decl) == "optional"
        and "omit" not in decl.help.lower()
        and "when neither" not in decl.help.lower()
    ]
    assert not silent, silent


# --- What the declarations deliver ---


class TestOptionalDelivery:
    def test_absent_optional_string_reaches_the_handler_as_none(self) -> None:
        """`--cwd` unset is absence, not an empty string."""
        seen = {}

        def fake_session(config):
            seen["cwd"] = config.cwd
            ctx = MagicMock()
            session = MagicMock()
            session.send.return_value = []
            ctx.__enter__ = MagicMock(return_value=session)
            ctx.__exit__ = MagicMock(return_value=False)
            return ctx

        with patch("claudestream._cli.SyncSession", side_effect=fake_session):
            app.test(["send", "hi", "--model", "opus", "--profile", "test"])
        assert seen["cwd"] is None

    def test_supplied_optional_string_reaches_the_handler_verbatim(self) -> None:
        seen = {}

        def fake_session(config):
            seen["cwd"] = config.cwd
            ctx = MagicMock()
            session = MagicMock()
            session.send.return_value = []
            ctx.__enter__ = MagicMock(return_value=session)
            ctx.__exit__ = MagicMock(return_value=False)
            return ctx

        with patch("claudestream._cli.SyncSession", side_effect=fake_session):
            app.test(["send", "hi", "--model", "opus", "--profile", "test", "--cwd", "/tmp/x"])
        assert seen["cwd"] == "/tmp/x"

    @pytest.mark.parametrize("command", ["doctor", "config"])
    def test_absent_profile_resolves_nothing(self, command: str) -> None:
        """`if profile is not None` replaced `if profile`, so absence skips the step."""
        with patch("claudewheel.profile.resolve_profile") as resolver:
            app.test([command])
        resolver.assert_not_called()

    @pytest.mark.parametrize("command", ["doctor", "config"])
    def test_supplied_profile_is_resolved(self, command: str) -> None:
        with patch("claudewheel.profile.resolve_profile", return_value={}) as resolver:
            app.test([command, "--profile", "test"])
        resolver.assert_called_once_with("test")


class TestOptDefault:
    """`_opt` is the one place absence becomes the documented fallback."""

    def test_absence_becomes_the_fallback(self) -> None:
        assert _opt(None, True) is True
        assert _opt(None, False) is False

    def test_a_supplied_false_is_not_absence(self) -> None:
        """The bug the helper exists to prevent: `or` would swallow this."""
        assert _opt(False, True) is False

    def test_a_supplied_true_survives_a_false_fallback(self) -> None:
        assert _opt(True, False) is True


class TestOptionalBoolsKeepTheirFormerBehavior:
    """The switches that lost `default=` behave identically when omitted."""

    @pytest.mark.parametrize("cmd_func", [cmd_send, cmd_stream, cmd_events])
    @patch("claudestream._cli.SyncSession")
    def test_footer_defaults_on(self, mock_cls, cmd_func, capsys) -> None:
        from claudestream import Result

        session = MagicMock()
        session.send.return_value = [
            Result(type="result", duration_ms=12.0, total_cost_usd=0.5)
        ]
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=session)
        ctx.__exit__ = MagicMock(return_value=False)
        mock_cls.return_value = ctx

        cmd_func(None, prompt="hello", model="sonnet", profile="test")
        assert "Done" in capsys.readouterr().err

    @patch("claudestream._cli.SyncSession")
    def test_json_output_defaults_off(self, mock_cls, capsys) -> None:
        from claudestream import AssistantText

        session = MagicMock()
        session.send.return_value = [AssistantText(type="assistant_text", text="plain")]
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=session)
        ctx.__exit__ = MagicMock(return_value=False)
        mock_cls.return_value = ctx

        cmd_send(None, prompt="hello", model="sonnet", profile="test", footer=False)
        out = capsys.readouterr().out
        assert out == "plain"


class TestPromptAbsenceIsNotEmptiness:
    """The one behavior change: `""` is a supplied prompt, not an absent one."""

    def test_absent_prompt_is_none(self) -> None:
        color = Colorizer(use_color=False)
        assert _resolve_prompt(None, False, color) == 1

    def test_explicit_empty_prompt_conflicts_with_stdin(self, capsys) -> None:
        """Previously `""` was falsy, so `--stdin` silently won. It no longer does."""
        color = Colorizer(use_color=False)
        with patch("sys.stdin", StringIO("piped\n")):
            result = _resolve_prompt("", True, color)
        assert result == 1
        assert "cannot use both prompt argument and --stdin" in capsys.readouterr().err

    def test_absent_prompt_with_stdin_reads_stdin(self) -> None:
        color = Colorizer(use_color=False)
        with patch("sys.stdin", StringIO("piped\n")):
            assert _resolve_prompt(None, True, color) == "piped"


class TestNoUpdateCommands:
    """strictcli's §27 update construct, judged and declined.

    ``update_of`` declares a command that changes named properties of a named
    resource in place, and it buys the at-least-one-property rule plus a
    rendered write set. claudestream has no such command: every mutating
    command here spawns a Claude Code session, and a session is not a record
    with an identity and a property set. Pinning the absence keeps a future
    ``update_of`` from arriving without the judgment being redone.
    """

    def test_no_command_declares_an_update(self) -> None:
        declared = {path for path, cmd in _walk().items() if cmd.update_of is not None}
        assert declared == set()
