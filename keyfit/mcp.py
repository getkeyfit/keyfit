"""Which MCP servers a session may see: the project's own account servers, never another project's.

Your existing servers keep their names (linear_acme, …) because your agent
guidance routes by those names. For a session in project P, a server is hidden when
- it is a Linear server (mcp.linear.app in its address or command, or a LINEAR_… variable or a key in its env)
  and P has no Linear account, or it reads another key variable than P's, carries another key than P's or signs
  in inside the agent (OAuth); Linear servers in the folder's .mcp.json are hidden unless P names them — they
  carry unverified keys;
- its name matches another project's `mcp_servers` patterns or Google Workspace servers, and none of P's;
- it is a Google Workspace server (Gmail, Drive, Calendar: a known package, or Google's own remote
  servers) and P has Google Workspace servers of its own (google-mcp) that do not include it.
A server P owns (`keyfit add mcp`, `keyfit add google-mcp`) is always visible in P, even when another
project owns it too — e.g. a server signed in to a team both projects share.
Claude gets `--settings` with `deniedMcpServers` (user scope) and
`disabledMcpjsonServers` (.mcp.json); Codex gets `-c mcp_servers.<name>.enabled=false`;
OpenCode gets the servers switched off in OPENCODE_CONFIG_CONTENT (opencode.json in the
folder and the folders above it counts like .mcp.json); Copilot CLI gets
`--disable-mcp-server=NAME` (.mcp.json and .github/mcp.json up to the repository's top count
like .mcp.json); Gemini CLI gets `--allowed-mcp-server-names` with every server it may keep
(the folder's .gemini/settings.json counts like .mcp.json).
Every other server stays visible everywhere. Claude's local scope (projects[<folder>] in ~/.claude.json) counts like its
user scope, and Codex's profile files and trusted project config like its config.toml. A config file one of them
loads that can't be parsed stops the agent (Unreadable): its servers can't be hidden.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import shlex
import sys
import tomllib
import urllib.parse
from pathlib import Path

from . import secrets
from .realbin import stable_path
from .registry import Project

LINEAR_HOST = re.compile(r"mcp\.linear\.app", re.IGNORECASE)  # in an address (any case, port) or a command's words
LINEAR_KEY = re.compile(r"lin_(?:api|oauth)_[A-Za-z0-9]+")
LINEAR_API_KEY = "LINEAR_API_KEY"  # the generic variable Keyfit fills with the project's key
# A variable a definition names, in any agent's syntax: ${VAR} (and ${VAR:-default}), $VAR, OpenCode's {env:VAR}
REFERENCE = re.compile(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)|\{env:([A-Za-z_][A-Za-z0-9_]*)\}")
# Codex splits a `-c` key on every dot and the whole option on its first "=", without quoting
# (a quoted name makes Codex refuse to start), so such a server name cannot be addressed.
CODEX_UNADDRESSABLE = re.compile(r"[.=]|^\s|\s$")
CODEX_PROFILE = re.compile(r"[A-Za-z0-9_-]+")  # the names Codex takes for --profile


class Unhideable(Exception):
    """A server Codex must not see in this project, but that Codex cannot be told to hide."""


class Unreadable(Exception):
    """A config file an agent loads (*path*) can't be parsed, so its servers can't be hidden; without a path,
    OPENCODE_CONFIG_CONTENT from the environment is not a JSON object, so no server can be added to it."""

    def __init__(self, path: Path | None = None):
        super().__init__(str(path or ""))
        self.path = path


def _read(path: Path, toml: bool = False, strict: bool = False) -> dict:
    """A config file as a table, {} when there is none. A byte-order mark is fine, and so are comments and trailing
    commas in JSON. A file that still can't be parsed is {} too, or with *strict* raises Unreadable."""
    from .adapters.cloudflare import _jsonc

    try:
        text = path.read_text(encoding="utf-8-sig")
        if not text.strip():
            return {}
        if toml:
            data = tomllib.loads(text)
        else:
            try:
                data = json.loads(text)
            except ValueError:  # the slower parse only for comments (~/.claude.json can be large)
                data = json.loads(_jsonc(text))
    except OSError:
        return {}
    except ValueError:  # also UnicodeDecodeError and TOMLDecodeError
        data = None
    if isinstance(data, dict):
        return data
    if strict:
        raise Unreadable(path)
    return {}


