"""Which MCP servers a session may see: the project's own account servers, never another project's.

Your existing servers keep their names (linear_acme, …) because your agent
guidance routes by those names. For a session in project P, a server is hidden when
- it is a Linear server reading another key variable than P's (Linear servers in the
  folder's .mcp.json are hidden unless P names them — they carry unverified keys);
- its name matches another project's `mcp_servers` patterns and none of P's.
A server P owns (`keyfit add mcp`) is always visible in P, even when another
project owns it too — e.g. a server signed in to a team both projects share.
Claude gets `--settings` with `deniedMcpServers` (user scope) and
`disabledMcpjsonServers` (.mcp.json); Codex gets `-c mcp_servers.<name>.enabled=false`.
Every other server stays visible everywhere.
"""

from __future__ import annotations

import fnmatch
import json
import re
import shlex
import sys
import tomllib
import urllib.parse
from pathlib import Path

from . import secrets
from .realbin import stable_path
from .registry import Project

LINEAR_HOST = "mcp.linear.app"
LINEAR_API_KEY = "LINEAR_API_KEY"  # the generic variable Keyfit fills with the project's key
VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
BARE = re.compile(r"^[A-Za-z0-9_-]+$")


def _host(url) -> str:
    return urllib.parse.urlparse(url or "").netloc


def _project_linear(project: Project) -> tuple[str | None, str | None]:
    """(key variable, explicit server name) of the project's Linear connection."""
    conn = project.connections.get("linear") or {}
    ref = secrets.normalise(conn.get("key", "")) if conn else ""
    return (ref[4:] if ref.startswith("env:") else None), conn.get("mcp_server")


def _matches(name: str, patterns) -> bool:
    return any(fnmatch.fnmatchcase(name, pattern) for pattern in patterns)


def _owned_elsewhere(name: str, project: Project, owners: dict | None) -> bool:
    """True when another project owns this server name (callers check the project's own claim first)."""
    return any(_matches(name, patterns) for other, patterns in (owners or {}).items() if other != project.name)


def hidden(name: str, spec: dict, project: Project, owners: dict | None = None,
           key_var: str | None = None, verified: bool = True) -> bool:
    """Should a session of *project* not see this server? *key_var* is the variable it authenticates with;
    *verified* is False for servers from the folder's .mcp.json, whose Linear keys Keyfit never checked."""
    host = _host(spec.get("url"))
    if host == LINEAR_HOST:
        keep_var, keep_name = _project_linear(project)
        if name == keep_name:
            return False
        # Keyfit puts the project's checked key into LINEAR_API_KEY (and into the project's own env: variable),
        # so a server reading either one gets that key — also when the key lives in the Keychain.
        injected = {LINEAR_API_KEY, keep_var} - {None} if "linear" in project.connections else set()
        return not (verified and key_var in injected)
    if _matches(name, project.mcp_servers):
        return False
    return _owned_elsewhere(name, project, owners)


def claude_user_servers(env: dict) -> dict:
    folder = env.get("CLAUDE_CONFIG_DIR")
    state = Path(folder) / ".claude.json" if folder else Path(env.get("HOME") or Path.home()) / ".claude.json"
    try:
        return json.loads(state.read_text()).get("mcpServers") or {}
    except (OSError, ValueError):
        return {}


def _header_var(spec: dict) -> str | None:
    for value in (spec.get("headers") or {}).values():
        m = VAR.search(str(value))
        if m:
            return m.group(1)
    return None


def statusline_command() -> str:
    """Run by path with this interpreter, so the status line works from a checkout and an installed package."""
    script = Path(__file__).resolve().parent / "statusline.py"
    return shlex.join([stable_path(sys.executable), "-I", stable_path(script)])


def _user_statusline(env: dict) -> bool:
    """Respect a status line the user configured themselves."""
    folder = Path(env["CLAUDE_CONFIG_DIR"]) if env.get("CLAUDE_CONFIG_DIR") else Path(env.get("HOME") or Path.home()) / ".claude"
    try:
        return "statusLine" in json.loads((folder / "settings.json").read_text())
    except (OSError, ValueError):
        return False


def claude_settings(project: Project, env: dict, cwd: Path, owners: dict | None = None) -> dict:
    denied = [name for name, spec in claude_user_servers(env).items()
              if hidden(name, spec, project, owners, key_var=_header_var(spec))]
    try:
        local = json.loads((cwd / ".mcp.json").read_text()).get("mcpServers") or {}
    except (OSError, ValueError):
        local = {}
    disabled = [name for name, spec in local.items() if hidden(name, spec, project, owners, verified=False)]
    settings = {}
    if not _user_statusline(env):
        settings["statusLine"] = {"type": "command", "command": statusline_command()}
    if denied:
        settings["deniedMcpServers"] = [{"serverName": name} for name in denied]
    if disabled:
        settings["disabledMcpjsonServers"] = disabled
    return settings


def codex_servers(env: dict) -> dict:
    home = Path(env["CODEX_HOME"]) if env.get("CODEX_HOME") else Path(env.get("HOME") or Path.home()) / ".codex"
    try:
        return tomllib.loads((home / "config.toml").read_text()).get("mcp_servers") or {}
    except (OSError, ValueError):
        return {}


def codex_overrides(project: Project, env: dict, owners: dict | None = None) -> list[str]:
    out = []
    for name, spec in codex_servers(env).items():
        if not hidden(name, spec, project, owners, key_var=spec.get("bearer_token_env_var")):
            continue
        key = name if BARE.match(name) else json.dumps(name)
        out += ["-c", f"mcp_servers.{key}.enabled=false"]
    return out
