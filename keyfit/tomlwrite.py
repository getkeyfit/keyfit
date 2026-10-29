"""Minimal TOML writer for the registry (stdlib has tomllib for reading only).

Rule: a dict whose values are *all* dicts is a group of sections and is
recursed into (``projects`` → ``[projects.acme]``, ``accounts`` →
``[accounts.github.octocat]``). Any other dict is written as one section
(``[projects.acme]``, ``[settings]``) whose dict values become inline tables
(``aws = { profile = "…", account = "…" }``).
Supports strings, booleans, integers and arrays of scalars.
"""

from __future__ import annotations

import re

_BARE_KEY = re.compile(r"^[A-Za-z0-9_-]+$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")  # TOML reads these only escaped: one written raw makes the file unreadable


def _key(k: str) -> str:
    return k if _BARE_KEY.match(k) else _string(k)


def _string(s: str) -> str:
    escaped = s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\t", "\\t")
    escaped = _CONTROL.sub(lambda m: f"\\u{ord(m.group()):04X}", escaped)
    return f'"{escaped}"'


def _value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, str):
        return _string(v)
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_value(i) for i in v) + "]"
    if isinstance(v, dict):
        return "{ " + ", ".join(f"{_key(k)} = {_value(x)}" for k, x in v.items()) + " }"
    raise TypeError(f"unsupported TOML value: {type(v).__name__}")


def _is_group(d: dict) -> bool:
    return bool(d) and all(isinstance(v, dict) for v in d.values())


def dumps(data: dict) -> str:
    lines: list[str] = []

    def emit(prefix: list[str], d: dict) -> None:
        if _is_group(d):
            for k, v in d.items():
                emit(prefix + [k], v)
            return
        if not prefix:
            for k, v in d.items():  # top-level scalars (none in the registry today)
                lines.append(f"{_key(k)} = {_value(v)}")
            return
        if not d:
            return
        lines.append("[" + ".".join(_key(p) for p in prefix) + "]")
        for k, v in d.items():
            lines.append(f"{_key(k)} = {_value(v)}")
        lines.append("")

    emit([], data)
    return ("\n".join(lines)).rstrip("\n") + "\n"
