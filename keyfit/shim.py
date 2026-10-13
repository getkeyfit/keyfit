"""The shim in front of `claude`, `codex`, `aws`, `gh` and `vercel` (ambient mode).

Three outcomes only:
  folder not registered      → run the real tool unchanged
  registered, all checks ok  → run it with the project's environment (silently)
  a check fails              → do not run it; print what is wrong and the fix

Inside a project session (KEYFIT_PROJECT set), `aws`, `gh` and `vercel` stay on
that session's project even after `cd` elsewhere; `claude` and `codex` re-resolve
from the folder, since starting a new agent elsewhere is deliberate.
Bypass once: KEYFIT_DISABLE=1 <tool> …
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from . import environment, mcp
from .adapters import vercel
from .checks import printable
from .realbin import find_real
from .registry import Project, Registry, canonical, tilde
from .resolve import resolve

TOOLS = ("claude", "codex", "aws", "gh", "vercel")
RELEVANT = {"aws": ("aws",), "gh": ("github",), "vercel": ("vercel",), "claude": None, "codex": None}
PINNED = ("aws", "gh", "vercel")
MAX_DEPTH = 6
CLAUDE_SUBCOMMANDS = {"agents", "attach", "auth", "auto-mode", "doctor", "gateway", "import", "install", "logs",
                      "mcp", "plugin", "purge", "respawn", "rm", "setup-token", "stop", "ultrareview", "update", "help"}
CODEX_SUBCOMMANDS = {"agents", "exec", "e", "review", "login", "logout", "mcp", "plugin", "app-server",
                     "remote-control", "app", "completion", "update", "doctor", "sandbox", "debug", "apply", "a",
                     "resume", "queue", "archive", "delete", "migrate-rollouts", "unarchive", "fork", "cloud",
                     "exec-server", "features", "help"}
CODEX_SESSIONS = {None, "exec", "e", "review", "resume", "fork"}
# Sign-in and setup commands always run — they are how a failed check gets fixed.
LOGIN_COMMANDS = {
    "aws": (("sso", "login"), ("sso", "logout"), ("login",), ("logout",), ("configure",)),
    "claude": (("auth",), ("setup-token",)),
    "codex": (("login",), ("logout",)),
    "gh": (("auth",),),
    "vercel": (("login",), ("logout",), ("switch",), ("whoami",), ("teams",)),
}
LOGIN_KINDS = {"aws": ("aws",), "claude": ("claude",), "codex": ("codex",), "gh": ("github",), "vercel": ("vercel",)}
VALUE_OPTIONS = {"--profile", "--region", "--output", "--endpoint-url", "--query", "--color", "--ca-bundle",
                 "--cli-read-timeout", "--cli-connect-timeout", "--sso-session", "-u", "--user", "-h", "--hostname",
                 "-c", "--config", "-m", "--model", "-Q", "--global-config", "-S", "--scope", "-T", "--team",
                 "-t", "--token", "-A", "--local-config", "--cwd", "--api"}


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
    words = positionals(args)
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
    return args


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


def vercel_login_args(project: Project, args: list[str], env: dict) -> list[str]:
    """Sign-in commands (`vercel login`, …) go to the project's login folder, unless the user named one."""
    conn = project.connections.get("vercel")
    if conn is None or any(name == "--global-config" for name, _ in vercel.options(args)):
        return args
    return vercel.config_args(conn, env) + args


def _say(lines: list[str]) -> None:
    for line in lines:
        print(line, file=sys.stderr)


def main(argv: list[str]) -> int:
    tool, args = Path(argv[0]).name, list(argv[1:])
    env = dict(os.environ)
    real = find_real(tool, env, skip_wrappers=True)
    if real is None:
        print(f"keyfit: the real {tool} was not found on PATH", file=sys.stderr)
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
        if project is None:
            os.execve(real, [tool, *args], env)  # not a registered project: exactly as before Keyfit

        relevant = RELEVANT.get(tool)
        if is_login_command(tool, args):
            # run sign-in/setup with the project's environment (so `aws sso login` / `claude auth login`
            # target the project's profile / account) but without checks; gh must manage its own tokens.
            login_env = environment.build(project, env, kinds=LOGIN_KINDS[tool])
            if tool == "gh":
                for var in ("GH_TOKEN", "GITHUB_TOKEN"):
                    login_env.pop(var, None)
            if tool == "vercel":  # `vercel login` signs in to the project's folder
                args = vercel_login_args(project, args, login_env)
            os.execve(real, [tool, *args], login_env)
        if relevant and not any(kind in project.connections for kind in relevant):
            raise Refused(f"keyfit: project {project.name} has no {relevant[0]} account mapped, so {tool} was not "
                          f"started.\n  Map one: keyfit add {relevant[0]} …   (bypass once: KEYFIT_DISABLE=1 {tool} …)")
        run_env = environment.build(project, env, kinds=relevant)  # aws/gh/vercel need only their own connection
        checks = environment.verify(project, run_env, remote=True, kinds=relevant, use_cache=True)
        failed = [c for c in checks if c.failed]
        if failed:
            _say([f"keyfit: {tool} not started, {project.name} needs attention"])
            for check in failed:
                _say(check.lines())
            _say([f"  (bypass once: KEYFIT_DISABLE=1 {tool} …)"])
            return 1
        if tool in ("claude", "codex"):
            for check in (c for c in checks if c.status == "warn"):
                _say([f"keyfit: {check.lines()[0].strip()}"])
        args = adjust_args(tool, project, args, run_env, Path.cwd(), registry.mcp_owners())
    except Refused as refusal:
        print(refusal, file=sys.stderr)
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
