"""Build a project's environment and verify it — the core the shims, `status` and `run` share."""

from __future__ import annotations

import os
import time

from . import cache
from .adapters import ADAPTERS
from .checks import Check, human
from .registry import Project, default_path, tilde


def _selected(project: Project, kinds) -> list[tuple[str, object]]:
    """kinds=None means every connection of the project."""
    return [(kind, conn) for kind, conn in project.connections.items() if kinds is None or kind in kinds]


def build(project: Project, base_env: dict | None = None, kinds=None) -> dict:
    """Start from the given environment, then let the selected adapters scrub and set their variables."""
    env = dict(os.environ if base_env is None else base_env)
    env["KEYFIT_PROJECT"] = project.name
    labels = []
    for kind, conn in _selected(project, kinds):
        adapter = ADAPTERS.get(kind)
        if adapter is not None:
            adapter.inject(conn, env)
            labels.append(f"{kind} {adapter.label(conn)}")
    env["KEYFIT_SUMMARY"] = " · ".join([project.name, *labels])  # shown by the Claude status line
    return env


def verify(project: Project, env: dict, remote: bool = True, kinds=None, use_cache: bool = False) -> list[Check]:
    """Check the selected connections. Remote checks run in parallel; successes are cached."""

    def one(item):
        kind, conn = item
        adapter = ADAPTERS.get(kind)
        if adapter is None:  # a typo (`githb = …`) or a newer Keyfit's kind: that account would go unchecked
            return Check(kind, "fail", "?", "not a kind of account this Keyfit knows",
                         f"fix or remove it in {tilde(default_path())} (known: {', '.join(sorted(ADAPTERS))})")
        if not remote:
            return adapter.verify(conn, env, remote=False)
        local = adapter.local(conn, env)
        if local.failed:
            return local
        k = cache.key(project.name, kind, conn, adapter.fingerprint(conn, env))
        if use_cache:
            hit = cache.get(k)
            if hit is not None:
                return local.merge(Check(kind, hit["status"], local.label, hit["detail"]))
        result = adapter.remote(conn, env)
        if result.failed and result.transient:
            result = adapter.remote(conn, env)  # one retry for a network blip
        if result.failed and result.transient:
            last = cache.get(k, max_age=cache.STALE)
            if last is not None:
                age = human(time.time() - last["at"])
                return local.merge(Check(kind, "warn", local.label,
                                         f"{last['detail']} {age} ago · could not re-check now ({result.detail})"))
        if not result.failed:
            cache.put(k, result.status, result.detail)
        return local.merge(result)

    items = _selected(project, kinds)
    if len(items) < 2:  # aws, gh and vercel check one connection: no thread pool (or its ~5 ms import) needed
        return [one(item) for item in items]
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=len(items)) as pool:
        return list(pool.map(one, items))
