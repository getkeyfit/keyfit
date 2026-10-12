"""Keyfit status line for Claude Code: this session's project and accounts, read from its own
environment (no network, no secrets). Claude sends session JSON on stdin; it is not needed here.

Run by path with `python -I` (see mcp.statusline_command), so it works the same from a checkout
and from an installed package; it imports nothing from Keyfit.
"""

import os
import sys


def main() -> int:
    sys.stdin.read()
    summary = os.environ.get("KEYFIT_SUMMARY")
    if summary:
        print(f"keyfit · {summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
