"""Build a project's environment and verify it — the core the shims, `status` and `run` share."""

from __future__ import annotations

import os
import time

from . import cache, secrets
from .adapters import ADAPTERS
from .checks import Check, human
from .registry import Project, default_path, tilde

CHECKS = "keyfit-check"  # the name of the threads that run checks in parallel


def label(kind: str, conn) -> str:
    adapter = ADAPTERS.get(kind)
    if adapter is not None and isinstance(conn, dict):
        return adapter.label(conn) if _odd(conn) is None else str(conn.get("name") or "?")
    return secrets.describe(str(conn)) if secrets.looks_like_key(str(conn)) else str(conn)  # a key written in by hand


def _odd(conn: dict) -> str | None:
    """A field of the connection that holds a number or a date where Keyfit writes text (a hand edit), else None."""
    return next((f for f, v in conn.items() if v is not None and not isinstance(v, (str, bool, list, dict))), None)


def unusable(kind: str, conn) -> Check | None:
    """A connection the registry holds in a form Keyfit cannot use (a hand edit): it fails, so the tool does not
    start without it, and says how to fix it. None for a usable one."""
    registry, shown = tilde(default_path()), conn if isinstance(conn, str) else ""
    key = secrets.looks_like_key(shown)
    if key:  # a key written in by hand (linear = "lin_api_…"): named, never shown
        shown = secrets.describe(shown)
    if kind not in ADAPTERS:
        return Check(kind, "fail", shown, f"unknown connection (a typo? Keyfit knows {', '.join(ADAPTERS)})",
                     f"rename or remove {kind} in {registry}")
    if isinstance(conn, dict):
        odd = _odd(conn)
        if odd is None:
            return None
        return Check(kind, "fail", str(conn.get("name") or ""), f"{odd} = {conn[odd]} is not text (a hand edit?)",
                     f'write it in quotes ({odd} = "…") in {registry}, or map it again: keyfit add {kind} …')
    reason = f"no account [accounts.{kind}.{conn}]" if shown else f"not a table ({kind} = {{ … }})"
    if key:
        reason = "a key, not an account name"
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
    """Check the selected connections. Remote checks run in parallel; successes are cached, and any other answer from
    the provider forgets the success before it."""

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
                trusted = local.merge(Check(kind, "warn", local.label,
                                            f"{last['detail']} {age} ago · could not re-check now ({result.detail})",
                                            scrub=_names(last)))
                trusted.transient = True  # the shim says so for every tool, not only the agents
                return trusted
        elif result.status == "ok" or result.scrub:  # and the shell keys OpenCode reported, by name, for its sessions
            cache.put(k, result.status, result.detail, result.scrub)
        else:  # a failure or a warning: the next launch asks again instead of trusting the success before it
            cache.drop(k)
        return local.merge(result)

    items = _selected(project, kinds)
    if len(items) < 2:  # aws, gh, vercel, supabase, wrangler, convex: one connection, no thread pool (~5 ms)
        return [one(item) for item in items]
    from concurrent.futures import ThreadPoolExecutor

    pool = ThreadPoolExecutor(max_workers=len(items), thread_name_prefix=CHECKS)
    try:
        return list(pool.map(one, items))
    finally:  # on Ctrl-C, return at once instead of waiting for the slowest check (`keyfit` then exits: cli.main)
        pool.shutdown(wait=False, cancel_futures=True)