def _table(value) -> dict:
    return value if isinstance(value, dict) else {}


def _strings(value) -> list[str]:
    """Every string in a definition's value, also in nested tables and lists."""
    if isinstance(value, str):
        return [value]
    items = value.values() if isinstance(value, dict) else value if isinstance(value, list) else ()
    return [s for item in items for s in _strings(item)]


def _linear(spec: dict) -> tuple[set[str], set[str], bool] | None:
    """(variables, keys, remote) of a Linear server, or None when *spec* is not one. Linear's remote server is found by
    mcp.linear.app in its address or its command (`npx mcp-remote https://mcp.linear.app/mcp`), a local one by a LINEAR_…
    variable (not LinearB's LINEARB_…) or a key in its env. Every variable its definition names counts (Codex's bearer_token_env_var,
    env_http_headers and env_vars name them bare), and so does every key written into it."""
    env = spec_env(spec)
    remote = any(LINEAR_HOST.search(word) for word in [str(spec.get("url") or ""), *spec_argv(spec)])
    if not remote and not any(n.upper().startswith("LINEAR_") or LINEAR_KEY.search(v) for n, v in env.items()):
        return None
    words = _strings(spec)
    variables = {a or b for word in words for a, b in REFERENCE.findall(word)}
    forwarded = spec.get("env_vars") if isinstance(spec.get("env_vars"), list) else []
    named = [spec.get("bearer_token_env_var"), *_strings(spec.get("env_http_headers")),
             *(v.get("name") if isinstance(v, dict) else v for v in forwarded)]
    variables |= {v for v in named if isinstance(v, str) and v}
    return variables, {key for word in words for key in LINEAR_KEY.findall(word)}, remote


def _project_linear(project: Project) -> tuple[str | None, str | None]:
    """(key variable, explicit server name) of the project's Linear connection."""
    conn = project.connections.get("linear") or {}
    ref = secrets.normalise(conn.get("key", "")) if conn else ""
    return (ref[4:] if ref.startswith("env:") else None), conn.get("mcp_server")


def _project_key(project: Project, env: dict | None) -> str | None:
    """The project's Linear key, read only to compare it with a key written into a definition."""
    return secrets.resolve((project.connections.get("linear") or {}).get("key", ""), env or {})


def _matches(name: str, patterns) -> bool:
    return any(fnmatch.fnmatchcase(name, pattern) for pattern in patterns)


def _owned_elsewhere(name: str, project: Project, owners: dict | None) -> bool:
    """True when another project owns this server name (callers check the project's own claim first)."""
    return any(_matches(name, patterns) for other, patterns in (owners or {}).items() if other != project.name)


def hidden(name: str, spec: dict, project: Project, owners: dict | None = None, verified: bool = True,
           env: dict | None = None) -> bool:
    """Should a session of *project* not see this server? *verified* is False for servers from the folder's .mcp.json,
    whose Linear keys Keyfit never checked; *env* is the session's, to tell the project's own key in a definition."""
    keep_var, keep_name = _project_linear(project)
    if name == keep_name or _matches(name, project.mcp_claims()):
        return False  # the project's own: its Linear server, or one it claimed (`keyfit add mcp`, Linear servers too)
    linear = _linear(spec)
    if linear is not None:
        if "linear" not in project.connections or not verified:
            return True
        variables, keys, remote = linear
        if not variables and not keys and remote:
            return True  # signed in inside the agent (OAuth): to which workspace, Keyfit can't tell
        # Keyfit puts the project's checked key into LINEAR_API_KEY (and into the project's own env: variable),
        # so a server reading either one gets that key — also when the key lives in the Keychain. A local server that
        # names no key reads LINEAR_API_KEY from the session.
        if not variables <= {LINEAR_API_KEY, keep_var} - {None}:
            return True
        if keys and keys != {_project_key(project, env)}:
            return True  # a key written into the definition: another workspace's, unless it is the project's own
    if "google-mcp" in project.connections and google_type(spec):
        return True  # another Google account's server, or one whose sign-in Keyfit never checked for this project
    return _owned_elsewhere(name, project, owners)


