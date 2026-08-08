"""The spend guard: integration opt-in state and the poisoned ``claude`` shim.

claudestream's integration tests drive the REAL Claude Code binary against a
real profile, which costs real money on every run. The default test lane must
therefore be spend-free *by construction*, not by convention:

- ``CLAUDESTREAM_INTEGRATION=1`` is the one and only opt-in signal. Without it
  every ``@pytest.mark.integration`` test is skipped at collection time
  (``conftest.pytest_collection_modifyitems``).
- In that same default lane, ``conftest.pytest_configure`` prepends a directory
  containing a poisoned ``claude`` executable to ``PATH``. Anything that
  resolves the binary by name -- ``shutil.which("claude")``,
  ``claudestream._process.find_binary()``, a stray subprocess spawn -- finds a
  shim that prints :data:`GUARD_MESSAGE` and exits :data:`GUARD_EXIT_CODE`
  instead of a binary that can talk to the API.

The two mechanisms are independent on purpose: the skip stops the known live
tests, and the poisoned PATH stops everything else, including code that has not
been written yet. ``tests/test_spend_guard.py`` pins both.
"""

from __future__ import annotations

import os
import shutil
import stat
import tempfile
from pathlib import Path

#: Environment variable that opts into the live (real binary, real spend) lane.
#: Its value must be exactly ``"1"`` -- no truthiness guessing, no implicit
#: default. Anything else means "default lane".
INTEGRATION_ENV = "CLAUDESTREAM_INTEGRATION"

#: Exit code of the poisoned shim. Distinctive so it is recognisable in a
#: traceback or a CI log.
GUARD_EXIT_CODE = 97

#: Text the poisoned shim writes to stderr.
GUARD_MESSAGE = (
    "claudestream spend guard: the real 'claude' binary is unreachable in the "
    "default test lane (this would have cost money). Set "
    f"{INTEGRATION_ENV}=1 to opt into the live lane."
)

#: Environment variable the guard exports so tests can locate the shim it
#: installed without re-deriving the temporary path.
GUARD_BINARY_ENV = "CLAUDESTREAM_SPEND_GUARD_BINARY"

_SHIM_SOURCE = f"""#!/bin/sh
echo "{GUARD_MESSAGE}" >&2
exit {GUARD_EXIT_CODE}
"""

#: Terminal header lines the guard writes on every pytest run rooted at this
#: repository, so its state is visible rather than inferred. ``ARMED_HEADER``
#: appearing is what proves the guard reached a given invocation --
#: ``tests/test_spend_guard.py`` asserts it for a path outside ``tests/``.
ARMED_HEADER = "claudestream spend guard: ARMED"
DISARMED_HEADER = "claudestream spend guard: DISARMED"

#: Skip reason attached to integration tests in the default lane.
SKIP_REASON = (
    f"live lane not enabled: set {INTEGRATION_ENV}=1 to run integration tests "
    "against the real claude binary (this spends real money)"
)


def live_lane_enabled() -> bool:
    """Return True when the live (real binary, real spend) lane is opted into."""
    return os.environ.get(INTEGRATION_ENV) == "1"


def install_poisoned_binary() -> Path:
    """Create a poisoned ``claude`` shim, put it first on ``PATH``, return it.

    Mutates ``os.environ`` (``PATH`` and :data:`GUARD_BINARY_ENV`) so the
    poisoning is inherited by every subprocess the suite spawns, not just by
    in-process ``shutil.which`` lookups.
    """
    directory = Path(tempfile.mkdtemp(prefix="claudestream-spend-guard-"))
    shim = directory / "claude"
    shim.write_text(_SHIM_SOURCE)
    shim.chmod(shim.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    os.environ["PATH"] = f"{directory}{os.pathsep}{os.environ.get('PATH', '')}"
    os.environ[GUARD_BINARY_ENV] = str(shim)
    return shim


def remove_poisoned_binary(shim: Path) -> None:
    """Undo :func:`install_poisoned_binary` for the shim it returned."""
    directory = shim.parent
    os.environ["PATH"] = os.pathsep.join(
        entry
        for entry in os.environ.get("PATH", "").split(os.pathsep)
        if entry != str(directory)
    )
    os.environ.pop(GUARD_BINARY_ENV, None)
    shutil.rmtree(directory, ignore_errors=True)
