"""Which MCP servers a session may see: the project's own account servers, never another project's.

Your existing servers keep their names (linear_acme, …) because your agent
guidance routes by those names. For a session in project P, a server is hidden when
- it is a Linear server reading another key variable than P's (Linear servers in the
  folder's .mcp.json are hidden unless P names them — they carry unverified keys);
- its name matches another project's `mcp_servers` patterns and none of P's.
A server P owns (`keyfit add mcp`) is always visible in P, even when another
project owns it too — e.g. a server signed in to a team both projects share.
Claude gets `--settings` with `deniedMcpServers` (user scope) and
`disabledMcpjsonServers` (.mcp.json); Codex gets `-c mcp_servers.<name>.enabled=false`;
OpenCode gets the servers switched off in OPENCODE_CONFIG_CONTENT (opencode.json in the
folder and the folders above it counts like .mcp.json).
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
# Codex splits a `-c` key on every dot and the whole option on its first "=", without quoting
# (a quoted name makes Codex refuse to start), so such a server name cannot be addressed.
CODEX_UNADDRESSABLE = re.compile(r"[.=]|^\s|\s$")


class Unhideable(Exception):
    """A server Codex must not see in this project, but that Codex cannot be told to hide."""


class Unreadable(Exception):
    """OPENCODE_CONFIG_CONTENT from the environment is not a JSON object, so no server can be added to it."""


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
        if not name or CODEX_UNADDRESSABLE.search(name):
            raise Unhideable(name)
        out += ["-c", f"mcp_servers.{name}.enabled=false"]
    return out


# OpenCode: the user's config folder (OPENCODE_CONFIG_DIR replaces it, as Superset's wrapper does), OPENCODE_CONFIG,
# then opencode.json(c) and .opencode/opencode.json(c) in the folder and every folder above it, then
# OPENCODE_CONFIG_CONTENT, the last and strongest. A server is switched off only by a whole entry there (OpenCode
# 2.0.25 drops a document with {"enabled": false} alone); a disabled one never starts, whatever its own type was.
OPENCODE_FILES = ("opencode.json", "opencode.jsonc")
OPENCODE_OFF = {"type": "local", "command": ["false"], "enabled": False}
OPENCODE_OWN = "KEYFIT_OPENCODE_CONFIG"  # your own OPENCODE_CONFIG_CONTENT while Keyfit's is set ("" for none)
OPENCODE_VAR = re.compile(r"\{env:([A-Za-z_][A-Za-z0-9_]*)\}")


def opencode_config_dir(env: dict) -> Path:
    if env.get("OPENCODE_CONFIG_DIR"):
        return Path(env["OPENCODE_CONFIG_DIR"]).expanduser()
    return Path(env.get("XDG_CONFIG_HOME") or Path(env.get("HOME") or Path.home()) / ".config") / "opencode"


def _opencode_where(mcp: dict) -> dict:
    """Where a config's `mcp` table keeps the servers: by name, or (OpenCode 2's own form) under `servers`."""
    inner = mcp.get("servers")
    if isinstance(inner, dict) and "type" not in inner and all(isinstance(v, dict) for v in inner.values()):
        return inner
    return mcp


def _opencode_table(data) -> dict:
    """The servers of one OpenCode config, by name."""
    mcp = data.get("mcp") if isinstance(data, dict) else None
    if not isinstance(mcp, dict):
        return {}
    return {name: spec for name, spec in _opencode_where(mcp).items() if isinstance(spec, dict)}


def _opencode_file(path: Path) -> dict:
    from .adapters.cloudflare import _jsonc  # JSON with comments and trailing commas, as opencode.jsonc allows

    try:
        return _opencode_table(json.loads(_jsonc(path.read_text())))
    except (OSError, UnicodeDecodeError, ValueError):
        return {}


def opencode_servers(env: dict, cwd: Path) -> list[tuple[str, dict, bool]]:
    """(name, spec, verified) of the servers OpenCode loads in *cwd* from files: the user's (verified) and the
    folder's and its parents' (unverified, like .mcp.json)."""
    mine = [opencode_config_dir(env) / name for name in OPENCODE_FILES]
    if env.get("OPENCODE_CONFIG"):
        mine.append(cwd / Path(env["OPENCODE_CONFIG"]).expanduser())
    out = [(name, spec, True) for path in mine for name, spec in _opencode_file(path).items()]
    seen = {p.resolve() for p in mine}
    for folder in (cwd, *cwd.parents):
        for path in (*(folder / n for n in OPENCODE_FILES), *(folder / ".opencode" / n for n in OPENCODE_FILES)):
            if path.is_file() and path.resolve() not in seen:
                out += [(name, spec, False) for name, spec in _opencode_file(path).items()]
    return out


def _opencode_var(spec: dict) -> str | None:
    for table in (spec.get("headers"), spec.get("environment")):
        for value in (table or {}).values() if isinstance(table, dict) else ():
            m = OPENCODE_VAR.search(str(value))
            if m:
                return m.group(1)
    return None


def opencode_environment(project: Project, env: dict, cwd: Path, owners: dict | None = None) -> list[str]:
    """Switch off in OPENCODE_CONFIG_CONTENT every server a session of *project* must not see; return their names.
    Your own OPENCODE_CONFIG_CONTENT is kept, and remembered in KEYFIT_OPENCODE_CONFIG while Keyfit's is set, so a
    session started inside this one starts from yours, not from this project's list."""
    from .adapters.cloudflare import _jsonc

    own = env.pop(OPENCODE_OWN) if OPENCODE_OWN in env else env.get("OPENCODE_CONFIG_CONTENT", "")
    try:
        content = json.loads(_jsonc(own)) if own.strip() else {}
    except ValueError:
        content = None
    if not isinstance(content, dict):
        raise Unreadable()
    servers = opencode_servers(env, cwd) + [(name, spec, True) for name, spec in _opencode_table(content).items()]
    names = sorted({name for name, spec, verified in servers
                    if hidden(name, spec, project, owners, key_var=_opencode_var(spec), verified=verified)})
    if not names:
        if own:
            env["OPENCODE_CONFIG_CONTENT"] = own
        else:
            env.pop("OPENCODE_CONFIG_CONTENT", None)
        return []
    if not isinstance(content.get("mcp"), dict):
        content["mcp"] = {}
    table = _opencode_where(content["mcp"])
    for name in names:
        table[name] = dict(OPENCODE_OFF)
    env["OPENCODE_CONFIG_CONTENT"] = json.dumps(content)
    env[OPENCODE_OWN] = own
    return names