# Google Workspace servers (Gmail, Drive, Calendar): the local ones by the package they run (any version, through npx,
# uvx, a global install or node_modules), Google's own remote ones (developer preview since May 2026) by host. The
# remote ones keep their sign-in inside each agent, out of Keyfit's reach: `keyfit add mcp NAME` can only give one to a
# project.
GOOGLE_PACKAGES = {"workspace-mcp": "workspace-mcp", "@gongrzhe/server-gmail-autoauth-mcp": "gmail-autoauth",
                   "@cocal/google-calendar-mcp": "google-calendar-mcp",
                   "@piotr-agier/google-drive-mcp": "google-drive-mcp"}
GOOGLE_HOSTS = {f"{product}mcp.googleapis.com" for product in
                ("gmail", "drive", "calendar", "docs", "sheets", "slides", "chat", "classroom")}
GOOGLE_REMOTE = "remote"


def _package(word: str) -> str:
    """A command word as a package name: without its version (name@1.2, name==1.2) or the folder it is run from."""
    word = re.split(r"[=<>~!]=|[<>]", word, maxsplit=1)[0]
    if word.startswith("@"):
        return "@" + word[1:].split("@", 1)[0]
    return word.split("@", 1)[0].rsplit("/", 1)[-1]


def spec_argv(spec: dict) -> list[str]:
    """The command a stdio server runs: `command` + `args`, or OpenCode's `command` list."""
    command, args = spec.get("command"), spec.get("args")
    words = command if isinstance(command, list) else [command] if isinstance(command, str) else []
    return [w for w in [*words, *(args if isinstance(args, list) else [])] if isinstance(w, str)]


def spec_env(spec: dict) -> dict:
    """The variables a server's definition sets (`env`, OpenCode's `environment`)."""
    table = spec.get("env") if isinstance(spec.get("env"), dict) else spec.get("environment")
    return {k: v for k, v in table.items() if isinstance(v, str)} if isinstance(table, dict) else {}


def google_type(spec: dict) -> str | None:
    """Which Google Workspace server a definition runs: a type of GOOGLE_PACKAGES, GOOGLE_REMOTE, or None."""
    url = urllib.parse.urlparse(str(spec.get("url") or spec.get("httpUrl") or ""))
    if url.netloc in GOOGLE_HOSTS or (url.netloc == "people.googleapis.com" and url.path.startswith("/mcp")):
        return GOOGLE_REMOTE
    for word in spec_argv(spec):
        for package, kind in GOOGLE_PACKAGES.items():
            if _package(word) == package or f"/{package}/" in word:  # node_modules/@cocal/google-calendar-mcp/…
                return kind
    return None


def definitions(env: dict, cwd: Path, mine: bool = False) -> list[tuple[str, str, dict]]:
    """(agent, name, spec) of every server the five agents load in *cwd*: the user's and the folder's (for Claude also
    its local scope, for Codex the project config it trusts). *mine*: only those of your own config files (Claude's
    local scope too, which lives in ~/.claude.json), none a repository brings."""
    out = [("Claude", n, s) for n, s in claude_servers(env, cwd)]
    if not mine:
        out += [("Claude", n, s) for n, s in _table(_read(cwd / ".mcp.json").get("mcpServers")).items()]
    out += [("Codex", n, s) for n, s in codex_servers(env, None if mine else cwd).items()]
    out += [("OpenCode", n, s) for n, s, verified in opencode_servers(env, cwd) if verified or not mine]
    out += [("Copilot CLI", n, s) for n, s, verified in copilot_servers(env, cwd) if verified or not mine]
    out += [("Gemini CLI", n, s) for n, s, verified in gemini_servers(env, cwd) if verified or not mine]
    return [(agent, n, s) for agent, n, s in out if isinstance(s, dict)]


