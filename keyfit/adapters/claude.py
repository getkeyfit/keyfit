"""Claude Code: which claude.ai login (config directory) a project uses.

Account:  [accounts.claude.acme]
          config_dir = "~/.claude"   email = "…"   org_id = "…"   org_name = "Acme"
Project:  claude = "acme"

inject  — remove everything that could switch Claude to another identity or
          billing (API keys, OAuth tokens, gateway URLs, cloud providers,
          another config dir). The default login (~/.claude) is selected by
          leaving CLAUDE_CONFIG_DIR unset: Claude keys the Keychain entry on
          the variable's text, so an explicit ~/.claude would look for a
          different (empty) entry. Any other account gets CLAUDE_CONFIG_DIR.
remote  — `claude auth status --json` in that environment must report a
          claude.ai subscription login for the expected org and email. An
          inherited API key would show up as authMethod "api_key" — a failure.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from ..checks import Check
from ..realbin import find_real
from ..registry import canonical, write_private
from .base import Adapter

SCRUB = (
    "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_PROFILE",
    "ANTHROPIC_BASE_URL", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY",
    "CLAUDE_SECURESTORAGE_CONFIG_DIR", "CLAUDE_CONFIG_DIR",
)
# Files that follow the user into a new account's config dir (never credentials or .claude.json).
SHARED = ("settings.json", "keybindings.json", "CLAUDE.md", "skills", "commands", "agents")


def default_dir(env: dict) -> Path:
    return canonical(Path(env.get("HOME") or Path.home()) / ".claude")


def is_default(config_dir: str | Path, env: dict) -> bool:
    return canonical(config_dir) == default_dir(env)


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
        """The org recorded in the config dir's .claude.json (and the login, when pinned) — a change re-verifies."""
        config_dir = conn.get("config_dir", "~/.claude")
        state = (Path(env.get("HOME") or Path.home()) / ".claude.json" if is_default(config_dir, env)
                 else canonical(config_dir) / ".claude.json")
        try:
            account = json.loads(state.read_text()).get("oauthAccount") or {}
        except (OSError, ValueError):
            return ""
        who = account.get("accountUuid", "") if conn.get("pin_email") else ""
        return f"{who}:{account.get('organizationUuid', '')}"

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
        if canonical(status.get("configDirectory", "")) != canonical(config_dir):
            return Check(self.kind, "fail", label,
                         f"Claude used config directory {status.get('configDirectory')}", fix)
        if conn.get("org_id") and status.get("orgId") != conn["org_id"]:
            return Check(self.kind, "fail", label,
                         f"signed in to org '{status.get('orgName')}', project expects '{conn.get('org_name')}'", fix)
        # The organization is the identity (who pays, whose data). A specific login is pinned only on request:
        # pooling tools such as claude-swap rotate between logins of the same org.
        if conn.get("pin_email") and status.get("email") != conn.get("email"):
            return Check(self.kind, "fail", label, "signed in as a different user than the pinned one", fix)
        plan = status.get("subscriptionType") or "subscription"
        return Check(self.kind, "ok", label, f"{status.get('orgName') or 'personal'} ({plan}) confirmed")


def identity_from(status: dict, config_dir: str, pin_email: bool = False) -> dict:
    """The account record kept in the registry — identity labels only, no credentials."""
    record = {"config_dir": config_dir, "org_id": status.get("orgId", ""), "org_name": status.get("orgName", "")}
    if pin_email or not status.get("orgId"):  # personal accounts have no org: the login is the identity
        record.update(email=status.get("email", ""), pin_email=True)
    return {k: v for k, v in record.items() if v}


def slug(text: str) -> str:
    out = "".join(c.lower() if c.isalnum() else "-" for c in text).strip("-")
    while "--" in out:
        out = out.replace("--", "-")
    return out or "default"
