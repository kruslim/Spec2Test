"""Which code produced a saved run, so runs of one code version can be grouped."""

from __future__ import annotations

import subprocess
from functools import cache
from pathlib import Path

from spec2test import __version__

_ROOT = Path(__file__).resolve().parents[2]


@cache
def code_version() -> str:
    """The package version plus the git commit it runs from: "0.0.1+00d8a8e", with
    ".dirty" when tracked files have uncommitted changes. Outside a git checkout (an
    installed wheel, a container image) it is the package version alone."""
    try:
        commit = _git("rev-parse", "--short", "HEAD")
        dirty = _git("status", "--porcelain", "--untracked-files=no")
    except (OSError, subprocess.SubprocessError):
        return __version__
    return f"{__version__}+{commit}{'.dirty' if dirty else ''}"


def _git(*args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(_ROOT), *args],
        capture_output=True,
        text=True,
        timeout=5,
        check=True,
    )
    return done.stdout.strip()