def _repository(cwd: Path) -> tuple[Path | None, Path | None]:
    """(top, main checkout) of the repository *cwd* is in: the folder holding .git and, for a linked worktree its
    repository lists back, the main checkout (else the top again). (None, None) outside a repository."""
    from .registry import canonical
    from .resolve import _lists_worktree

    top = next((folder for folder in (cwd, *cwd.parents) if (folder / ".git").exists()), None)
    if top is None:
        return None, None
    try:
        line = (top / ".git").read_text().strip()
    except (OSError, UnicodeDecodeError):  # a .git folder: the main checkout itself
        return top, top
    own = canonical(top / line[len("gitdir:"):].strip()) if line.startswith("gitdir:") else None
    common = own.parent.parent if own is not None else None
    if common is not None and common.name == ".git" and _lists_worktree(common, own, canonical(top)):
        return top, common.parent
    return top, top


def claude_servers(env: dict, cwd: Path | None = None, strict: bool = False) -> list[tuple[str, dict]]:
    """(name, spec) of the servers Claude loads from ~/.claude.json (or $CLAUDE_CONFIG_DIR's): the user scope's, then
    the local scope's (`claude mcp add` without --scope: projects[<folder>].mcpServers) for *cwd*, the top of its
    repository and, for a linked worktree, its main checkout. A local server wins over a user server of its name."""
    from .registry import canonical

    folder = env.get("CLAUDE_CONFIG_DIR")
    state = _read(Path(folder) / ".claude.json" if folder else Path(env.get("HOME") or Path.home()) / ".claude.json",
                  strict=strict)
    out = list(_table(state.get("mcpServers")).items())
    projects = _table(state.get("projects"))
    for path in dict.fromkeys((cwd, *_repository(cwd)) if cwd is not None else ()):
        for key in dict.fromkeys((str(path), str(canonical(path)))) if path is not None else ():
            out += _table(_table(projects.get(key)).get("mcpServers")).items()
    return [(name, spec) for name, spec in out if isinstance(spec, dict)]


def claude_user_servers(env: dict) -> dict:
    return dict(claude_servers(env))


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
    """Claude's --settings for a session of *project*. A name is denied when any of its definitions must be hidden (the
    deny goes by name, for every scope). Unreadable: ~/.claude.json or the folder's .mcp.json can't be parsed."""
    denied = list(dict.fromkeys(name for name, spec in claude_servers(env, cwd, strict=True)
                                if hidden(name, spec, project, owners, env=env)))
    local = _table(_read(cwd / ".mcp.json", strict=True).get("mcpServers"))
    disabled = [name for name, spec in local.items()
                if isinstance(spec, dict) and hidden(name, spec, project, owners, verified=False, env=env)]
    settings = {"disableAgentView": True}  # background sessions would run with their supervisor's first environment
    if not _user_statusline(env):
        settings["statusLine"] = {"type": "command", "command": statusline_command()}
    if denied:
        settings["deniedMcpServers"] = [{"serverName": name} for name in denied]
    if disabled:
        settings["disabledMcpjsonServers"] = disabled
    return settings


def _merge(base: dict, over: dict) -> dict:
    """*over* on top of *base*, table by table, as Codex merges its config layers."""
    out = dict(base)
    for key, value in over.items():
        out[key] = _merge(out[key], value) if isinstance(value, dict) and isinstance(out.get(key), dict) else value
    return out


def _codex_trusted(projects: dict, folders) -> bool:
    """As Codex decides it: the first of *folders* (the folder, the project's root, the repository's main checkout)
    with a trust_level in [projects], by its real path, then as spelled."""
    from .registry import canonical

    for folder in folders:
        for key in dict.fromkeys((str(canonical(folder)), str(folder))) if folder is not None else ():
            level = _table(projects.get(key)).get("trust_level")
            if level is not None:
                return level == "trusted"
    return False


