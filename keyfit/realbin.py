"""Find the real binary behind a name, skipping Keyfit's own shim directory.

Shims sit first on PATH; when a shim (or a check) needs the real `aws`,
`claude`, `gh` or `vercel`, it must not find itself again.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from .registry import canonical, config_dir


MARKER = ".keyfit-shims"  # written into every shim folder by `keyfit setup`


def shim_dir() -> Path:
    return config_dir() / "bin"


def stable_path(path: str | Path) -> str:
    """Homebrew keeps each version in Cellar/keyfit/<version> and deletes it on upgrade; opt/keyfit always
    points at the current one. Shims name the interpreter and package by that path so upgrades don't break them."""
    return re.sub(r"/Cellar/keyfit/[^/]+/", "/opt/keyfit/", str(path))


def is_shim_dir(folder: Path) -> bool:
    """A folder holding Keyfit shims, whatever KEYFIT_CONFIG_DIR says — finding a shim again would recurse."""
    return (folder / MARKER).exists()


BUILDS = re.compile(r"\.(?:paradev|superset)-.*")  # the home of another Paradev or Superset build: ~/.paradev-canary


def wrapper_dirs(env: dict) -> tuple[Path, ...]:
    """Other tools' agent wrappers, skipped when Keyfit runs a check itself: Paradev's (~/.paradev/bin) and Superset's,
    as Paradev was called before (~/.superset/bin), and those of their other builds (~/.paradev-*/bin,
    ~/.superset-*/bin). Every launch asks, so one listing of the home folder finds the builds of both."""
    home = Path(env.get("HOME") or Path.home())
    try:
        builds = sorted(name for name in os.listdir(home) if BUILDS.fullmatch(name))
    except OSError:
        builds = []
    return (home / ".paradev" / "bin", home / ".superset" / "bin", *(home / name / "bin" for name in builds))


def find_real(name: str, env: dict, skip: tuple[Path, ...] = (), skip_wrappers: bool = False) -> str | None:
    if skip_wrappers:
        skip = (*skip, *wrapper_dirs(env))
    skipped = {canonical(p) for p in skip} | {canonical(shim_dir())}
    for entry in env.get("PATH", "").split(os.pathsep):
        if not entry:
            continue
        try:
            here = canonical(entry)
        except OSError:  # a relative entry (`.`, node_modules/.bin) in a deleted folder: nothing runs from there
            continue
        if here in skipped or is_shim_dir(Path(entry)):
            continue
        candidate = Path(entry) / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None
