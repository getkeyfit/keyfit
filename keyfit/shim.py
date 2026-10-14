"""The shim in front of `claude`, `codex`, `aws`, `gh`, `vercel` and `supabase` (ambient mode).

Three outcomes only:
  folder not registered      → run the real tool unchanged
  registered, all checks ok  → run it with the project's environment (silently)
  a check fails              → do not run it; print what is wrong and the fix (in a terminal, when the fix
                               is only a sign-in, offer to run it and then start the tool)

Inside a project session (KEYFIT_PROJECT set), `aws`, `gh`, `vercel` and `supabase` stay on
that session's project even after `cd` elsewhere; `claude` and `codex` re-resolve
from the folder, since starting a new agent elsewhere is deliberate.
Bypass once: KEYFIT_DISABLE=1 <tool> …
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from . import cache, environment, mcp, secrets
from .adapters import ADAPTERS, supabase, vercel
from .checks import printable
from .realbin import find_real
from .registry import Project, Registry, RegistryError, canonical, tilde
from .resolve import resolve

TOOLS = ("claude", "codex", "aws", "gh", "vercel", "supabase")
INSTALL = {"claude": "curl -fsSL https://claude.ai/install.sh | bash", "codex": "npm install -g @openai/codex",
           "aws": "brew install awscli", "gh": "brew install gh", "vercel": "npm install -g vercel",
           "supabase": "brew install supabase/tap/supabase"}
# How to map the one account `aws`, `gh` and `vercel` need inside a project before they start there.
MAP = {"aws": "keyfit add aws --profile <profile>", "github": "keyfit add github --login <login>",
       "vercel": "keyfit add vercel"}
RELEVANT = {"aws": ("aws",), "gh": ("github",), "vercel": ("vercel",), "supabase": ("supabase",), "claude": None,
            "codex": None}
# Tools that run unchanged in a project without an account of their kind: Supabase is often used only locally
# (supabase start), with no account at all.
OPTIONAL = ("supabase",)
PINNED = ("aws", "gh", "vercel", "supabase")
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
    "supabase": (("login",), ("logout",)),
}
LOGIN_KINDS = {"aws": ("aws",), "claude": ("claude",), "codex": ("codex",), "gh": ("github",), "vercel": ("vercel",),
               "supabase": ("supabase",)}
# Variables sign-in commands run without: gh must manage its own tokens, and `supabase login` its own login.
LOGIN_SCRUB = {"gh": ("GH_TOKEN", "GITHUB_TOKEN"), "supabase": ("SUPABASE_ACCESS_TOKEN", "SUPABASE_PROFILE")}
# Fixes that are nothing but a sign-in, which Keyfit offers to run when you start a tool yourself in a terminal
# (None: one value such as a profile name). Every other fix, including gh's (it switches gh's active account), is
# only printed. A variable a fix may set in front: Claude's and Codex's config folder of the account.
SIGN_INS = {
    "aws": (("login", "--profile", None), ("sso", "login", "--profile", None), ("sso", "login", "--sso-session", None)),
    "claude": (("auth", "login"),),
    "codex": (("login",),),
    "vercel": (("login",), ("login", "--global-config", None)),
}
SIGN_IN_VARS = {"claude": "CLAUDE_CONFIG_DIR", "codex": "CODEX_HOME"}
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
    if tool == "supabase":
        return supabase_args(project, args, env, cwd)
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
    words = positionals(args)
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
    if words[:1] == ["link"] and len(words) > 1:
        asked.append((f"link {words[1]}", words[1]))
    if words[:2] == ["projects", "delete"] and len(words) > 2:
        asked.append((f"projects delete {words[2]}", words[2]))
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
    word = positionals(args)[0]
    raise Refused(f"keyfit: this folder belongs to {project.name}, whose Supabase token Keyfit keeps "
                  f"({secrets.describe(conn.get('token', ''))}); `supabase {word}` would change only the supabase CLI's "
                  f"own login, which {project.name} does not use.\n  Another token for {project.name}: "
                  f"{supabase.add_command(conn)}   (paste it; or sign the CLI in outside {project.name}, then add "
                  f"--cli)\n  Bypass once: KEYFIT_DISABLE=1 supabase …")


def login_environment(tool: str, project: Project, env: dict) -> dict:
    """The environment a sign-in command runs with: the project's (so it signs in to the project's account), without
    the variables that would make the tool skip its own login."""
    login_env = environment.build(project, env, kinds=LOGIN_KINDS[tool])
    for var in LOGIN_SCRUB.get(tool, ()):
        login_env.pop(var, None)
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
        real = find_real(name, env, skip_wrappers=True)
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
            # target the project's profile / account) but without checks
            if tool == "supabase":
                supabase_login_check(project, args)
            login_env = login_environment(tool, project, env)
            if tool == "vercel":  # `vercel login` signs in to the project's folder
                args = vercel_login_args(project, args, login_env)
            os.execve(real, [tool, *args], login_env)
        if relevant and not any(kind in project.connections for kind in relevant):
            if tool in OPTIONAL:  # no account of its kind: it runs as before Keyfit, minus other projects' tokens
                os.execve(real, [tool, *args], environment.build(project, env, kinds=relevant))
            raise Refused(f"keyfit: project {project.name} has no {relevant[0]} account mapped, so {tool} was not "
                          f"started.\n  Map one: {MAP[relevant[0]]}   (bypass once: KEYFIT_DISABLE=1 {tool} …)")
        run_env = environment.build(project, env, kinds=relevant)  # aws/gh/vercel/supabase: only their own account
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
        if tool in ("claude", "codex"):
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
