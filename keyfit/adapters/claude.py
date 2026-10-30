"""Claude Code: which claude.ai login (config directory) a project uses.

Account:  [accounts.claude.acme]
          config_dir = "~/.claude"   email = "…"   org_id = "…"   org_name = "Acme"
Project:  claude = "acme"

inject  — remove everything that could switch Claude to another identity or
          billing (API keys, OAuth tokens, a refresh token `claude auth login`
          would sign in with, gateway URLs and headers, cloud providers, another
          config dir). The default login (~/.claude) is selected by
          leaving CLAUDE_CONFIG_DIR unset: Claude keys the Keychain entry on
          the variable's text, so an explicit ~/.claude would look for a
          different (empty) entry. Any other account gets CLAUDE_CONFIG_DIR.
remote  — `claude auth status --json` in that environment must report a
          claude.ai subscription login for the expected org (any login). An
          inherited API key would show up as authMethod "api_key" — a failure.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

from ..checks import Check
from ..realbin import find_real
from ..registry import Project, Registry, canonical, write_private
from .base import Adapter

SCRUB = (
    "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_OAUTH_REFRESH_TOKEN",
    "ANTHROPIC_PROFILE", "ANTHROPIC_BASE_URL", "ANTHROPIC_CUSTOM_HEADERS", "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY", "CLAUDE_CODE_USE_ANTHROPIC_AWS", "CLAUDE_CODE_USE_MANTLE",
    "CLAUDE_SECURESTORAGE_CONFIG_DIR", "CLAUDE_CONFIG_DIR",
)
# Files that follow the user into a new account's config dir (never credentials or .claude.json). Claude keeps the
# plugins and marketplaces settings.json enables in the config dir too (`plugins`).
SHARED = ("settings.json", "keybindings.json", "CLAUDE.md", "skills", "commands", "agents", "plugins")


def default_dir(env: dict) -> Path:
    return canonical(Path(env.get("HOME") or Path.home()) / ".claude")


def is_default(config_dir: str | Path, env: dict) -> bool:
    return canonical(config_dir) == default_dir(env)


def own_folder(project: Project, registry: Registry, env: dict) -> Path | None:
    """The project's Claude config folder when it is the project's own: not the default login's (~/.claude, or the
    CLAUDE_CONFIG_DIR your shell sets) and no other project's Claude account is in it. Claude Code runs one background
    supervisor per config folder, so only there do background sessions keep to the project's accounts."""
    def folder(p: Project) -> Path | None:
        conn = p.connections.get("claude")
        given = (conn.get("config_dir") or "~/.claude") if isinstance(conn, dict) else None
        return canonical(given) if isinstance(given, str) else None  # not text (a hand edit): its check fails
    mine = folder(project)
    shell = None if env.get("KEYFIT_PROJECT") else env.get("CLAUDE_CONFIG_DIR")  # inside a session: the session's
    if mine is None or mine == default_dir(env) or (shell and canonical(shell) == mine):
        return None
    if any(folder(p) == mine for name, p in registry.projects().items() if name != project.name):
        return None
    return mine


