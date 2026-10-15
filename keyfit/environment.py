"""Build a project's environment and verify it — the core the shims, `status` and `run` share."""

from __future__ import annotations

import os
import time

from . import cache
from .adapters import ADAPTERS
from .checks import Check, human
from .registry import Project, default_path, tilde


def label(kind: str, conn) -> str:
    adapter = ADAPTERS.get(kind)
    return adapter.label(conn) if adapter is not None and isinstance(conn, dict) else str(conn)


def unusable(kind: str, conn) -> Check | None:
    """A connection the registry holds in a form Keyfit cannot use (a hand edit): it fails, so the tool does not
    start without it, and says how to fix it. None for a usable one."""
    registry, shown = tilde(default_path()), conn if isinstance(conn, str) else ""
    if kind not in ADAPTERS:
        return Check(kind, "fail", shown, f"unknown connection (a typo? Keyfit knows {', '.join(ADAPTERS)})",
                     f"rename or remove {kind} in {registry}")
    if isinstance(conn, dict):
        return None
    reason = f"no account [accounts.{kind}.{conn}]" if shown else f"not a table ({kind} = {{ … }})"
    return Check(kind, "fail", shown, reason, f"map it again: keyfit add {kind} …   (or fix it in {registry})")


def _selected(project: Project, kinds) -> list[tuple[str, object]]:
    """kinds=None means every connection of the project."""
    return [(kind, conn) for kind, conn in project.connections.items() if kinds is None or kind in kinds]


def build(project: Project, base_env: dict | None = None, kinds=None) -> dict:
    """Start from the given environment, then let the selected adapters scrub and set their variables."""
    env = dict(os.environ if base_env is None else base_env)
    env["KEYFIT_PROJECT"] = project.name
    for kind, adapter in ADAPTERS.items():  # a session of another project may have left its credentials behind
        if kind not in project.connections and (kinds is None or kind in kinds):
            adapter.forget(env)
    labels = []
    # Copilot CLI's account last: when it is the project's GitHub login, it reuses the token the GitHub account read
    for kind, conn in sorted(_selected(project, kinds), key=lambda item: item[0] == "copilot"):
        if unusable(kind, conn) is None:  # an unusable one fails its check; nothing is injected for it
            ADAPTERS[kind].inject(conn, env)
            labels.append(f"{kind} {label(kind, conn)}")
    env["KEYFIT_SUMMARY"] = " · ".join([project.name, *labels])  # shown by the Claude status line
    return env


def _names(entry: dict) -> tuple:
    """The variable names a cached check found in the shell."""
    names = entry.get("scrub")
    return tuple(n for n in names if isinstance(n, str)) if isinstance(names, list) else ()


def verify(project: Project, env: dict, remote: bool = True, kinds=None, use_cache: bool = False) -> list[Check]:
    """Check the selected connections. Remote checks run in parallel; successes are cached."""

    def one(item):
        kind, conn = item
        broken = unusable(kind, conn)
        if broken is not None:
            return broken
        adapter = ADAPTERS[kind]
        if not remote:
            return adapter.verify(conn, env, remote=False)
        local = adapter.local(conn, env)
        if local.failed:
            return local
        k = cache.key(project.name, kind, conn, adapter.fingerprint(conn, env))
        if use_cache:
            hit = cache.get(k)
            if hit is not None:
                return local.merge(Check(kind, hit["status"], local.label, hit["detail"], scrub=_names(hit)))
        result = adapter.remote(conn, env)
        if result.failed and result.transient:
            result = adapter.remote(conn, env)  # one retry for a network blip
        if result.failed and result.transient:
            last = cache.get(k, max_age=cache.STALE)
            if last is not None:
                age = human(time.time() - last["at"])
                return local.merge(Check(kind, "warn", local.label,
                                         f"{last['detail']} {age} ago · could not re-check now ({result.detail})",
                                         scrub=_names(last)))
        if not result.failed:
            cache.put(k, result.status, result.detail, result.scrub)
        return local.merge(result)

    items = _selected(project, kinds)
    if len(items) < 2:  # aws, gh, vercel, supabase, wrangler, convex: one connection, no thread pool (~5 ms)
        return [one(item) for item in items]
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=len(items)) as pool:
        return list(pool.map(one, items))