def _codex_project_files(config: dict, home: Path, cwd: Path) -> list[Path]:
    """The project config Codex loads in *cwd*: .codex/config.toml in each folder from the project's root (the nearest
    with one of project_root_markers, by default .git; else *cwd*) down to *cwd*, when the folder is trusted. Codex's
    own folder doesn't count."""
    from .registry import canonical

    markers = config.get("project_root_markers", [".git"])
    markers = [m for m in markers if isinstance(m, str)] if isinstance(markers, list) else [".git"]
    folders = [cwd, *cwd.parents]
    end = next((i for i, folder in enumerate(folders) if any((folder / m).exists() for m in markers)), 0)
    found = [folder / ".codex" for folder in reversed(folders[:end + 1])
             if (folder / ".codex").is_dir() and canonical(folder / ".codex") != canonical(home)]
    if not found:
        return []
    repository = _repository(cwd)[1]
    projects = _table(config.get("projects"))
    return [dot / "config.toml" for dot in found if _codex_trusted(projects, (dot.parent, folders[end], repository))]


def codex_servers(env: dict, cwd: Path | None = None, profile: str | None = None, strict: bool = False) -> dict:
    """The servers Codex loads, merged as Codex merges them: ~/.codex/config.toml (or $CODEX_HOME's), the profile file
    `--profile NAME` selects ($CODEX_HOME/NAME.config.toml), then the trusted project config in *cwd* (the closest
    wins)."""
    home = Path(env["CODEX_HOME"]) if env.get("CODEX_HOME") else Path(env.get("HOME") or Path.home()) / ".codex"
    config = _read(home / "config.toml", toml=True, strict=strict)
    if profile and CODEX_PROFILE.fullmatch(profile):
        config = _merge(config, _read(home / f"{profile}.config.toml", toml=True, strict=strict))
    servers = _table(config.get("mcp_servers"))
    for path in _codex_project_files(config, home, cwd) if cwd is not None else ():
        servers = _merge(servers, _table(_read(path, toml=True, strict=strict).get("mcp_servers")))
    return servers


def _codex_option(args, short: str, long: str) -> str | None:
    """The value of a Codex option (`-p NAME`, `-pNAME`, `--profile NAME`, `--profile=NAME`; the last one counts);
    words after `--` are a prompt."""
    found = None
    for i, arg in enumerate(args):
        if arg == "--":
            break
        if arg in (short, long):
            found = args[i + 1] if i + 1 < len(args) else None
        elif arg.startswith(long + "="):
            found = arg[len(long) + 1:]
        elif arg.startswith(short) and not arg.startswith("--"):
            found = arg[len(short):].removeprefix("=")
    return found


def codex_overrides(project: Project, env: dict, owners: dict | None = None, cwd: Path | None = None,
                    args=()) -> list[str]:
    """The -c options that hide what a Codex session of *project* must not see, with the profile and folder (-C) its
    *args* name. Unhideable: a server whose name -c can't address; Unreadable: a config file that can't be parsed."""
    folder = _codex_option(args, "-C", "--cd")
    if cwd is not None and folder:
        cwd = Path(os.path.normpath(cwd / folder))
    out = []
    for name, spec in codex_servers(env, cwd, _codex_option(args, "-p", "--profile"), strict=True).items():
        if not isinstance(spec, dict) or not hidden(name, spec, project, owners, env=env):
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


def _opencode_file(path: Path, strict: bool = False) -> dict:
    return _opencode_table(_read(path, strict=strict))  # JSON with comments and trailing commas, as opencode.jsonc allows


def opencode_servers(env: dict, cwd: Path, strict: bool = False) -> list[tuple[str, dict, bool]]:
    """(name, spec, verified) of the servers OpenCode loads in *cwd* from files: the user's (verified) and the
    folder's and its parents' (unverified, like .mcp.json). *strict*: Unreadable for a file that can't be parsed."""
    mine = [opencode_config_dir(env) / name for name in OPENCODE_FILES]
    if env.get("OPENCODE_CONFIG"):
        mine.append(cwd / Path(env["OPENCODE_CONFIG"]).expanduser())
    out = [(name, spec, True) for path in mine for name, spec in _opencode_file(path, strict).items()]
    seen = {p.resolve() for p in mine}
    for folder in (cwd, *cwd.parents):
        for path in (*(folder / n for n in OPENCODE_FILES), *(folder / ".opencode" / n for n in OPENCODE_FILES)):
            if path.is_file() and path.resolve() not in seen:
                out += [(name, spec, False) for name, spec in _opencode_file(path, strict).items()]
    return out


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
    servers = opencode_servers(env, cwd, strict=True) + [(name, spec, True) for name, spec in _opencode_table(content).items()]
    names = sorted({name for name, spec, verified in servers if hidden(name, spec, project, owners, verified, env)})
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


