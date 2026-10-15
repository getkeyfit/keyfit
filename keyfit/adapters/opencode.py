"""OpenCode: which sign-ins (OpenCode's login database) a project's OpenCode sessions run on.

Account:  [accounts.opencode.work]
          db = "~/.config/keyfit/opencode/work/opencode.db"
          logins = ["anthropic", "openai"]   (the providers signed in there when the account was added)
Project:  opencode = "work"

OpenCode 2 keeps every sign-in (`opencode auth login`, API keys too) in its SQLite database next to the sessions:
~/.local/share/opencode/opencode.db (under $XDG_DATA_HOME when set), or the file OPENCODE_DB names. Its configuration
(~/.config/opencode, or OPENCODE_CONFIG_DIR, which Superset's wrapper sets) stays shared. A sign-in is labelled with
the provider's name ("OpenAI", "OpenAI 2"), not the person, so an account is checked by the providers signed in,
not down to the person. Sessions normally run in OpenCode's background server, which serves every folder and keeps
the environment of the start that launched it, so the shim starts them with --standalone (a private server with the
session's environment), as it starts Codex without its daemon.

inject  — point OPENCODE_DB at the account's database. The provider keys OpenCode would take from the shell next to
          the database's sign-ins are removed only where OpenCode starts (scrub: the shim and the remote check), so
          other agents' sessions keep them: always those of Anthropic, OpenAI, Google, GitHub Copilot and OpenCode
          Zen, and every other key OpenCode's own report named at the last check (OpenRouter, Groq, xAI, …).
forget  — in a project without an OpenCode account, the OPENCODE_DB Keyfit set for another project goes.
local   — the database holds a sign-in, and one for each provider recorded with the account (read with SQLite,
          read-only: provider and label, never a credential).
remote  — `opencode auth list --standalone` in that environment (with no configuration loaded, so no MCP server
          starts) must list those sign-ins as stored. Every key or token it lists as coming from the environment is
          from your shell, not the project's account: the check warns, naming it, and the shim leaves it out. No
          provider is asked: there is no cheap call that confirms a sign-in without using it, so this checks what
          OpenCode would use.
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
import tempfile
import urllib.parse
from pathlib import Path

from ..checks import Check
from ..realbin import find_real
from ..registry import canonical, tilde
from .base import Adapter

MARK = "KEYFIT_OPENCODE"  # set next to the OPENCODE_DB Keyfit injected: the account's name
# Shell variables OpenCode 2.0.25 takes as sign-ins of Anthropic, OpenAI, Google, GitHub Copilot and OpenCode Zen/Go
# (`opencode auth list` shows them as "environment"), removed without asking. Keys of the other providers it knows
# (OpenRouter, Groq, Bedrock, Cloudflare, GitLab, …) are removed once the check has seen them in its report.
PROVIDER_KEYS = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_GENERATIVE_AI_API_KEY",
                 "GOOGLE_API_KEY", "GITHUB_TOKEN", "OPENCODE_API_KEY")


def scrub(env: dict, seen=()) -> None:
    """Remove the provider keys an OpenCode of a project with an OpenCode account must not take from the shell: the
    fixed ones, and those the check *seen* in OpenCode's report."""
    for var in (*PROVIDER_KEYS, *seen):
        env.pop(var, None)


def key_like(name: str) -> bool:
    """A variable that can hold a key or token (OPENROUTER_API_KEY, HF_TOKEN, AWS_BEARER_TOKEN_BEDROCK). OpenCode also
    lists settings such as CLOUDFLARE_ACCOUNT_ID, which Keyfit sets for the project's Cloudflare account: no sign-in."""
    return ("KEY" in name or "TOKEN" in name) and not name.startswith("KEYFIT_")


def default_db(env: dict) -> Path:
    data = env.get("XDG_DATA_HOME") or Path(env.get("HOME") or Path.home()) / ".local" / "share"
    return canonical(Path(data) / "opencode" / "opencode.db")


def database(conn, env: dict) -> Path:
    return canonical(conn.get("db") or default_db(env))


def sign_in(db: Path, env: dict, provider: str | None = None) -> str:
    """The sign-in command for this database. --standalone: a background server that is already running would take
    the sign-in to its own database."""
    prefix = "" if canonical(db) == default_db(env) else f"OPENCODE_DB={shlex.quote(str(canonical(db)))} "
    return f"{prefix}opencode auth login{' ' + provider if provider else ''} --standalone"


def _rows(db: Path, columns: str) -> tuple[list | None, str]:
    """Rows of the sign-ins table, read-only. Never the column that holds the credential."""
    if not db.is_file():
        return None, "not signed in (no OpenCode database yet)"
    import sqlite3  # ~5 ms to import: only where an OpenCode account is checked

    try:
        con = sqlite3.connect(f"file:{urllib.parse.quote(str(db))}?mode=ro", uri=True, timeout=2)
        try:
            return con.execute(f"SELECT {columns} FROM credential ORDER BY time_created").fetchall(), ""
        finally:
            con.close()
    except sqlite3.Error as err:
        return None, f"cannot read the sign-ins in {tilde(db)} ({err})"


def signed_in(db: Path) -> tuple[dict | None, str]:
    """{provider: [labels]} of the sign-ins in an OpenCode database, or None and why not. Sign-ins of MCP servers
    (provider mcp_…) are left out: the account is about the model providers."""
    rows, reason = _rows(db, "integration_id, label")
    if rows is None:
        return None, reason
    found: dict[str, list[str]] = {}
    for provider, label in rows:
        if isinstance(provider, str) and not provider.startswith("mcp_"):
            found.setdefault(provider, []).append(str(label))
    return found, ""