def supervisor(config_dir: Path) -> str | None:
    """The project whose environment Claude's background supervisor for *config_dir* runs with (its KEYFIT_PROJECT;
    "" when it has none, or Keyfit can't read it), or None when none runs. Read only: the pid in the folder's
    daemon.lock, that process's start time against the lock's procStart (as Claude checks it: a pid gets reused), and
    its environment (`ps -E`), of which only that one name is kept."""
    try:
        lock = json.loads((config_dir / "daemon.lock").read_text())
        pid = int(lock["pid"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if pid <= 0:
        return None
    try:
        os.kill(pid, 0)
    except PermissionError:
        pass  # another user's process: ps tells
    except (OSError, OverflowError):
        return None
    try:
        out = subprocess.run(["/bin/ps", "-E", "-ww", "-o", "lstart=,command=", "-p", str(pid)], env={"TZ": "UTC"},
                             capture_output=True, text=True, timeout=5, stdin=subprocess.DEVNULL, check=False).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if not out.split():
        return None
    started = lock.get("procStart")
    if isinstance(started, str) and started.split() != out.split()[:5]:
        return None  # the supervisor is gone, and another process has its pid
    found = set(re.findall(r"(?<!\S)KEYFIT_PROJECT=(\S*)", out))
    return found.pop() if len(found) == 1 else ""


def login_command(config_dir: str | Path, env: dict) -> str:
    if is_default(config_dir, env):
        return "claude auth login"
    return f"CLAUDE_CONFIG_DIR={canonical(config_dir)} claude auth login"


def auth_status(env: dict, timeout: float = 30) -> tuple[dict | None, str]:
    """Run the real `claude auth status --json` (never a wrapper or shim) in *env*."""
    claude = find_real("claude", env, skip_wrappers=True)
    if claude is None:
        return None, "claude not found on PATH"
    try:
        out = subprocess.run([claude, "auth", "status", "--json"], env=env, capture_output=True, text=True,
                             timeout=timeout, stdin=subprocess.DEVNULL, check=False)
    except subprocess.TimeoutExpired:
        return None, f"claude auth status did not answer within {timeout:g} s"
    try:
        return json.loads(out.stdout), ""
    except ValueError:
        lines = [line for line in out.stderr.splitlines() if line.strip()]
        return None, (lines[-1] if lines else "unreadable output from claude auth status")[:200]


def _trusted(root: Path, env: dict) -> bool:
    """Whether *root* (or a folder above it) is trusted in the default account's ~/.claude.json."""
    try:
        projects = json.loads((Path(env.get("HOME") or Path.home()) / ".claude.json").read_text()).get("projects") or {}
    except (OSError, ValueError, AttributeError):
        return False
    path = canonical(root)
    return any((projects.get(str(folder)) or {}).get("hasTrustDialogAccepted") is True
               for folder in (path, *path.parents))


def seed(config_dir: Path, env: dict, trust: list[Path] = ()) -> list[str]:
    """Create a new account's config dir: shared settings symlinked from ~/.claude, and the project's repos
    pre-trusted where you already trust them in the default account (trust is what lets a repository's own
    hooks and settings run, so a new login never trusts more than the old one). Owner-only: Claude keeps
    the account's session transcripts and history there."""
    config_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    made = []
    source = default_dir(env)
    for item in SHARED:
        target, link = source / item, config_dir / item
        if target.exists() and not link.exists() and not link.is_symlink():
            link.symlink_to(target)
            made.append(item)
    state = config_dir / ".claude.json"
    if not state.exists():
        trust = [root for root in trust if _trusted(root, env)]
        projects = {str(canonical(root)): {"hasTrustDialogAccepted": True} for root in trust}
        write_private(state, json.dumps({"projects": projects}, indent=2))
        made.append(".claude.json (trusted: " + ", ".join(p.name for p in trust) + ")" if trust else ".claude.json")
    return made


class ClaudeAdapter(Adapter):
    kind = "claude"

    def __init__(self, timeout: float = 30):
        self.timeout = timeout

    def label(self, conn) -> str:
        name, org = conn.get("name", "?"), conn.get("org_name") or "?"
        return org if slug(org) == name else f"{name} · {org}"

    def inject(self, conn, env: dict) -> None:
        for var in SCRUB:
            env.pop(var, None)
        config_dir = conn.get("config_dir", "~/.claude")
        if not is_default(config_dir, env):
            env["CLAUDE_CONFIG_DIR"] = str(canonical(config_dir))

    def local(self, conn, env: dict) -> Check:
        label = self.label(conn)
        config_dir = conn.get("config_dir")
        if not config_dir:
            return Check(self.kind, "fail", label, "no config directory recorded", "keyfit add claude")
        if not canonical(config_dir).is_dir():
            return Check(self.kind, "fail", label, f"config directory {config_dir} does not exist",
                         f"keyfit add claude --name {conn.get('name', '<name>')} --new")
        if not (conn.get("org_id") or conn.get("email")):
            return Check(self.kind, "fail", label, "no expected identity recorded",
                         f"{login_command(config_dir, env)}  then: keyfit add claude --name {conn.get('name', '<name>')}")
        return Check(self.kind, "ok", label, "", pending=True)

    def fingerprint(self, conn, env: dict) -> str:
        """The org recorded in the config dir's .claude.json — a change re-verifies."""
        config_dir = conn.get("config_dir", "~/.claude")
        state = (Path(env.get("HOME") or Path.home()) / ".claude.json" if is_default(config_dir, env)
                 else canonical(config_dir) / ".claude.json")
        try:
            account = json.loads(state.read_text()).get("oauthAccount") or {}
        except (OSError, ValueError):
            return ""
        return account.get("organizationUuid", "")

    def remote(self, conn, env: dict) -> Check:
        label = self.label(conn)
        config_dir = conn["config_dir"]
        fix = login_command(config_dir, env)
        status, reason = auth_status(env, self.timeout)
        if status is None:
            return Check(self.kind, "fail", label, reason, fix, transient="did not answer" in reason)
        if not status.get("loggedIn"):
            return Check(self.kind, "fail", label, "not signed in", fix)
        if status.get("authMethod") != "claude.ai":
            return Check(self.kind, "fail", label,
                         f"signed in with {status.get('authMethod')} instead of a Claude subscription "
                         "(an API key or helper would change billing)",
                         "remove ANTHROPIC_API_KEY / apiKeyHelper from Claude settings, or: " + fix)
        used = status.get("configDirectory")  # Claude Code before 2.1.268 does not say: the org still has to match
        if used and canonical(used) != canonical(config_dir):
            return Check(self.kind, "fail", label, f"Claude used config directory {used}", fix)
        if conn.get("org_id") and status.get("orgId") != conn["org_id"]:
            return Check(self.kind, "fail", label,
                         f"signed in to org '{status.get('orgName')}', project expects '{conn.get('org_name')}'", fix)
        # The organization is the identity (who pays, whose data), never one login: account switchers such as
        # claude-swap move between logins.
        plan = status.get("subscriptionType") or "subscription"
        return Check(self.kind, "ok", label, f"{status.get('orgName') or 'personal'} ({plan}) confirmed")


def identity_from(status: dict, config_dir: str) -> dict:
    """The account record kept in the registry — identity labels only, no credentials."""
    record = {"config_dir": config_dir, "org_id": status.get("orgId", ""), "org_name": status.get("orgName", "")}
    if not status.get("orgId"):  # personal accounts have no org: the login it was added with, never checked
        record.update(email=status.get("email", ""))
    return {k: v for k, v in record.items() if v}


def slug(text: str) -> str:
    out = "".join(c.lower() if c.isalnum() else "-" for c in text).strip("-")
    while "--" in out:
        out = out.replace("--", "-")
    return out or "default"