# Copilot CLI (1.0.97): the user's mcp-config.json in its config folder (--config-dir, else COPILOT_HOME, else
# ~/.copilot), .mcp.json and .github/mcp.json in the folder and the folders above it up to the repository's top, the
# servers of installed plugins and any --additional-mcp-config. `--disable-mcp-server=NAME` switches one off for the
# session by name, whatever its source; it is never written to Copilot's settings.
COPILOT_FILES = (".mcp.json", ".github/mcp.json")


def copilot_config_dir(env: dict, config_dir: str | None = None) -> Path:
    if config_dir:
        return Path(config_dir).expanduser()
    if env.get("COPILOT_HOME"):
        return Path(env["COPILOT_HOME"]).expanduser()
    return Path(env.get("HOME") or Path.home()) / ".copilot"


def _copilot_table(data) -> dict:
    servers = data.get("mcpServers") if isinstance(data, dict) else None
    return {name: spec for name, spec in servers.items() if isinstance(spec, dict)} if isinstance(servers, dict) else {}


def _copilot_file(path: Path, strict: bool = False) -> dict:
    return _copilot_table(_read(path, strict=strict))


def _copilot_workspace(cwd: Path) -> list[Path]:
    """The folders Copilot looks for workspace servers in: *cwd* and those above it, up to the repository's top."""
    folders = [cwd, *cwd.parents]
    top = next((i for i, folder in enumerate(folders) if (folder / ".git").exists()), None)
    return folders[:top + 1] if top is not None else [cwd]


def copilot_servers(env: dict, cwd: Path, config_dir: str | None = None,
                    additional: list[str] = (), strict: bool = False) -> list[tuple[str, dict, bool]]:
    """(name, spec, verified) of the servers Copilot CLI loads in *cwd* from files and the command line: the user's
    and --additional-mcp-config (JSON, or @FILE) verified, the folder's unverified (like .mcp.json). *strict*:
    Unreadable for a file that can't be parsed."""
    out = [(name, spec, True)
           for name, spec in _copilot_file(copilot_config_dir(env, config_dir) / "mcp-config.json", strict).items()]
    for value in additional:
        if value.startswith("@"):
            path = Path(value[1:]).expanduser()
            table = _copilot_file(path if path.is_absolute() else cwd / path, strict)
        else:
            try:
                table = _copilot_table(json.loads(value))
            except ValueError:
                table = {}
        out += [(name, spec, True) for name, spec in table.items()]
    for folder in _copilot_workspace(cwd):
        for name in COPILOT_FILES:
            out += [(server, spec, False) for server, spec in _copilot_file(folder / name, strict).items()]
    return out


def copilot_hidden(project: Project, env: dict, cwd: Path, owners: dict | None = None, config_dir: str | None = None,
                   additional: list[str] = ()) -> list[str]:
    """The servers a Copilot CLI session of *project* must not see, by name."""
    return sorted({name for name, spec, verified in copilot_servers(env, cwd, config_dir, additional, strict=True)
                   if hidden(name, spec, project, owners, verified, env)})


# Gemini CLI (0.64.0): mcpServers in its settings files (the system's and its defaults, yours in its home, and the
# .gemini/settings.json of the folder it starts in, not the folders above it) and in each extension's
# gemini-extension.json. Its only per-session switch is an allowlist, --allowed-mcp-server-names, which replaces the
# settings' own mcp.allowed and mcp.excluded for that session: Keyfit passes every server the session may keep, minus
# what those settings leave out. An empty allowlist allows everything, so when nothing may stay the list names a server
# that does not exist. The names go comma-separated, so a server with a comma in its name stays hidden too.
GEMINI_NONE = "keyfit-none"
GEMINI_ALLOWED = ("--allowed-mcp-server-names", "--allowedMcpServerNames")