def auth_list(env: dict, timeout: float = 30) -> tuple[list | None, str]:
    """`opencode auth list --standalone --format json` in *env*, from the real opencode (never a wrapper or shim),
    with an empty configuration: no MCP server or plugin starts, no model list or update is fetched. It needs no
    network (OpenCode 2.0.25 answered with every outbound connection but localhost blocked)."""
    binary = find_real("opencode", env, skip_wrappers=True)
    if binary is None:
        return None, "opencode not found on PATH"
    with tempfile.TemporaryDirectory(prefix="keyfit-opencode-") as empty:
        quiet = {k: v for k, v in env.items() if k not in ("OPENCODE_CONFIG", "OPENCODE_CONFIG_CONTENT")}
        scrub(quiet)  # as the shim starts OpenCode, before it knows what else this report names
        quiet.update(OPENCODE_CONFIG_DIR=empty, OPENCODE_DISABLE_PROJECT_CONFIG="1", OPENCODE_DISABLE_MODELS_FETCH="1",
                     OPENCODE_DISABLE_AUTOUPDATE="1")
        try:
            out = subprocess.run([binary, "auth", "list", "--standalone", "--format", "json"], env=quiet, cwd=empty,
                                 capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL, check=False)
        except subprocess.TimeoutExpired:
            return None, f"opencode auth list did not answer within {timeout:g} s"
        except OSError as err:
            return None, f"cannot run opencode ({err.strerror})"
    try:
        listing = json.loads(out.stdout)
    except ValueError:
        listing = None
    if out.returncode == 0 and isinstance(listing, list):
        return listing, ""
    lines = [line for line in out.stderr.splitlines() if line.strip()]
    return None, (lines[-1] if lines else "unreadable output from opencode auth list")[:200]


class OpenCodeAdapter(Adapter):
    kind = "opencode"

    def __init__(self, timeout: float = 30):
        self.timeout = timeout

    def label(self, conn) -> str:
        return conn.get("name", "?")

    def inject(self, conn, env: dict) -> None:
        env["OPENCODE_DB"] = str(database(conn, env))
        env[MARK] = conn.get("name", "")

    def forget(self, env: dict) -> None:
        if MARK in env:  # Keyfit pointed OpenCode at another project's database
            for var in ("OPENCODE_DB", MARK):
                env.pop(var, None)

    def _wanted(self, conn) -> list[str]:
        logins = conn.get("logins")
        return [p for p in logins if isinstance(p, str)] if isinstance(logins, list) else []

    def local(self, conn, env: dict) -> Check:
        label = self.label(conn)
        db, wanted = database(conn, env), self._wanted(conn)
        found, reason = signed_in(db)
        if found is None:
            return Check(self.kind, "fail", label, reason, sign_in(db, env, wanted[0] if wanted else None))
        missing = [p for p in wanted if p not in found]
        if missing:
            return Check(self.kind, "fail", label, f"not signed in to {', '.join(missing)} in {tilde(db)}",
                         sign_in(db, env, missing[0]))
        if not found:
            return Check(self.kind, "fail", label, f"not signed in ({tilde(db)} holds no sign-in)", sign_in(db, env))
        return Check(self.kind, "ok", label, f"{', '.join(sorted(found))} signed in", pending=True)

    def fingerprint(self, conn, env: dict) -> str:
        """Changes with the sign-ins (a new one, one removed, another selected) and with the names of key-like
        variables in the environment, so a cached check never vouches for another sign-in, and the shell keys it
        names (which the shim removes) are those of this environment."""
        rows, _ = _rows(database(conn, env), "id, integration_id, label, active")
        keys = sorted(k for k in env if key_like(k))
        return hashlib.sha256(json.dumps([rows or [], keys]).encode()).hexdigest()[:16]

    def remote(self, conn, env: dict) -> Check:
        label = self.label(conn)
        db, wanted = database(conn, env), self._wanted(conn)
        listing, reason = auth_list(env, self.timeout)
        if listing is None:
            fix = "brew install opencode" if "not found" in reason else \
                "see why inside the project: opencode auth list   (Keyfit adds --standalone)"
            return Check(self.kind, "fail", label, reason, fix, transient="did not answer" in reason)
        stored: dict[str, str] = {}
        shell: list[str] = []
        for entry in listing:
            if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
                continue
            for item in entry.get("connections") or []:
                if not isinstance(item, dict):
                    continue
                var = item.get("name")
                if item.get("type") == "credential":
                    stored[entry["id"]] = str(entry.get("name") or entry["id"])
                elif item.get("type") == "env" and isinstance(var, str) and key_like(var) and var not in shell:
                    shell.append(var)
        missing = [p for p in wanted if p not in stored]
        if missing:
            return Check(self.kind, "fail", label,
                         f"OpenCode does not see the {', '.join(missing)} sign-in in {tilde(db)}",
                         sign_in(db, env, missing[0]))
        if not stored:
            return Check(self.kind, "fail", label, f"OpenCode sees no sign-in in {tilde(db)}", sign_in(db, env))
        if shell:  # the shim leaves them out: OpenCode would offer their providers, billed to your own keys
            return Check(self.kind, "warn", label, f"confirmed by OpenCode · {', '.join(sorted(shell))} from your "
                         "shell, not the project's account: left out of OpenCode sessions", scrub=tuple(sorted(shell)))
        return Check(self.kind, "ok", label, "confirmed by OpenCode")
