"""Keyfit — per-project, verified environments for AI coding tools."""

import sys

__version__ = "0.1.1"  # the only place the version lives: pyproject.toml reads it, `keyfit --version` prints it

# Checked before any module that needs 3.11 (tomllib) loads: macOS's own python3 is 3.9, and `bin/keyfit` from a
# checkout runs whatever python3 comes first on PATH. Kept to syntax old Pythons still parse.
if sys.version_info < (3, 11):
    raise SystemExit("keyfit needs Python 3.11 or newer, but this is Python %d.%d (%s).\n"
                     "  Install a newer one (brew install python@3.13), then run Keyfit with it, "
                     "for example: pipx install --python python3.13 keyfit" % (*sys.version_info[:2], sys.executable))
