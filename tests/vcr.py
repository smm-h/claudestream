"""Test-side helpers for the recorded-replay (VCR) lane.

The replay lane runs claudestream's real stack against
``tests/fixtures/claude_vcr.py`` instead of the real ``claude`` binary, so the
protocol tests cost nothing and need no credentials while still going through
``asyncio.create_subprocess_exec``, real pipes and real NDJSON encoding.

Two redirections make that work, and neither is a monkeypatch:

- **The binary** is redirected through ``SessionConfig.binary``, a first-class
  field, so ``find_binary`` is exercised exactly as in production.
- **The profile** is redirected through claudewheel's own workspace knob,
  ``CLAUDEWHEEL_CONFIG_DIR``. :func:`fake_workspace` builds a throwaway
  workspace holding a single ``replay`` profile, so ``resolve_profile`` runs for
  real yet never reads the developer's ``~/.claudewheel`` and never yields a
  token.
"""

from __future__ import annotations

import os
from pathlib import Path

from claudestream._options import SessionConfig

TESTS_DIR = Path(__file__).resolve().parent

#: The stand-in binary. Executable, stdlib-only, see its module docstring.
VCR_BINARY = TESTS_DIR / "fixtures" / "claude_vcr.py"

#: Where recorded transcripts live.
TRANSCRIPTS_DIR = TESTS_DIR / "fixtures" / "transcripts"

#: Profile name inside the throwaway workspace built by :func:`fake_workspace`.
REPLAY_PROFILE = "replay"

MODE_ENV = "CLAUDESTREAM_VCR_MODE"
CASSETTE_ENV = "CLAUDESTREAM_VCR_CASSETTE"
REAL_BINARY_ENV = "CLAUDESTREAM_VCR_REAL_BINARY"
WORKSPACE_ENV = "CLAUDEWHEEL_CONFIG_DIR"


def cassette(name: str) -> Path:
    """Return the path of a named cassette (``name`` without the extension)."""
    return TRANSCRIPTS_DIR / f"{name}.jsonl"


def available_cassettes() -> list[str]:
    """Names of every recorded cassette, sorted."""
    if not TRANSCRIPTS_DIR.is_dir():
        return []
    return sorted(path.stem for path in TRANSCRIPTS_DIR.glob("*.jsonl"))


def fake_workspace(root: Path) -> str:
    """Build a throwaway claudewheel workspace under *root* and return its profile.

    The profile carries a ``settings.json`` and no credentials, which is what
    makes it discoverable by ``resolve_profile`` yet tokenless. Pointing
    claudewheel at it is the caller's job: set :data:`WORKSPACE_ENV` to *root*
    (``monkeypatch.setenv`` in tests, so it is undone afterwards).
    """
    profile_dir = root / "profiles" / REPLAY_PROFILE
    profile_dir.mkdir(parents=True, exist_ok=True)
    (profile_dir / "settings.json").write_text("{}\n", encoding="utf-8")
    return REPLAY_PROFILE


def replay_env(cassette_path: Path) -> dict[str, str]:
    """Subprocess env that puts the stand-in binary into replay mode."""
    return {MODE_ENV: "replay", CASSETTE_ENV: str(cassette_path)}


def record_env(cassette_path: Path, real_binary: str) -> dict[str, str]:
    """Subprocess env that puts the stand-in binary into record mode."""
    return {
        MODE_ENV: "record",
        CASSETTE_ENV: str(cassette_path),
        REAL_BINARY_ENV: real_binary,
    }


def replay_config(cassette_path: Path, *, profile: str, **kwargs) -> SessionConfig:
    """Build a ``SessionConfig`` that replays *cassette_path*.

    ``model`` defaults to ``haiku`` because that is what the recordings used;
    every other field is passed straight through.
    """
    env = {**replay_env(cassette_path), **kwargs.pop("env", {})}
    return SessionConfig(
        model=kwargs.pop("model", "haiku"),
        profile=profile,
        binary=str(VCR_BINARY),
        env=env,
        **kwargs,
    )
