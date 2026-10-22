"""Successful remote checks, remembered so daily launches stay instant and a network blip does not block work.

Local checks run on every launch. A remote check is reused while it is fresh
(30 minutes) and while the connection and its fingerprint (a new sign-in, a
different key) are unchanged. When the network does not answer, a success from
the last 24 hours is shown as a warning instead of blocking. Only successes are
cached (with the names of the shell keys OpenCode reported): a failure or a
warning removes the entry, so the next launch checks again. The file holds
statuses, labels and variable names only — no secrets.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time

from .registry import config_dir, write_private

TTL = 30 * 60          # fresh: reused without asking the provider
STALE = 24 * 60 * 60   # kept: trusted (as a warning) only when the provider cannot be reached
_LOCK = threading.Lock()  # checks run in parallel threads; one writer at a time per process


def _path():
    return config_dir() / "cache" / "checks.json"


def key(project: str, kind: str, conn, fingerprint: str) -> str:
    return hashlib.sha256(json.dumps([project, kind, conn, fingerprint], sort_keys=True).encode()).hexdigest()


def _load() -> dict:
    try:
        return json.loads(_path().read_text())
    except (OSError, ValueError):
        return {}


def get(k: str, now: float | None = None, max_age: float = TTL) -> dict | None:
    entry = _load().get(k)
    now = time.time() if now is None else now
    if entry and now - entry.get("at", 0) < max_age:
        return entry
    return None


def put(k: str, status: str, detail: str, scrub=()) -> None:
    """Best effort: a lost entry only means one more remote check later; never break a launch. *scrub*: the names of
    the shell variables the check found that the tool must start without."""
    with _LOCK:
        now = time.time()
        data = {key_: v for key_, v in _load().items() if now - v.get("at", 0) < STALE}
        data[k] = {"at": now, "status": status, "detail": detail, **({"scrub": list(scrub)} if scrub else {})}
        _save(data)


def drop(k: str) -> None:
    """Forget one check after the provider answered otherwise. Best effort, like put; no write when it is not there."""
    with _LOCK:
        data = _load()
        if data.pop(k, None) is not None:
            _save(data)


def _save(data: dict) -> None:
    path = _path()
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        tmp = path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")  # unique per process and thread
        write_private(tmp, json.dumps(data))
        os.replace(tmp, path)
    except OSError:
        pass


def clear() -> None:
    try:
        _path().unlink()
    except OSError:
        pass
