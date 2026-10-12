"""Find the real binary behind a name, skipping Keyfit's own shim directory.

Shims sit first on PATH; when a shim (or a check) needs the real `aws`,
`claude` or `gh`, it must not find itself again.
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


def wrapper_dirs(env: dict) -> tuple[Path, ...]:
    """Other tools' agent wrappers (Superset's) — skipped when Keyfit runs a check itself."""
    home = Path(env.get("HOME") or Path.home())
    return (home / ".superset" / "bin", *sorted(home.glob(".superset-*/bin")))


def find_real(name: str, env: dict, skip: tuple[Path, ...] = (), skip_wrappers: bool = False) -> str | None:
    if skip_wrappers:
        skip = (*skip, *wrapper_dirs(env))
    skipped = {canonical(p) for p in skip} | {canonical(shim_dir())}
    for entry in env.get("PATH", "").split(os.pathsep):
        if not entry:
            continue
        if canonical(entry) in skipped or is_shim_dir(Path(entry)):
            continue
        candidate = Path(entry) / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None
