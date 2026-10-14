"""The shim in front of `claude`, `codex`, `opencode`, `copilot`, `aws`, `gh`, `vercel`, `supabase`, `wrangler` and
`convex` (ambient mode).

Three outcomes only:
  folder not registered      → run the real tool unchanged
  registered, all checks ok  → run it with the project's environment (silently)
  a check fails              → do not run it; print what is wrong and the fix (in a terminal, when the fix
                               is only a sign-in, offer to run it and then start the tool)

Inside a project session (KEYFIT_PROJECT set), `aws`, `gh`, `vercel`, `supabase`, `wrangler` and
`convex` stay on that session's project even after `cd` elsewhere; `claude`, `codex`, `opencode` and
`copilot` re-resolve from the folder, since starting a new agent elsewhere is deliberate.
Bypass once: KEYFIT_DISABLE=1 <tool> …
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from . import cache, environment, mcp, secrets
from .adapters import ADAPTERS, cloudflare, convex, copilot, opencode, supabase, vercel
from .checks import printable
from .realbin import find_real
from .registry import Project, Registry, RegistryError, canonical, tilde
from .resolve import resolve

TOOLS = ("claude", "codex", "opencode", "copilot", "aws", "gh", "vercel", "supabase", "wrangler", "convex")
INSTALL = {"claude": "curl -fsSL https://claude.ai/install.sh | bash", "codex": "npm install -g @openai/codex",
           "opencode": "brew install opencode", "copilot": "npm install -g @github/copilot",
           "aws": "brew install awscli", "gh": "brew install gh", "vercel": "npm install -g vercel",
           "supabase": "brew install supabase/tap/supabase",
           "wrangler": "npm install -g wrangler   (projects that have it run their own: npx wrangler …)",
           "convex": "npm install -g convex   (projects that have it run their own: npx convex …)",
           "npx": "brew install node"}
# How to map the one account `aws`, `gh` and `vercel` need inside a project before they start there.
MAP = {"aws": "keyfit add aws --profile <profile>", "github": "keyfit add github --login <login>",
       "vercel": "keyfit add vercel"}
RELEVANT = {"aws": ("aws",), "gh": ("github",), "vercel": ("vercel",), "supabase": ("supabase",),
            "wrangler": ("cloudflare",), "convex": ("convex",), "claude": None, "codex": None,
            "opencode": None, "copilot": None}
# The agents: they start with all of the project's accounts, checked.
AGENTS = ("claude", "codex", "opencode", "copilot")
# Tools that run unchanged in a project without an account of their kind: Supabase is often used only locally
# (supabase start), with no account at all, and so are wrangler (wrangler dev) and convex (an anonymous local
# deployment).
OPTIONAL = ("supabase", "wrangler", "convex")
PINNED = ("aws", "gh", "vercel", "supabase", "wrangler", "convex")
MAX_DEPTH = 6
CLAUDE_SUBCOMMANDS = {"agents", "attach", "auth", "auto-mode", "doctor", "gateway", "import", "install", "logs",
                      "mcp", "plugin", "purge", "respawn", "rm", "setup-token", "stop", "ultrareview", "update", "help"}
CODEX_SUBCOMMANDS = {"agents", "exec", "e", "review", "login", "logout", "mcp", "plugin", "app-server",
                     "remote-control", "app", "completion", "update", "doctor", "sandbox", "debug", "apply", "a",
                     "resume", "queue", "archive", "delete", "migrate-rollouts", "unarchive", "fork", "cloud",
                     "exec-server", "features", "help"}
CODEX_SESSIONS = {None, "exec", "e", "review", "resume", "fork"}
OPENCODE_SUBCOMMANDS = {"upgrade", "update", "uninstall", "acp", "api", "debug", "auth", "mcp", "plugin", "models",
                        "stats", "mini", "run", "session", "service", "reload", "pair", "serve"}
# OpenCode's background server serves every folder and keeps the environment of the start that launched it. Its own
# commands, and OpenCode's updates, run with your environment as outside a project (see opencode_private).
OPENCODE_SHARED = {"upgrade", "update", "uninstall", "api", "debug", "mcp", "plugin", "service", "reload", "pair",
                   "serve"}
OPENCODE_NO_SERVER = {"upgrade", "update", "uninstall"}
# Commands that take --standalone: a private server, with the session's environment and the project's sign-ins.
OPENCODE_STANDALONE = {None, "run", "mini", "models", "stats", "session", "auth", "api", "reload"}
OPENCODE_VALUED = {"--server", "--session", "-s", "--prompt", "--log-level", "--completions"}
COPILOT_SUBCOMMANDS = {"app", "login", "help", "init", "config", "update", "version", "workflow", "sessions",
                       "memories", "plugin", "mcp", "skill", "instruction", "lsp", "sandbox", "completion"}
COPILOT_SESSIONS = {None, "init", "workflow"}  # they run the agent: its MCP servers start
# Copilot's options whose values matter to Keyfit (the folder it works in, where its servers come from).
COPILOT_VALUED = ("-C", "--config-dir", "--additional-mcp-config", "--enable-mcp-server")
# Sign-in and setup commands always run — they are how a failed check gets fixed.
LOGIN_COMMANDS = {
    "aws": (("sso", "login"), ("sso", "logout"), ("login",), ("logout",), ("configure",)),
    "claude": (("auth",), ("setup-token",)),
    "codex": (("login",), ("logout",)),
    "gh": (("auth",),),
    "vercel": (("login",), ("logout",), ("switch",), ("whoami",), ("teams",)),
    "supabase": (("login",), ("logout",)),
    "wrangler": (("login",), ("logout",), ("whoami",), ("auth", "create"), ("auth", "activate"), ("auth", "deactivate"),
                 ("auth", "delete"), ("auth", "list"), ("auth", "keyring")),
    "convex": (("login",), ("logout",)),  # not `login status`: it only reads the login
    "opencode": (("auth",),),
    "copilot": (("login",),),
}
LOGIN_KINDS = {"aws": ("aws",), "claude": ("claude",), "codex": ("codex",), "gh": ("github",), "vercel": ("vercel",),
               "supabase": ("supabase",), "wrangler": ("cloudflare",), "convex": ("convex",), "npx": ("convex",),
               "opencode": ("opencode",), "copilot": ("copilot",)}
# Variables sign-in commands run without: gh must manage its own tokens, Copilot CLI its own login (`copilot login`
# takes a token from the environment instead of signing in), and convex its own login (with the override set,
# `convex login` would only say the injected token is valid). (`supabase login` runs only in a project without a
# Supabase account, where Keyfit has removed the token it set for another project, and nothing else.)
LOGIN_SCRUB = {"gh": ("GH_TOKEN", "GITHUB_TOKEN"), "copilot": (copilot.TOKEN, "GH_TOKEN", "GITHUB_TOKEN"),
               "convex": (convex.OVERRIDE,), "npx": (convex.OVERRIDE,)}
# Fixes that are nothing but a sign-in, which Keyfit offers to run when you start a tool yourself in a terminal
# (None: one value such as a profile name). Every other fix, including gh's (it switches gh's active account), is
# only printed. A variable a fix may set in front: Claude's and Codex's config folder of the account.
SIGN_INS = {
    "aws": (("login", "--profile", None), ("sso", "login", "--profile", None), ("sso", "login", "--sso-session", None)),
    "claude": (("auth", "login"),),
    "codex": (("login",),),
    "vercel": (("login",), ("login", "--global-config", None)),
    "wrangler": (("auth", "create", None), ("login",)),
    "npx": (("convex", "login", "--force"),),  # a Convex account that is the convex CLI's own login
    "opencode": (("auth", "login", "--standalone"), ("auth", "login", None, "--standalone")),
}
SIGN_IN_VARS = {"claude": "CLAUDE_CONFIG_DIR", "codex": "CODEX_HOME", "opencode": "OPENCODE_DB"}
VALUE_OPTIONS = {"--profile", "--region", "--output", "--endpoint-url", "--query", "--color", "--ca-bundle",
                 "--cli-read-timeout", "--cli-connect-timeout", "--sso-session", "-u", "--user", "-h", "--hostname",
                 "-c", "--config", "-m", "--model", "-Q", "--global-config", "-S", "--scope", "-T", "--team",
                 "-t", "--token", "-A", "--local-config", "--cwd", "--api", "--workdir", "--project-ref",
                 "--project-id", "--org-id", "--network-id", "--dns-resolver", "-o"}


class Refused(Exception):
    pass


def positionals(args: list[str]) -> list[str]:
    """The command words, skipping options and the values of VALUE_OPTIONS. An option Keyfit does not know may
    take the next word as its value (`claude --add-dir auth` starts a session), so the words stop there: an
    unclear command line is never taken for a sign-in command, which would skip every check."""
    out = []
    for i, arg in enumerate(args):
        if arg == "--":
            break
        if i > 0 and args[i - 1] in VALUE_OPTIONS:
            continue
        if arg.startswith("-"):
            if "=" not in arg and arg not in VALUE_OPTIONS and i + 1 < len(args) and not args[i + 1].startswith("-"):
                break
            continue
        out.append(arg)
    return out


def is_login_command(tool: str, args: list[str]) -> bool:
    readers = {"supabase": supabase.words, "wrangler": cloudflare.words, "convex": convex.words}
    words = readers.get(tool, positionals)(args)
    if tool == "convex" and words[:2] == ["login", "status"]:
        return False
    return any(tuple(words[:len(prefix)]) == prefix for prefix in LOGIN_COMMANDS.get(tool, ()))


def subcommand(args: list[str], known: set[str]) -> str | None:
    previous = None
    for arg in args:
        if arg == "--":
            return None
        if arg in known and (previous is None or not previous.startswith("-")):
            return arg
        previous = arg
    return None


def requested_profiles(args: list[str]) -> list[str]:
    """Profiles named with --profile. The AWS CLI's parser (argparse) also takes an abbreviation that no other
    global option shares — `--prof x`, `--pro=x`, down to `--p` — so those count too."""
    out = []
    for i, arg in enumerate(args):
        name, eq, value = arg.partition("=")
        if len(name) < 3 or not "--profile".startswith(name):
            continue
        if eq:
            out.append(value)
        elif i + 1 < len(args):
            out.append(args[i + 1])
    return out


def adjust_args(tool: str, project: Project, args: list[str], env: dict, cwd: Path,
                owners: dict | None = None) -> list[str]:
    if tool == "aws":
        profile = project.connections["aws"]["profile"]
        for asked in requested_profiles(args):
            if asked != profile:
                raise Refused(f"keyfit: this folder belongs to {project.name} (AWS profile {profile}); "
                              f"refusing --profile {asked}.\n  Bypass once: KEYFIT_DISABLE=1 aws …")
        return args
    if tool == "vercel":
        return vercel_args(project, args, env, cwd)
    if tool == "supabase":
        return supabase_args(project, args, env, cwd)
    if tool == "wrangler":
        return wrangler_args(project, args, env, cwd)
    if tool == "convex":
        return convex_args(project, args, env, cwd)
    if tool == "claude":
        if subcommand(args, CLAUDE_SUBCOMMANDS):
            return args
        settings = mcp.claude_settings(project, env, cwd, owners)
        return (["--settings", json.dumps(settings)] if settings else []) + args
    if tool == "codex":
        sub = subcommand(args, CODEX_SUBCOMMANDS)
        if sub not in CODEX_SESSIONS:
            return args
        try:
            extra = mcp.codex_overrides(project, env, owners)
        except mcp.Unhideable as server:
            raise Refused(f"keyfit: codex not started, {project.name} must not see MCP server {printable(str(server))}, "
                          f"and Codex cannot hide a name with a dot, \"=\" or a space at either end.\n"
                          f"  Rename it in ~/.codex/config.toml (underscores work), or remove it.") from None
        if sub is None:
            extra.append("--no-daemon")  # the shared daemon keeps the first launcher's environment
        return extra + args
    if tool == "opencode":
        return opencode_args(project, args, env, cwd, owners)
    if tool == "copilot":
        return copilot_args(project, args, env, cwd, owners)
    return args


def opencode_standalone(args: list[str]) -> list[str]:
    """Add --standalone (OpenCode's private server, with this environment) where the command takes it, unless a
    server is named already. `opencode auth` and `opencode session` alone print their commands and take none."""
    end = args.index("--") if "--" in args else len(args)
    sub = subcommand(args, OPENCODE_SUBCOMMANDS)
    if sub not in OPENCODE_STANDALONE:
        return args
    if sub in ("auth", "session") and not any(not a.startswith("-") for a in args[args.index(sub) + 1:end]):
        return args
    if any(a == "--standalone" or a == "--server" or a.startswith("--server=") for a in args[:end]):
        return args
    return args[:end] + ["--standalone"] + args[end:]


def opencode_server_option(project: Project, args: list[str]) -> None:
    """Refuse another --server (its sign-ins and environment are not the project's), and `acp --login`, whose sign-in
    goes through the background server."""
    end = args.index("--") if "--" in args else len(args)
    if any(a == "--server" or a.startswith("--server=") for a in args[:end]):
        raise Refused(f"keyfit: this folder belongs to {project.name}; refusing --server: OpenCode would run with "
                      f"that server's sign-ins and environment, not {project.name}'s.\n"
                      f"  Bypass once: KEYFIT_DISABLE=1 opencode …")
    if subcommand(args, OPENCODE_SUBCOMMANDS) == "acp" and "--login" in args[:end]:
        raise Refused(f"keyfit: this folder belongs to {project.name}; refusing `opencode acp --login`: it signs in "
                      f"through OpenCode's background server, not {project.name}'s database.\n"
                      f"  Sign in with: opencode auth login   (Keyfit sends it to {project.name}'s database)")


def opencode_private(args: list[str], env: dict) -> list[str]:
    """OpenCode where Keyfit does not start it with a project's environment: in a folder outside every project, and
    for the background server's own commands. In your terminal it runs unchanged. Inside a session Keyfit started
    (KEYFIT_PROJECT set), the environment holds that project's accounts, which a background server started now would
    keep for every folder: commands that take --standalone get it, and the others are refused."""
    end = args.index("--") if "--" in args else len(args)
    if not env.get("KEYFIT_PROJECT") or any(a == "--server" or a.startswith("--server=") for a in args[:end]):
        return args  # your own environment, or a server you named: none is started
    sub = subcommand(args, OPENCODE_SUBCOMMANDS)
    if sub in OPENCODE_NO_SERVER or (sub == "acp" and "--login" not in args[:end]):  # acp: always a private server
        return args
    standalone = opencode_standalone(args)
    if standalone != args or "--standalone" in args or sub in ("auth", "session"):
        return standalone
    raise Refused(f"keyfit: opencode not started: inside a session of {printable(env['KEYFIT_PROJECT'])}, "
                  f"`opencode {sub or ' '.join(args)}` could start OpenCode's background server with that project's "
                  f"accounts, and every folder would share them.\n  Run it in a terminal outside the session "
                  f"(bypass once: KEYFIT_DISABLE=1 opencode …)")


def opencode_mcp(project: Project, env: dict, cwd: Path, owners: dict | None) -> None:
    try:
        mcp.opencode_environment(project, env, cwd, owners)
    except mcp.Unreadable:
        raise Refused(f"keyfit: opencode not started: OPENCODE_CONFIG_CONTENT in your environment is not a JSON "
                      f"object, so Keyfit cannot switch off the MCP servers {project.name} must not see.\n"
                      f"  Fix or unset it (bypass once: KEYFIT_DISABLE=1 opencode …)") from None


def opencode_args(project: Project, args: list[str], env: dict, cwd: Path, owners: dict | None = None) -> list[str]:
    """Sessions, and the commands that read the project's sign-ins and sessions, run on a private server
    (--standalone): the background server serves every folder and keeps the environment of the start that launched
    it. Every MCP server the project must not see is switched off, for the folder OpenCode opens; with an OpenCode
    account, provider keys from the shell go."""
    opencode_server_option(project, args)
    if "opencode" in project.connections:
        opencode.scrub(env)
    folder = opencode_folder(args)
    opencode_mcp(project, env, Path(folder).resolve() if folder else cwd, owners)
    return opencode_standalone(args)


def opencode_folder(args: list[str]) -> str | None:
    """The folder `opencode DIRECTORY` starts in, when one is named."""
    skip = False
    for arg in args:
        if arg == "--":
            return None
        if skip:
            skip = False
        elif arg.startswith("-"):
            skip = arg in OPENCODE_VALUED
        else:
            return arg if subcommand([arg], OPENCODE_SUBCOMMANDS) is None and os.path.isdir(arg) else None
    return None


def folder_check(tool: str, folder: str | None, registry: Registry, project: Project | None) -> None:
    """`opencode DIRECTORY` and `copilot -C DIRECTORY` work in that folder with this folder's accounts: refuse when
    the two folders belong to different projects (or only one is registered)."""
    if folder is None:
        return
    there = resolve(folder, registry).project
    if (there.name if there else None) == (project.name if project else None):
        return
    import shlex

    raise Refused(f"keyfit: {tool} not started: {printable(folder)} belongs to "
                  f"{there.name if there else 'no registered project'}, this folder to "
                  f"{project.name if project else 'no registered project'}, and the session would get this folder's "
                  f"accounts.\n  Start it there: cd {shlex.quote(folder)} && {tool}   "
                  f"(bypass once: KEYFIT_DISABLE=1 {tool} …)")


def copilot_options(args: list[str]) -> list[tuple[str, str | None]]:
    """(option, value) of COPILOT_VALUED on the command line, in any form Copilot takes (`-C dir`, `-Cdir`,
    `--config-dir=dir`); words after `--` are a prompt."""
    out = []
    for i, arg in enumerate(args):
        if arg == "--":
            break
        for name in COPILOT_VALUED:
            if arg == name:
                out.append((name, args[i + 1] if i + 1 < len(args) else None))
            elif arg.startswith(name + "=") or (name == "-C" and arg.startswith("-C") and len(arg) > 2):
                out.append((name, arg[len(name):].removeprefix("=")))
    return out


def copilot_folder(args: list[str], cwd: Path | None = None) -> str | None:
    """The folder `copilot -C DIRECTORY` works in (relative to *cwd*, default the current folder), when it is one."""
    folders = [value for name, value in copilot_options(args) if name == "-C" and value]
    return folders[-1] if folders and os.path.isdir(os.path.join(cwd or os.curdir, folders[-1])) else None


def copilot_args(project: Project, args: list[str], env: dict, cwd: Path, owners: dict | None = None) -> list[str]:
    """Sessions (and `init` and `workflow`, which run the agent) start with every MCP server the project must not
    see switched off by name, for the folder Copilot works in; `--enable-mcp-server` cannot bring one back."""
    sub = subcommand(args, COPILOT_SUBCOMMANDS)
    if sub == "login":  # an option before it hid it from the sign-in commands (`copilot -C DIR login`)
        copilot_login_check(project)
        for var in LOGIN_SCRUB["copilot"]:
            env.pop(var, None)
        return args
    if sub not in COPILOT_SESSIONS:
        return args
    given = copilot_options(args)
    folder = copilot_folder(args, cwd)
    config = [value for name, value in given if name == "--config-dir" and value]
    names = mcp.copilot_hidden(project, env, (cwd / folder).resolve() if folder else cwd, owners,
                               config_dir=str(cwd / Path(config[-1]).expanduser()) if config else None,
                               additional=[value for name, value in given if name == "--additional-mcp-config" and value])
    for name, value in given:
        if name == "--enable-mcp-server" and value in names:
            raise Refused(f"keyfit: this folder belongs to {project.name}; refusing --enable-mcp-server "
                          f"{printable(value)}: that MCP server belongs to another project.\n"
                          f"  Bypass once: KEYFIT_DISABLE=1 copilot …")
    return [f"--disable-mcp-server={name}" for name in names] + args


def copilot_login_check(project: Project) -> None:
    """`copilot login` changes only Copilot's own login, which a project with a Copilot account does not use (Keyfit
    hands Copilot gh's token for the account): refuse there rather than seem to work, and name the sign-in that
    counts."""
    conn = project.connections.get("copilot")
    if not isinstance(conn, dict):
        return  # no Copilot account: Copilot's own login is what copilot uses here
    raise Refused(f"keyfit: this folder belongs to {project.name}, whose Copilot CLI runs as GitHub "
                  f"{printable(conn.get('login', '?'))} with the token gh keeps for it; `copilot login` would change "
                  f"only Copilot's own login, which {project.name} does not use.\n"
                  f"  Sign gh in instead: {ADAPTERS['copilot'].login_fix(conn)}\n"
                  f"  Bypass once: KEYFIT_DISABLE=1 copilot …")


def vercel_args(project: Project, args: list[str], env: dict, cwd: Path) -> list[str]:
    """Refuse options that would leave the project's Vercel login or team (like `aws --profile`), then add the
    login's folder (--global-config) and the team (--scope) unless the user named them already."""
    conn = project.connections["vercel"]
    folder = canonical(conn["config_dir"])
    team = conn.get("team") or conn.get("team_id")
    bypass = "\n  Bypass once: KEYFIT_DISABLE=1 vercel …"
    given = vercel.options(args)
    for name, value in given:
        if name == "--token":
            raise Refused(f"keyfit: this folder belongs to {project.name} (Vercel login in {tilde(folder)}); "
                          f"refusing --token: it would skip that login and its check.{bypass}")
        if name == "--global-config" and value is not None and (cwd / value).resolve() != folder:
            raise Refused(f"keyfit: this folder belongs to {project.name} (Vercel login in {tilde(folder)}); "
                          f"refusing --global-config {value}.{bypass}")
        if name in ("--scope", "--team") and team and value is not None \
                and value not in (conn.get("team"), conn.get("team_id")):
            raise Refused(f"keyfit: this folder belongs to {project.name} (Vercel team {team}); "
                          f"refusing {name} {value}.{bypass}")
        if name == "--cwd" and value:
            link = vercel.foreign_link(conn, cwd / value, env)
            if link is not None:
                raise Refused(f"keyfit: {tilde(link[0])} links {value} to another Vercel team ({printable(link[1])}), but this "
                              f"folder belongs to {project.name} (Vercel team {team}); refusing --cwd {value}.{bypass}")
    names = {name for name, _ in given}
    extra = [] if "--global-config" in names else vercel.config_args(conn, env)
    if team and not names & {"--scope", "--team"}:
        extra += ["--scope", team]
    return extra + args


def supabase_args(project: Project, args: list[str], env: dict, cwd: Path) -> list[str]:
    """Refuse what would leave the project's Supabase login or organization (like `aws --profile`): another profile
    or --org-id, and a project the token reaches in another organization (or cannot reach) named by --project-ref,
    `gen … --project-id`, `link <ref>`, `projects delete <ref>` or the link of a --workdir. The folder's own link
    was checked already."""
    conn = project.connections["supabase"]
    org = conn.get("org_name") or conn["org"]
    refuse = f"keyfit: this folder belongs to {project.name} (Supabase organization {printable(org)}); refusing"
    bypass = "\n  Bypass once: KEYFIT_DISABLE=1 supabase …"
    given = supabase.options(args)
    named = {name for name, _ in given}
    words = supabase.words(args)
    asked = []  # (what the command line said, the project ref it means)
    for name, value in given:
        if value is None:
            continue
        if name == "--profile" and value.lower() != supabase.PROFILE:
            raise Refused(f"{refuse} --profile {printable(value)}: a profile picks another login and API host.{bypass}")
        if name == "--org-id" and value != conn["org"]:
            raise Refused(f"{refuse} --org-id {printable(value)}.{bypass}")
        if name == "--project-ref" or (name == "--project-id" and words[:1] == ["gen"]):  # `stop --project-id`: local
            asked.append((f"{name} {value}", value))
        if name == "--workdir" and not named & {"--project-ref", "--project-id"}:
            ref, source = supabase.linked_ref(cwd, {**env, "SUPABASE_WORKDIR": value})
            if ref:
                asked.append((f"--workdir {value} (linked to {ref} in {source})", ref))
    if words[:1] == ["link"]:  # every word after it that could be a ref: an option Keyfit does not know may hold
        asked += [(f"link {word}", word) for word in words[1:]]  # one value in between
    if words[:2] == ["projects", "delete"]:
        asked += [(f"projects delete {word}", word) for word in words[2:]]
    checked, _ = supabase.linked_ref(cwd, env)
    for shown, ref in asked:
        if ref == checked or not supabase.REF.fullmatch(ref):  # checked already; or a branch name, which the CLI
            continue  # looks up among the linked project's branches, or a ref it refuses itself
        reason = project_outside(project, conn, ref, env)
        if reason:
            raise Refused(f"{refuse} {printable(shown)}: {reason}.{bypass}")
    return args


def project_outside(project: Project, conn: dict, ref: str, env: dict) -> str:
    """Why the project's token must not work on project *ref* ("" when it may): *ref* belongs to another
    organization, or the token cannot reach it. A ref Supabase does not find (a preview branch, or a project this
    token cannot see, where the CLI fails by itself) may go ahead. A confirmation is cached like a check."""
    import hashlib

    token = env.get("SUPABASE_ACCESS_TOKEN", "")
    k = cache.key(project.name, "supabase-project", {"ref": ref, "org": conn["org"]},
                  hashlib.sha256(token.encode()).hexdigest()[:16])
    if cache.get(k) is not None:
        return ""
    found, reason, transient = ADAPTERS["supabase"].project(token, ref)
    if found is None and reason == "not found":
        return ""
    if found is None:
        return f"{reason} (check the network and retry)" if transient else reason
    if found["org"] != conn["org"]:
        return f"it belongs to another organization ({printable(found['org'])})"
    cache.put(k, "ok", "")
    return ""


def supabase_login_check(project: Project, args: list[str]) -> None:
    """`supabase login`/`logout` change only the CLI's own login, which a project with a Supabase account does not use
    (Keyfit keeps its token): refuse there rather than seem to work, and name what changes the project's token."""
    conn = project.connections.get("supabase")
    if not isinstance(conn, dict):
        return  # no Supabase account: the CLI's own login is what supabase uses here
    word = supabase.words(args)[0]
    raise Refused(f"keyfit: this folder belongs to {project.name}, whose Supabase token Keyfit keeps "
                  f"({secrets.describe(conn.get('token', ''))}); `supabase {word}` would change only the supabase CLI's "
                  f"own login, which {project.name} does not use.\n  Another token for {project.name}: "
                  f"{supabase.add_command(conn)}   (paste it; or sign the CLI in outside {project.name}, then add "
                  f"--cli)\n  Bypass once: KEYFIT_DISABLE=1 supabase …")


def wrangler_args(project: Project, args: list[str], env: dict, cwd: Path) -> list[str]:
    """Refuse what would leave the project's wrangler profile or Cloudflare account (like `aws --profile`): another
    --profile, and a --cwd or --config whose folder is bound to another profile or whose config names another
    account_id. The folder's own binding and config were checked already."""
    conn = project.connections["cloudflare"]
    title = conn.get("account_name") or conn["account_id"]
    refuse = (f"keyfit: this folder belongs to {project.name} (wrangler profile {conn['profile']}, Cloudflare account "
              f"{printable(title)}); refusing")
    bypass = "\n  Bypass once: KEYFIT_DISABLE=1 wrangler …"
    given = cloudflare.options(args)
    asked = moved = config = None
    for name, value in given:
        if value is None:
            continue
        if name == "--profile":
            if value != conn["profile"]:
                raise Refused(f"{refuse} --profile {printable(value)}: it signs in as another wrangler profile.{bypass}")
            asked = value
        elif name == "--cwd":
            moved = value
        else:
            config = value
    if moved is None and config is None:
        return args
    base = os.path.join(cwd, moved) if moved else str(cwd)  # wrangler moves to --cwd first, then reads --config
    path = Path(os.path.abspath(os.path.join(base, config))) if config else None
    problem = cloudflare.mismatch(conn, path.parent if path else base, env, asked=asked, config=path)
    if problem:
        shown = " ".join(f"{name} {value}" for name, value in given if name != "--profile" and value is not None)
        raise Refused(f"{refuse} {printable(shown)}: {problem[0]}.\n  → {problem[1]}{bypass}")
    return args


def wrangler_login_check(project: Project, args: list[str], registry: Registry, env: dict) -> None:
    """wrangler's sign-in and binding commands run without checks (they are how a failed check gets fixed), except
    those that would move a registered folder off its project's profile: `login`/`logout` (wrangler's default login
    only) in a project with a named profile, `auth activate`/`deactivate` of a folder whose project uses another
    profile, `auth create` of another project's profile and `auth delete` of any project's (it unbinds its folders)."""
    words = cloudflare.words(args)
    conn = project.connections.get("cloudflare")
    own = conn.get("profile") if isinstance(conn, dict) else None
    bypass = "\n  Bypass once: KEYFIT_DISABLE=1 wrangler …"
    if words[:1] in (["login"], ["logout"]):
        if own and own != cloudflare.DEFAULT:
            raise Refused(f"keyfit: this folder belongs to {project.name}, which uses wrangler profile {own}; "
                          f"`wrangler {words[0]}` would change only wrangler's default login, which {project.name} does "
                          f"not use.\n  Sign in again as {own}: wrangler auth create {own}{bypass}")
        return
    if words[:1] != ["auth"] or len(words) < 2:
        return
    users: dict[str, list[str]] = {}  # profile → the projects that use it
    for other in registry.projects().values():
        theirs = other.connections.get("cloudflare")
        if isinstance(theirs, dict) and isinstance(theirs.get("profile"), str):
            users.setdefault(theirs["profile"], []).append(other.name)
    action, rest = words[1], words[2:]
    name = rest[0] if rest else None
    if action == "create" and name and name != own and users.get(name):  # a profile it shares stays its own
        others = ", ".join(p for p in users[name] if p != project.name)
        raise Refused(f"keyfit: this folder belongs to {project.name}; refusing `wrangler auth create {printable(name)}`: "
                      f"that profile is the sign-in of {others}.{bypass}")
    if action == "delete" and name and users.get(name):
        raise Refused(f"keyfit: refusing `wrangler auth delete {printable(name)}`: {', '.join(users[name])} "
                      f"uses that profile (deleting it also unbinds its folders).{bypass}")
    if action not in ("activate", "deactivate"):
        return
    target = (rest[1] if len(rest) > 1 else None) if action == "activate" else name
    moved = [value for option, value in cloudflare.options(args) if option == "--cwd" and value]
    folder = os.path.abspath(os.path.join(os.getcwd(), *moved[-1:], target or "."))
    owner = resolve(folder, registry).project
    theirs = owner.connections.get("cloudflare") if owner else None
    if not isinstance(theirs, dict) or not isinstance(theirs.get("profile"), str):
        return
    if action == "activate" and name and name != theirs["profile"]:
        raise Refused(f"keyfit: refusing `wrangler auth activate {printable(name)}` for {tilde(folder)}: that folder "
                      f"belongs to {owner.name}, which uses wrangler profile {theirs['profile']}.{bypass}")
    if action == "deactivate" and cloudflare.bindings(env).get(folder) == theirs["profile"]:
        raise Refused(f"keyfit: refusing `wrangler auth deactivate` for {tilde(folder)}: {owner.name} uses wrangler "
                      f"profile {theirs['profile']} there.{bypass}")


def convex_args(project: Project, args: list[str], env: dict, cwd: Path) -> list[str]:
    """Refuse what would leave the project's Convex login or team (like `aws --profile`): an admin key (it skips the
    login and its check), another team (--team, --deployment TEAM:PROJECT:REF), and a deployment of another team
    named by --deployment-name, --deployment NAME or an --env-file. The folder's own deployment was checked already."""
    conn = project.connections["convex"]
    team = conn["team"]
    refuse = (f"keyfit: this folder belongs to {project.name} (Convex team {printable(conn.get('team_name') or team)}); "
              f"refusing")
    bypass = "\n  Bypass once: KEYFIT_DISABLE=1 convex …"
    asked = []  # (what the command line said, the deployment name it means)
    for name, value in convex.options(args):
        if value is None:
            continue
        if name == "--admin-key":
            raise Refused(f"{refuse} --admin-key: it would skip {project.name}'s Convex login and its check.{bypass}")
        if name == "--team" and value != team:
            raise Refused(f"{refuse} --team {printable(value)}.{bypass}")
        if name == "--deployment" and value.count(":") == 2 and value.split(":")[0] != team:
            raise Refused(f"{refuse} --deployment {printable(value)}: another team.{bypass}")
        if name == "--deployment-name" or (name == "--deployment" and convex.DEPLOYMENT_NAME.fullmatch(value)):
            asked.append((f"{name} {value}", value))  # any other --deployment is a ref inside the folder's project
        if name == "--env-file":
            aim = convex.target(cwd, env, files=convex.read_env_file(cwd / value))
            if aim and aim["kind"] == "preview" and aim["team"] != team:
                raise Refused(f"{refuse} --env-file {printable(value)}: a preview deploy key of another team "
                              f"({printable(aim['team'])}).{bypass}")
            if aim and aim["kind"] == "deployment":
                asked.append((f"--env-file {value} (deployment {aim['name']})", aim["name"]))
    checked = convex.target(cwd, env) or {}
    if convex.OVERRIDE not in env:  # the folder's project deploy key is the CLI's login: it reaches only its project
        return args
    for shown, name in asked:
        if name == checked.get("name"):  # checked already
            continue
        reason = deployment_outside(project, conn, name, env)
        if reason:
            raise Refused(f"{refuse} {printable(shown)}: {reason}.{bypass}")
    return args


def deployment_outside(project: Project, conn: dict, name: str, env: dict) -> str:
    """Why the project's login must not work on deployment *name* ("" when it may): it belongs to another team, or
    the login cannot reach it. A confirmation is cached like a check."""
    import hashlib

    token = env.get(convex.OVERRIDE, "")
    k = cache.key(project.name, "convex-deployment", {"name": name, "team": conn["team"]},
                  hashlib.sha256(token.encode()).hexdigest()[:16])
    if cache.get(k) is not None:
        return ""
    found, reason, transient = ADAPTERS["convex"].deployment(token, name)
    if found is None:
        return f"{reason} (check the network and retry)" if transient else reason
    if found["team"] != conn["team"]:
        return f"it belongs to another team ({printable(found['team'])})"
    cache.put(k, "ok", "")
    return ""


def convex_login_check(project: Project, args: list[str]) -> None:
    """`convex login`/`logout` change only the CLI's own login: fine when that login is the project's account, refused
    when Keyfit keeps the project's token (they would seem to work and change nothing for the project)."""
    conn = project.connections.get("convex")
    if not isinstance(conn, dict) or conn.get("token") == convex.CLI:
        return
    word = convex.words(args)[0]
    raise Refused(f"keyfit: this folder belongs to {project.name}, whose Convex token Keyfit keeps "
                  f"({convex.where(conn)}); `convex {word}` would change only the convex CLI's own login, which "
                  f"{project.name} does not use.\n  Another token for {project.name}: {convex.add_command(conn)}   "
                  f"(paste it; or sign the CLI in outside {project.name}, then add --copy)\n"
                  f"  Bypass once: KEYFIT_DISABLE=1 convex …")


def login_environment(tool: str, project: Project, env: dict) -> dict:
    """The environment a sign-in command runs with: the project's (so it signs in to the project's account), without
    the variables that would make the tool skip its own login."""
    login_env = environment.build(project, env, kinds=LOGIN_KINDS[tool])
    for var in LOGIN_SCRUB.get(tool, ()):
        login_env.pop(var, None)
    if tool == "opencode":  # its sign-in starts a private server, and that the MCP servers of its configuration
        if "opencode" in project.connections:
            opencode.scrub(login_env)
        opencode_mcp(project, login_env, Path.cwd(), Registry.load().mcp_owners())
    return login_env


def vercel_login_args(project: Project, args: list[str], env: dict) -> list[str]:
    """Sign-in commands (`vercel login`, …) go to the project's login folder, unless the user named one."""
    conn = project.connections.get("vercel")
    if not isinstance(conn, dict) or any(name == "--global-config" for name, _ in vercel.options(args)):
        return args
    return vercel.config_args(conn, env) + args


def _say(lines: list[str]) -> None:
    for line in lines:
        print(line, file=sys.stderr)


def sign_in_command(fix: str) -> tuple[dict, list[str]] | None:
    """The fix as (variables, words) when it is exactly one of SIGN_INS, else None."""
    import shlex

    try:
        words = shlex.split(fix)
    except ValueError:
        return None
    assignments = {}
    while words and "=" in words[0] and not words[0].startswith("-"):
        name, _, value = words.pop(0).partition("=")
        assignments[name] = value
    if not words or words[0] not in SIGN_INS or set(assignments) - {SIGN_IN_VARS.get(words[0])}:
        return None
    rest = words[1:]
    for shape in SIGN_INS[words[0]]:
        if len(shape) == len(rest) and all(want == word if want else word and not word.startswith("-")
                                           for want, word in zip(shape, rest)):
            return assignments, words
    return None


def offer_sign_in(failed: list, project: Project, env: dict) -> bool | None:
    """In a terminal, when every failed check is fixed by a sign-in, ask and run them. True: they all finished;
    False: one failed; None: nothing was offered or the answer was no. Never asked without a terminal (agents,
    scripts, CI), so nothing waits for an answer that cannot come."""
    commands = [sign_in_command(check.fix) for check in failed]
    if not commands or None in commands or env.get("KEYFIT_DRY_RUN") == "1" or env.get("CI"):
        return None
    if not (sys.stdin.isatty() and sys.stderr.isatty()):
        return None
    sys.stderr.write("  Sign in now? [Y/n] ")
    sys.stderr.flush()
    answer = sys.stdin.readline()
    if not answer or answer.strip().lower() not in ("", "y", "yes"):  # end of input counts as no
        return None
    import subprocess

    for assignments, words in {(tuple(a.items()), tuple(w)): None for a, w in commands}:
        name = words[0]
        real = cloudflare.wrangler_bin(env, os.getcwd()) if name == "wrangler" else \
            find_real(name, env, skip_wrappers=True)
        if real is None:
            _say([f"keyfit: {name} is not installed. Install it: {INSTALL[name]}"])
            return False
        login_env = login_environment(name, project, env)  # as when you type the sign-in
        login_env.update(assignments)
        if subprocess.run(list(words), executable=real, env=login_env).returncode != 0:
            _say([f"keyfit: {name} sign-in did not finish"])
            return False
    return True


def main(argv: list[str]) -> int:
    tool, args = Path(argv[0]).name, list(argv[1:])
    env = dict(os.environ)
    real = find_real(tool, env, skip_wrappers=True)
    if real is None:
        print(f"keyfit: {tool} is not installed (no {tool} on PATH besides Keyfit's shim).\n"
              f"  Install it: {INSTALL[tool]}   (keyfit doctor shows where each tool is found)", file=sys.stderr)
        return 127
    depth = int(env.get("KEYFIT_SHIM_DEPTH") or 0)
    if depth >= MAX_DEPTH:  # shims calling shims: stop instead of recursing until the process table is full
        print(f"keyfit: {tool} nested {depth} levels deep through Keyfit — stopping to avoid a loop.\n"
              f"  Bypass once: KEYFIT_DISABLE=1 {tool} …", file=sys.stderr)
        return 1
    if env.get("KEYFIT_DISABLE") == "1":
        os.execve(real, [tool, *args], env)
    # counted on every path, not only where checks run: a wrapper later on PATH that calls the tool by name
    # comes back through here also in folders that are not registered
    env["KEYFIT_SHIM_DEPTH"] = str(depth + 1)

    try:
        if tool == "opencode" and subcommand(args, OPENCODE_SUBCOMMANDS) in OPENCODE_SHARED:
            os.execve(real, [tool, *opencode_private(args, env)], env)
        registry = Registry.load()
        shared = tool == "aws" and requested_profiles(args)
        if shared and set(shared) <= registry.shared_aws_profiles():
            # profiles of machine-wide tools rather than a project ([settings]): they run unchanged anywhere
            os.execve(real, [tool, *args], env)
        pinned = env.get("KEYFIT_PROJECT") if tool in PINNED else None
        project = registry.get(pinned) if pinned else resolve(".", registry).project
        if pinned and project is None:  # the session's environment is that project's: never run it unchecked
            raise Refused(f"keyfit: this session belongs to project {printable(pinned)}, which is not in the registry "
                          f"(renamed or removed?), so {tool} was not started.\n  Start the session again, or bypass "
                          f"once: KEYFIT_DISABLE=1 {tool} …")
        if tool in ("opencode", "copilot"):
            folder_check(tool, opencode_folder(args) if tool == "opencode" else copilot_folder(args), registry, project)
        if project is None:  # not a registered project: exactly as before Keyfit
            os.execve(real, [tool, *(opencode_private(args, env) if tool == "opencode" else args)], env)

        relevant = RELEVANT.get(tool)
        if is_login_command(tool, args):
            # run sign-in/setup with the project's environment (so `aws sso login` / `claude auth login`
            # target the project's profile / account) but without checks
            if tool == "supabase":
                supabase_login_check(project, args)
            if tool == "wrangler":
                wrangler_login_check(project, args, registry, env)
            if tool == "convex":
                convex_login_check(project, args)
            if tool == "copilot":
                copilot_login_check(project)
            login_env = login_environment(tool, project, env)
            if tool == "vercel":  # `vercel login` signs in to the project's folder
                args = vercel_login_args(project, args, login_env)
            if tool == "opencode":  # to the project's database, not a background server's
                opencode_server_option(project, args)
                args = opencode_standalone(args)
            os.execve(real, [tool, *args], login_env)
        if relevant and not any(kind in project.connections for kind in relevant):
            if tool in OPTIONAL:  # no account of its kind: it runs as before Keyfit, minus other projects' tokens
                os.execve(real, [tool, *args], environment.build(project, env, kinds=relevant))
            raise Refused(f"keyfit: project {project.name} has no {relevant[0]} account mapped, so {tool} was not "
                          f"started.\n  Map one: {MAP[relevant[0]]}   (bypass once: KEYFIT_DISABLE=1 {tool} …)")
        run_env = environment.build(project, env, kinds=relevant)  # aws/gh/vercel/…/convex: only their own account
        checks = environment.verify(project, run_env, remote=True, kinds=relevant, use_cache=True)
        failed = [c for c in checks if c.failed]
        if failed:
            _say([f"keyfit: {tool} not started, {project.name} needs attention"])
            for check in failed:
                _say(check.lines())
            if offer_sign_in(failed, project, env):
                run_env = environment.build(project, env, kinds=relevant)
                checks = environment.verify(project, run_env, remote=True, kinds=relevant, use_cache=True)
                failed = [c for c in checks if c.failed]
                if failed:
                    _say([f"keyfit: {tool} not started, {project.name} still needs attention"])
                    for check in failed:
                        _say(check.lines())
                else:
                    _say([f"keyfit: signed in, starting {tool}"])
            if failed:
                _say([f"  (bypass once: KEYFIT_DISABLE=1 {tool} …)"])
                return 1
        if tool in AGENTS:
            for check in (c for c in checks if c.status == "warn"):
                _say([f"keyfit: {check.lines()[0].strip()}"])
        args = adjust_args(tool, project, args, run_env, Path.cwd(), registry.mcp_owners())
    except Refused as refusal:
        print(refusal, file=sys.stderr)
        return 1
    except RegistryError as err:  # a hand edit: no project can be told apart, so nothing starts checked
        _say([f"keyfit: {tool} not started, {tilde(err.path)} has a mistake", f"  ✘ {err}", f"    → {err.fix}",
              f"  (bypass once: KEYFIT_DISABLE=1 {tool} …)"])
        return 1
    except Exception as err:  # a Keyfit bug must not silently drop the checks
        import traceback

        # the type and the place, never the message: messages can quote the files they read (a key, a token)
        where = traceback.extract_tb(err.__traceback__)[-1]
        print(f"keyfit: internal error ({type(err).__name__} in {Path(where.filename).name}:{where.lineno}) — "
              f"{tool} was not started.\n  Bypass once: KEYFIT_DISABLE=1 {tool} …", file=sys.stderr)
        return 1
    if env.get("KEYFIT_DRY_RUN") == "1":
        changed = sorted(k for k in set(env) | set(run_env) if env.get(k) != run_env.get(k))
        print(f"keyfit: project {project.name}, checks ok\n  would run: {real} {' '.join(args)}\n"
              f"  environment changes (names only): {', '.join(changed)}", file=sys.stderr)
        return 0
    os.execve(real, [tool, *args], run_env)