def gemini_home(env: dict) -> Path:
    return Path(env["GEMINI_CLI_HOME"]).expanduser() if env.get("GEMINI_CLI_HOME") else Path(env.get("HOME") or Path.home())


def _gemini_table(data) -> dict:
    servers = data.get("mcpServers") if isinstance(data, dict) else None
    if not isinstance(servers, dict):
        return {}
    out = {}
    for name, spec in servers.items():
        if isinstance(spec, dict):  # a streamable HTTP server names its address httpUrl
            out[name] = {**spec, "url": spec.get("httpUrl") or spec.get("url")}
    return out


def gemini_servers(env: dict, cwd: Path, strict: bool = False) -> list[tuple[str, dict, bool]]:
    """(name, spec, verified) of the servers Gemini CLI loads in *cwd*: the system's, yours and your extensions'
    (verified), and the folder's .gemini/settings.json (unverified, like .mcp.json). *strict*: Unreadable for a file
    that can't be parsed."""
    from .adapters.gemini import settings_files

    home = gemini_home(env)
    out = []
    for scope, path in settings_files(home, env, cwd):
        out += [(name, spec, scope != "workspace") for name, spec in _gemini_table(_read(path, strict=strict)).items()]
    try:
        extensions = sorted((home / ".gemini" / "extensions").iterdir())
    except OSError:
        extensions = []
    for extension in extensions:
        out += [(name, spec, True)
                for name, spec in _gemini_table(_read(extension / "gemini-extension.json", strict=strict)).items()]
    return out


def _gemini_own_lists(env: dict, cwd: Path) -> tuple[set | None, set]:
    """What the settings themselves allow (None: no allowlist; several lists: the names on all of them, in any case)
    and exclude, which --allowed-mcp-server-names would otherwise drop."""
    from .adapters.gemini import _jsonc, settings_files

    allowed, excluded = None, set()
    for _, path in settings_files(gemini_home(env), env, cwd):
        data = _jsonc(path)
        table = data.get("mcp") if isinstance(data, dict) else None
        if not isinstance(table, dict):
            continue
        if isinstance(table.get("allowed"), list):
            names = {str(n).lower().strip() for n in table["allowed"]}
            allowed = names if allowed is None else allowed & names
        if isinstance(table.get("excluded"), list):
            excluded |= {str(n) for n in table["excluded"]}
    return allowed, excluded


def gemini_hidden(project: Project, env: dict, cwd: Path, owners: dict | None = None) -> tuple[list[str], list[str]]:
    """(hidden, kept): the servers a Gemini CLI session of *project* must not see, and the ones it keeps (what
    --allowed-mcp-server-names should name). Both empty when nothing is hidden."""
    servers = gemini_servers(env, cwd, strict=True)
    hide = {name for name, spec, verified in servers if hidden(name, spec, project, owners, verified, env) or "," in name}
    if not hide:
        return [], []
    allowed, excluded = _gemini_own_lists(env, cwd)
    names = list(dict.fromkeys(name for name, _, _ in servers))
    kept = [name for name in names if name not in hide and name not in excluded
            and (allowed is None or name.lower().strip() in allowed)]
    return sorted(hide), kept


def gemini_allowed(args: list[str]) -> list[str]:
    """The names of --allowed-mcp-server-names on the command line (`--name a,b`, `--name=a`, repeated); words after
    `--` are a prompt."""
    out = []
    for i, arg in enumerate(args):
        if arg == "--":
            break
        name, eq, value = arg.partition("=")
        if name in GEMINI_ALLOWED:
            value = value if eq else (args[i + 1] if i + 1 < len(args) else "")
            out += [v.strip() for v in value.split(",") if v.strip()]
    return out
