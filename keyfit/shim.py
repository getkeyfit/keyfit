"""The shim in front of `claude`, `codex`, `opencode`, `copilot`, `gemini`, `aws`, `gh`, `vercel`, `supabase`,
`wrangler`, `convex`, `gcloud` and `firebase` (ambient mode).

Three outcomes only:
  folder not registered      → run the real tool unchanged
  registered, all checks ok  → run it with the project's environment (silently)
  a check fails              → do not run it; print what is wrong and the fix (in a terminal, when the fix
                               is only a sign-in or binding a folder of the project to its wrangler profile or
                               firebase sign-in, offer to run it and then start the tool)

Inside a project session (KEYFIT_PROJECT set), `aws`, `gh`, `vercel`, `supabase`, `wrangler`,
`convex`, `gcloud` and `firebase` stay on that session's project even after `cd` elsewhere; `claude`, `codex`,
`opencode`, `copilot` and `gemini` re-resolve from the folder, since starting a new agent elsewhere is deliberate.
Bypass once: KEYFIT_DISABLE=1 <tool> …
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from . import cache, environment, mcp, secrets
from .adapters import ADAPTERS, cloudflare, convex, copilot, firebase, gcloud, gemini, opencode, supabase, vercel
from .adapters.google import same
from .checks import printable
from .realbin import find_real
from .registry import Project, Registry, RegistryError, canonical, tilde
from .resolve import GitError, resolve

TOOLS = ("claude", "codex", "opencode", "copilot", "gemini", "aws", "gh", "vercel", "supabase", "wrangler", "convex",
         "gcloud", "firebase")
INSTALL = {"claude": "curl -fsSL https://claude.ai/install.sh | bash", "codex": "npm install -g @openai/codex",
           "opencode": "brew install opencode", "copilot": "npm install -g @github/copilot",
           "gemini": "npm install -g @google/gemini-cli",
           "aws": "brew install awscli", "gh": "brew install gh", "vercel": "npm install -g vercel",
           "supabase": "brew install supabase/tap/supabase",
           "wrangler": "npm install -g wrangler   (projects that have it run their own: npx wrangler …)",
           "convex": "npm install -g convex   (projects that have it run their own: npx convex …)",
           "gcloud": "brew install --cask gcloud-cli",
           "firebase": "npm install -g firebase-tools   (projects that have it run their own: npx firebase-tools …)",
           "npx": "brew install node"}
# How to map the one account `aws`, `gh` and `vercel` need inside a project before they start there.
MAP = {"aws": "keyfit add aws --profile <profile>",
       "github": "keyfit add github --login <a login gh auth status lists>", "vercel": "keyfit add vercel"}
RELEVANT = {"aws": ("aws",), "gh": ("github",), "vercel": ("vercel",), "supabase": ("supabase",),
            "wrangler": ("cloudflare",), "convex": ("convex",), "gcloud": ("gcloud",), "firebase": ("firebase",),
            "claude": None, "codex": None, "opencode": None, "copilot": None, "gemini": None}
# The agents: they start with all of the project's accounts, checked.
AGENTS = ("claude", "codex", "opencode", "copilot", "gemini")
# Tools that run unchanged in a project without an account of their kind: Supabase is often used only locally
# (supabase start), with no account at all, and so are wrangler (wrangler dev) and convex (an anonymous local
# deployment). gcloud keeps its own configurations, and many of its commands (components, emulators, config) need none;
# the firebase CLI keeps its own sign-ins, and its emulators need none.
OPTIONAL = ("supabase", "wrangler", "convex", "gcloud", "firebase")
PINNED = ("aws", "gh", "vercel", "supabase", "wrangler", "convex", "gcloud", "firebase")
MAX_DEPTH = 6
CLAUDE_SUBCOMMANDS = {"agents", "attach", "auth", "auto-mode", "daemon", "doctor", "gateway", "import", "install",
                      "logs", "mcp", "plugin", "purge", "respawn", "rm", "setup-token", "stop", "ultrareview", "update",
                      "help"}
# Claude Code's background sessions run under one supervisor per config folder, which keeps the environment of the start
# that launched it and gives it to every background session, in any folder. What starts or wakes one is refused where
# that environment holds a project's accounts (see claude_background); `daemon status`, `logs` and `stop` only read or
# stop it.
CLAUDE_BACKGROUND = {"agents", "attach", "respawn"}
CLAUDE_DAEMON_READ = {"status", "logs", "stop"}
CODEX_SUBCOMMANDS = {"agents", "exec", "e", "review", "login", "logout", "mcp", "plugin", "app-server",
                     "remote-control", "app", "completion", "update", "doctor", "sandbox", "debug", "apply", "a",
                     "resume", "queue", "archive", "delete", "migrate-rollouts", "unarchive", "fork", "cloud",
                     "exec-server", "features", "help"}
CODEX_SESSIONS = {None, "exec", "e", "review", "resume", "fork"}
# The interactive sessions, which use Codex's shared app server unless told --no-daemon, and the commands that work on
# that server: it keeps the environment of the start that launched it, for every folder.
CODEX_NO_DAEMON = {None, "resume", "fork"}
CODEX_SHARED = {"agents", "queue", "remote-control", "app-server"}
OPENCODE_SUBCOMMANDS = {"upgrade", "update", "uninstall", "acp", "api", "debug", "auth", "mcp", "plugin", "models",
                        "stats", "mini", "run", "session", "service", "reload", "pair", "serve"}
# OpenCode's background server serves every folder and keeps the environment of the start that launched it. Its own
# commands, and OpenCode's updates, run with your environment as outside a project (see opencode_private).
OPENCODE_SHARED = {"upgrade", "update", "uninstall", "api", "debug", "mcp", "plugin", "service", "reload", "pair",
                   "serve"}
OPENCODE_NO_SERVER = {"upgrade", "update", "uninstall"}
# Commands that take --standalone: a private server, with the session's environment and the project's sign-ins.
OPENCODE_STANDALONE = {None, "run", "mini", "models", "stats", "session", "auth", "api", "reload"}
# OpenCode's options (opencode.ai/docs/cli) that take a value, those that take several (yargs arrays) and its flags,
# to find the folder `opencode DIRECTORY` opens after them.
OPENCODE_VALUED = {"--server", "--session", "-s", "--prompt", "--log-level", "--completions", "-m", "--model", "--agent",
                   "--port", "--hostname", "--mdns-domain", "--command", "--format", "--title", "--attach", "-p",
                   "--password", "-u", "--username", "--dir", "--variant", "--cwd"}
OPENCODE_SEVERAL = {"--cors", "-f", "--file"}
OPENCODE_FLAGS = {"-c", "--continue", "--fork", "--auto", "--mdns", "--print-logs", "--pure", "--standalone",
                  "--thinking", "-h", "--help", "-v", "--version"}
COPILOT_SUBCOMMANDS = {"app", "login", "help", "init", "config", "update", "version", "workflow", "sessions",
                       "memories", "plugin", "mcp", "skill", "instruction", "lsp", "sandbox", "completion"}
COPILOT_SESSIONS = {None, "init", "workflow"}  # they run the agent: its MCP servers start
# Copilot's options whose values matter to Keyfit (the folder it works in, where its servers come from).
COPILOT_VALUED = ("-C", "--config-dir", "--additional-mcp-config", "--enable-mcp-server")
# Gemini CLI's own commands (they change its settings or extensions, and start no session) and its sign-in: Gemini CLI
# has no sign-in command, so Keyfit's is a session that signs in (its own dialog) and quits.
GEMINI_SUBCOMMANDS = {"mcp", "extensions", "extension", "skills", "skill", "hooks", "hook", "gemma"}
GEMINI_SIGN_INS = (["--prompt-interactive", "/quit"], ["-i", "/quit"], ["--prompt-interactive=/quit"], ["-i=/quit"])
# Sign-in and setup commands always run — they are how a failed check gets fixed. Only those: `gh auth token` and
# `status`, or `vercel teams invite`, run checked like any other command.
LOGIN_COMMANDS = {
    "aws": (("sso", "login"), ("sso", "logout"), ("login",), ("logout",), ("configure",)),
    "claude": (("auth",), ("setup-token",)),
    "codex": (("login",), ("logout",)),
    "gh": (("auth", "login"), ("auth", "logout"), ("auth", "refresh"), ("auth", "switch"), ("auth", "setup-git")),
    "vercel": (("login",), ("logout",), ("switch",), ("whoami",), ("teams", "switch"), ("teams", "change"),
               ("teams", "ls"), ("teams", "list")),
    "supabase": (("login",), ("logout",)),
    "wrangler": (("login",), ("logout",), ("whoami",), ("auth", "create"), ("auth", "activate"), ("auth", "deactivate"),
                 ("auth", "delete"), ("auth", "list"), ("auth", "keyring")),
    "convex": (("login",), ("logout",)),  # not `login status`: it only reads the login
    "opencode": (("auth",),),
    "copilot": (("login",),),
    # the fixes of a gcloud check (a configuration to create, its account or project to set, a setting to unset) and
    # what shows gcloud's sign-ins and configurations
    "gcloud": (("auth", "login"), ("auth", "list"), ("config", "set"), ("config", "unset"),
               ("config", "configurations", "create"), ("config", "configurations", "list"),
               ("config", "configurations", "describe")),
    # the fixes of a firebase check (a sign-in, binding the folder to it, the project to use) and what shows them
    "firebase": (("login",), ("login:add",), ("login:use",), ("login:list",), ("logout",), ("use",)),
}
LOGIN_KINDS = {"aws": ("aws",), "claude": ("claude",), "codex": ("codex",), "gh": ("github",), "vercel": ("vercel",),
               "supabase": ("supabase",), "wrangler": ("cloudflare",), "convex": ("convex",), "npx": ("convex",),
               "opencode": ("opencode",), "copilot": ("copilot",), "gemini": ("gemini",), "gcloud": ("gcloud",),
               "firebase": ("firebase",)}
# Variables sign-in commands run without: gh must manage its own tokens, Copilot CLI its own login (`copilot login`
# takes a token from the environment instead of signing in), convex its own login (with the override set,
# `convex login` would only say the injected token is valid), and gcloud's sign-in and `config set` write the
# account and project into the project's configuration (CLOUDSDK_ACTIVE_CONFIG_NAME) rather than beside it.
# (`supabase login` runs only in a project without a Supabase account, where Keyfit has removed the token it set for
# another project, and nothing else.)
LOGIN_SCRUB = {"gh": ("GH_TOKEN", "GITHUB_TOKEN"), "copilot": (copilot.TOKEN, "GH_TOKEN", "GITHUB_TOKEN"),
               "convex": (convex.OVERRIDE,), "npx": (convex.OVERRIDE,), "gcloud": (gcloud.ACCOUNT, gcloud.PROJECT_VAR)}
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
    "gemini": (tuple(gemini.SIGN_IN),),  # Gemini CLI's sign-in dialog in the account's home, then it quits
    "gcloud": (("auth", "login", None),),  # in the project's configuration, which it then uses
    # a first sign-in, another account, a renewed one; and binding the folder to the account (asked as a binding)
    "firebase": (("login",), ("login", "--reauth"), ("login:add",), ("login:add", None), ("login:use", None)),
}
SIGN_IN_VARS = {"claude": "CLAUDE_CONFIG_DIR", "codex": "CODEX_HOME", "opencode": "OPENCODE_DB",
                "gemini": gemini.HOME_VAR}
VALUE_OPTIONS = {"--profile", "--region", "--output", "--endpoint-url", "--query", "--color", "--ca-bundle",
                 "--cli-read-timeout", "--cli-connect-timeout", "--sso-session", "-u", "--user", "-h", "--hostname",
                 "-c", "--config", "-m", "--model", "-Q", "--global-config", "-S", "--scope", "-T", "--team",
                 "-t", "--token", "-A", "--local-config", "--cwd", "--api", "--workdir", "--project-ref",
                 "--project-id", "--org-id", "--network-id", "--dns-resolver", "-o"}
# gh's options that decide which account's token `gh auth token` and `status` print, and those taking a value.
GH_SHORT = {"h": "--hostname", "u": "--user", "t": "--show-token", "a": "--active"}
GH_VALUED = {"--hostname", "--user", "--json", "--jq", "--template"}
# Each agent's own options that take a value, and those that take every word up to the next option
# (`claude --add-dir ../a ../b`), from their CLI references: `-c` is `--continue` for Claude and OpenCode and takes none.
AGENT_OPTIONS = {
    "claude": ({"--advisor", "--agent", "--agents", "--append-subagent-system-prompt",
                "--append-subagent-system-prompt-file", "--append-system-prompt", "--append-system-prompt-file",
                "--autocompact", "--debug-file", "--effort", "--environment", "--exec", "--fallback-model", "--from-pr",
                "--input-format", "--json-schema", "--max-budget-usd", "--max-turns", "--model", "-n", "--name",
                "--output-format", "--permission-mode", "--permission-prompt-tool", "--permission-prompts",
                "--plugin-url", "--ref", "--remote-control", "--rc", "--remote-control-session-name-prefix", "-r",
                "--resume", "--session-id", "--setting-sources", "--settings", "--system-prompt", "--system-prompt-file",
                "--system-prompt-snapshot", "--teammate-mode", "-w", "--worktree"},
               {"--add-dir", "--allowedTools", "--allowed-tools", "--betas", "--channels",
                "--dangerously-load-development-channels", "--disallowedTools", "--disallowed-tools", "--file",
                "--mcp-config", "--plugin-dir", "--tools"}),
    "codex": ({"-c", "--config", "--enable", "--disable", "-m", "--model", "--local-provider", "-p", "--profile", "-s",
               "--sandbox", "-a", "--ask-for-approval", "-C", "--cd", "--add-dir", "--remote", "--remote-auth-token-env",
               "--color", "-o", "--output-last-message", "--output-schema"},
              {"-i", "--image"}),
    "opencode": (OPENCODE_VALUED, OPENCODE_SEVERAL),
    "copilot": ({"-C", "--config-dir", "--additional-mcp-config", "--enable-mcp-server", "--disable-mcp-server",
                 "--model", "-p", "--prompt", "--agent", "--log-level", "--log-dir"}, set()),
    "gemini": ({"-m", "--model", "-p", "--prompt", "-i", "--prompt-interactive", "-w", "--worktree", "--approval-mode",
                "-r", "--resume", "--delete-session", "-o", "--output-format"},
               {"--allowed-mcp-server-names", "--allowedMcpServerNames", "--allowed-tools", "-e", "--extensions",
                "--include-directories"}),
}


class Refused(Exception):
    pass


def positionals(args: list[str], tool: str | None = None) -> list[str]:
    """The command words, skipping options and the values of the tool's options (AGENT_OPTIONS, else VALUE_OPTIONS).
    An option Keyfit does not know may take the next word as its value (`claude --verbose auth` …), so the words stop
    there: an unclear command line is never taken for a sign-in command, which would skip every check."""
    valued, several = AGENT_OPTIONS.get(tool, (VALUE_OPTIONS, set()))
    out = []
    taking = False  # the words of an option that takes several
    for i, arg in enumerate(args):
        if arg == "--":
            break
        if i > 0 and args[i - 1] in valued:
            continue
        if arg.startswith("-"):
            taking = arg in several
            if "=" not in arg and arg not in valued and not taking and i + 1 < len(args) \
                    and not args[i + 1].startswith("-"):
                break
            continue
        if not taking:
            out.append(arg)
    return out


def is_login_command(tool: str, args: list[str]) -> bool:
    if tool == "gemini":
        return args in GEMINI_SIGN_INS
    readers = {"supabase": supabase.words, "wrangler": cloudflare.words, "convex": convex.words, "gcloud": gcloud.words,
               "firebase": firebase.words}
    words = readers[tool](args) if tool in readers else positionals(args, tool)
    if tool == "convex" and words[:2] == ["login", "status"]:
        return False
    return any(tuple(words[:len(prefix)]) == prefix for prefix in LOGIN_COMMANDS.get(tool, ()))


def subcommand(tool: str, args: list[str], known: set[str]) -> str | None:
    """The tool's own command: its first command word (positionals), when that is one of *known*. A later word never
    is (`gemini summarize the mcp setup` is a session), and an unclear command line has none: it is a session, with
    the checks and the MCP hiding."""
    words = positionals(args, tool)
    return words[0] if words and words[0] in known else None


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
                owners: dict | None = None, seen=()) -> list[str]:
    """*seen*: the shell variables the checks found that the tool must start without (OpenCode's)."""
    if tool == "aws":
        aws_profiles(project, args)
        return args
    if tool == "gh":
        return gh_args(project, args)
    if tool == "vercel":
        return vercel_args(project, args, env, cwd)
    if tool == "supabase":
        return supabase_args(project, args, env, cwd)
    if tool == "wrangler":
        return wrangler_args(project, args, env, cwd)
    if tool == "convex":
        return convex_args(project, args, env, cwd)
    if tool == "firebase":
        return firebase_args(project, args, env, cwd)
    if tool == "claude":
        if subcommand(tool, args, CLAUDE_SUBCOMMANDS):
            return args
        env["CLAUDE_CODE_DISABLE_AGENT_VIEW"] = "1"  # no background sessions, also from ← and /background
        given, configs, rest = claude_given(args)  # one --settings: a repeated option keeps its last value
        try:
            settings = mcp.claude_settings(project, env, cwd, owners, given[-1] if given else None, configs)
        except mcp.Unreadable as error:
            if error.option:
                raise Refused(f"keyfit: claude not started: the JSON given to {error.option} is not a JSON object "
                              f"Keyfit can read, so it can't hide the MCP servers {project.name} must not see.\n"
                              f"  Fix it (bypass once: KEYFIT_DISABLE=1 claude …)") from None
            raise unreadable(tool, project, error) from None
        if given and not given[-1].lstrip().startswith("{") and settings.get("env"):  # a file, kept off the command line
            raise Refused(f"keyfit: claude not started: your --settings file {printable(given[-1])} sets variables (env), "
                          f"and Keyfit would have to put them on Claude's command line, where every process can read "
                          f"them, to add the MCP servers {project.name} must not see.\n  Set them in your shell instead "
                          f"(bypass once: KEYFIT_DISABLE=1 claude …)")
        return (["--settings", json.dumps(settings)] if settings else []) + rest
    if tool == "codex":
        sub = subcommand(tool, args, CODEX_SUBCOMMANDS)
        if sub in CODEX_SHARED:
            raise Refused(f"keyfit: codex not started: `codex {sub}` works on Codex's shared app server, which keeps "
                          f"the environment it started with for every folder: {project.name}'s accounts could reach "
                          f"other folders, or another project's accounts this one.\n  Run it in a terminal outside "
                          f"every project (bypass once: KEYFIT_DISABLE=1 codex …)")
        if sub not in CODEX_SESSIONS:
            return args
        try:
            extra = mcp.codex_overrides(project, env, owners, cwd, args)
        except mcp.Unreadable as error:
            raise unreadable(tool, project, error) from None
        except mcp.Unhideable as server:
            raise Refused(f"keyfit: codex not started, {project.name} must not see MCP server {printable(str(server))}, "
                          f"and Codex cannot hide a name with a dot, \"=\" or a space at either end.\n"
                          f"  Rename it in ~/.codex/config.toml (underscores work), or remove it.") from None
        back = mcp.codex_reenabled(args, extra)
        if back:
            raise Refused(f"keyfit: this folder belongs to {project.name}; refusing -c {printable(back)}: it would turn "
                          f"on an MCP server {project.name} must not see.\n  Bypass once: KEYFIT_DISABLE=1 codex …")
        if sub in CODEX_NO_DAEMON:
            extra.append("--no-daemon")  # the shared daemon keeps the first launcher's environment
        return extra + args
    if tool == "opencode":
        return opencode_args(project, args, env, cwd, owners, seen)
    if tool == "copilot":
        return copilot_args(project, args, env, cwd, owners)
    if tool == "gemini":
        return gemini_args(project, args, env, cwd, owners)
    return args


def aws_profiles(project: Project, args: list[str]) -> None:
    """Refuse --profile other than the project's: another profile's credentials, and with the sign-in commands its
    sign-in or settings (`aws configure export-credentials --profile other` prints its keys). The shared ones
    ([settings] shared_aws_profiles) ran before. A project without an AWS account signs in to any profile."""
    conn = project.connections.get("aws")
    if not isinstance(conn, dict):
        return
    profile = conn.get("profile")
    for asked in requested_profiles(args):
        if asked != profile:
            raise Refused(f"keyfit: this folder belongs to {project.name} (AWS profile {profile}); "
                          f"refusing --profile {asked}.\n  Bypass once: KEYFIT_DISABLE=1 aws …")


def gh_options(args: list[str]) -> list[tuple[str, str | None]]:
    """(long name, value) of gh's options before `--`, read like gh: `--user x`, `--user=x`, `-u x`, `-ux` and
    letters grouped (`-at`). A switch has the value None, or what follows its "=" (`--show-token=false`)."""
    found, i = [], 0
    while i < len(args):
        arg = args[i]
        if arg == "--":
            break
        if arg.startswith("--"):
            name, eq, value = arg.partition("=")
            if name in GH_VALUED and not eq:
                value = args[i + 1] if i + 1 < len(args) else None
                i += 1
            found.append((name, value if eq or name in GH_VALUED else None))
        elif arg.startswith("-") and len(arg) > 1:
            letters = arg[1:]
            for j, letter in enumerate(letters):
                name, rest = GH_SHORT.get(letter, "-" + letter), letters[j + 1:]
                if name in GH_VALUED:  # the rest of the word is its value, else the next word
                    if not rest:
                        rest = args[i + 1] if i + 1 < len(args) else None
                        i += 1
                    found.append((name, rest.removeprefix("=") if rest else rest))
                    break
                if rest.startswith("="):
                    found.append((name, rest[1:]))
                    break
                found.append((name, None))
        i += 1
    return found


def gh_switch(given: list[tuple[str, str | None]], name: str) -> bool:
    values = [value for option, value in given if option == name]  # the last one counts
    return bool(values) and (values[-1] is None or values[-1].lower() not in ("false", "f", "0"))


def gh_args(project: Project, args: list[str]) -> list[str]:
    """`gh auth token`, `status` and `git-credential` run with the project's GH_TOKEN, which gh answers with first.
    Refuse what would make them answer with another account's token: --user other than the project's login,
    `--secure-storage` without it (the token of gh's active account) and `--show-token` without --active (the tokens
    of every account gh keeps)."""
    if positionals(args)[:1] != ["auth"]:
        return args
    login = project.connections["github"].get("login") or ""
    refuse = f"keyfit: this folder belongs to {project.name} (GitHub {login}); refusing"
    bypass = "\n  Bypass once: KEYFIT_DISABLE=1 gh …"
    given = gh_options(args)
    users = [value for name, value in given if name == "--user"]
    for user in users:
        if (user or "").lower() != login.lower():  # GitHub logins ignore case
            raise Refused(f"{refuse} --user {printable(user or '')}.{bypass}")
    if gh_switch(given, "--secure-storage") and not users:
        raise Refused(f"{refuse} --secure-storage without --user {login}: gh would read the token of its active "
                      f"account, which may be another one.{bypass}")
    if gh_switch(given, "--show-token") and not gh_switch(given, "--active"):
        raise Refused(f"{refuse} --show-token without --active: gh would print the token of every account it "
                      f"keeps, not only {login}'s.\n  Only {login}'s: gh auth status --active --show-token{bypass}")
    return args


def claude_background(args: list[str], env: dict, project: Project | None) -> None:
    """Refuse what starts or wakes Claude Code's background sessions (`--bg`, CLAUDE_BACKGROUND, `daemon run`) in a
    project, and inside a session Keyfit started (KEYFIT_PROJECT set), whose environment holds that project's accounts.
    Sessions in a project start with background sessions off (adjust_args); elsewhere Claude runs unchanged."""
    session = env.get("KEYFIT_PROJECT")
    if project is None and not session:
        return
    end = args.index("--") if "--" in args else len(args)
    sub = subcommand("claude", args, CLAUDE_SUBCOMMANDS)
    if sub == "daemon":
        words = [a for a in args[:end] if not a.startswith("-")]
        after = words[words.index(sub) + 1:][:1]
        command = None if after and after[0] in CLAUDE_DAEMON_READ else " ".join([sub, *after])
    elif sub in CLAUDE_BACKGROUND:
        command = sub
    elif sub is None:
        command = next((a for a in args[:end] if a in ("--bg", "--background")), None)
    else:
        command = None
    if command is None:
        return
    if session:
        raise Refused(f"keyfit: claude not started: inside a session of {printable(session)}, "
                      f"`claude {printable(command)}` could start Claude's background supervisor with that project's "
                      f"accounts, and every background session, in any folder, would get them.\n  Run it in a terminal "
                      f"outside the session (bypass once: KEYFIT_DISABLE=1 claude …)")
    raise Refused(f"keyfit: claude not started: `claude {printable(command)}` runs under Claude's background "
                  f"supervisor, which keeps the environment it started with for every background session, in any "
                  f"folder: {project.name}'s accounts could reach other folders, or another project's accounts this "
                  f"one.\n  Bypass once, with your own environment: KEYFIT_DISABLE=1 claude …")


def claude_given(args: list[str]) -> tuple[list[str], list[str], list[str]]:
    """(the values of your own --settings, those of --mcp-config, the command line without --settings): `--settings X`,
    `--settings=X`, and every word after --mcp-config up to the next option. A word that looks like an option counts as
    one; words after `--` are a prompt."""
    end = args.index("--") if "--" in args else len(args)
    settings, configs, rest = [], [], []
    taking, i = False, 0
    while i < end:
        arg = args[i]
        name, eq, value = arg.partition("=")
        if arg.startswith("-"):
            taking = arg == "--mcp-config"
        elif taking:
            configs.append(arg)
        if name == "--mcp-config" and eq:
            configs.append(value)
        if name == "--settings" and (eq or i + 1 < end):
            settings.append(value if eq else args[i + 1])
            i += 1 if eq else 2
            continue
        rest.append(arg)
        i += 1
    return settings, configs, rest + args[end:]


def codex_private(args: list[str], env: dict) -> list[str]:
    """Codex in a folder outside every project. In your terminal it runs unchanged. Inside a session Keyfit started
    (KEYFIT_PROJECT set), the environment holds that project's accounts, which Codex's shared app server, started now,
    would keep for every folder: sessions start without it (--no-daemon), and the commands that work on it are
    refused."""
    if not env.get("KEYFIT_PROJECT"):
        return args
    sub = subcommand("codex", args, CODEX_SUBCOMMANDS)
    if sub in CODEX_SHARED:
        raise Refused(f"keyfit: codex not started: inside a session of {printable(env['KEYFIT_PROJECT'])}, "
                      f"`codex {sub}` could start Codex's shared app server with that project's accounts, and every "
                      f"folder would share them.\n  Run it in a terminal outside the session (bypass once: "
                      f"KEYFIT_DISABLE=1 codex …)")
    return ["--no-daemon"] + args if sub in CODEX_NO_DAEMON else args


def opencode_standalone(args: list[str]) -> list[str]:
    """Add --standalone (OpenCode's private server, with this environment) where the command takes it, unless a
    server is named already. `opencode auth` and `opencode session` alone print their commands and take none."""
    end = args.index("--") if "--" in args else len(args)
    sub = subcommand("opencode", args, OPENCODE_SUBCOMMANDS)
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
    if subcommand("opencode", args, OPENCODE_SUBCOMMANDS) == "acp" and "--login" in args[:end]:
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
    sub = subcommand("opencode", args, OPENCODE_SUBCOMMANDS)
    if sub in OPENCODE_NO_SERVER or (sub == "acp" and "--login" not in args[:end]):  # acp: always a private server
        return args
    standalone = opencode_standalone(args)
    if standalone != args or "--standalone" in args or sub in ("auth", "session"):
        return standalone
    raise Refused(f"keyfit: opencode not started: inside a session of {printable(env['KEYFIT_PROJECT'])}, "
                  f"`opencode {sub or ' '.join(args)}` could start OpenCode's background server with that project's "
                  f"accounts, and every folder would share them.\n  Run it in a terminal outside the session "
                  f"(bypass once: KEYFIT_DISABLE=1 opencode …)")


def unreadable(tool: str, project: Project, error: mcp.Unreadable) -> Refused:
    """A config file the agent loads that Keyfit can't parse: the agent would start with servers Keyfit didn't see."""
    return Refused(f"keyfit: {tool} not started: Keyfit can't parse {printable(tilde(error.path))}, so it can't hide "
                   f"the MCP servers there that {project.name} must not see.\n"
                   f"  Fix the file (bypass once: KEYFIT_DISABLE=1 {tool} …)")


def opencode_mcp(project: Project, env: dict, cwd: Path, owners: dict | None) -> None:
    try:
        mcp.opencode_environment(project, env, cwd, owners)
    except mcp.Unreadable as error:
        if error.path:
            raise unreadable("opencode", project, error) from None
        raise Refused(f"keyfit: opencode not started: OPENCODE_CONFIG_CONTENT in your environment is not a JSON "
                      f"object, so Keyfit cannot switch off the MCP servers {project.name} must not see.\n"
                      f"  Fix or unset it (bypass once: KEYFIT_DISABLE=1 opencode …)") from None


def opencode_args(project: Project, args: list[str], env: dict, cwd: Path, owners: dict | None = None,
                  seen=()) -> list[str]:
    """Sessions, and the commands that read the project's sign-ins and sessions, run on a private server
    (--standalone): the background server serves every folder and keeps the environment of the start that launched
    it. Every MCP server the project must not see is switched off, for the folder OpenCode opens; with an OpenCode
    account, provider keys from the shell go: the fixed ones, and those OpenCode's report named at the check (*seen*,
    cached with it)."""
    opencode_server_option(project, args)
    if "opencode" in project.connections:
        opencode.scrub(env, seen)
    folder = opencode_folder(args, cwd)
    opencode_mcp(project, env, (cwd / folder).resolve() if folder else cwd, owners)
    return opencode_standalone(args)


def opencode_folder(args: list[str], cwd: Path | None = None) -> str | None:
    """The folder OpenCode works in, when one is named (relative to *cwd*, default the current folder): `opencode
    DIRECTORY`, `run --dir DIRECTORY`, `acp --cwd DIRECTORY`. Refused: a folder after an option Keyfit doesn't know
    (or one that takes several values), which may take that folder as its value or leave it to OpenCode."""
    base = Path(cwd or os.curdir)
    end = args.index("--") if "--" in args else len(args)
    named = [value if eq else args[i + 1] if i + 1 < end else ""
             for i, (name, eq, value) in enumerate(arg.partition("=") for arg in args[:end]) if name in ("--dir", "--cwd")]
    if named:
        return named[-1] if named[-1] and (base / named[-1]).is_dir() else None
    if subcommand("opencode", args, OPENCODE_SUBCOMMANDS):
        return None
    skip, unclear = False, None  # unclear: an option whose words, up to the next option, may be its values
    for arg in args[:end]:
        if skip:
            skip = False
        elif arg.startswith("-"):
            skip = arg in OPENCODE_VALUED
            unclear = None if skip or "=" in arg or arg in OPENCODE_FLAGS else arg
        elif unclear:
            if (base / arg).is_dir() and (base / arg).resolve() != base.resolve():
                raise Refused(f"keyfit: opencode not started: Keyfit can't tell whether {printable(arg)} is a value of "
                              f"{printable(unclear)} or the folder OpenCode opens, which would get this folder's "
                              f"accounts.\n  Name the folder first (opencode DIR {printable(unclear)} …), or the value "
                              f"with = ({printable(unclear)}=VALUE)   (bypass once: KEYFIT_DISABLE=1 opencode …)")
        else:
            return arg if arg not in OPENCODE_SUBCOMMANDS and (base / arg).is_dir() else None
    return None


def codex_folder(args: list[str], cwd: Path | None = None) -> str | None:
    """The folder `codex -C DIRECTORY` (or `--cd`) works in, when it is one."""
    folder = mcp._codex_option(args, "-C", "--cd")
    return folder if folder and os.path.isdir(os.path.join(cwd or os.curdir, folder)) else None


def folder_check(tool: str, folder: str | None, registry: Registry, project: Project | None) -> None:
    """`opencode DIRECTORY` (`run --dir`, `acp --cwd`), `copilot -C DIRECTORY` and `codex -C DIRECTORY` work in that
    folder with this folder's accounts: refuse when the two folders belong to different projects (or only one is
    registered)."""
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
    sub = subcommand("copilot", args, COPILOT_SUBCOMMANDS)
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
    try:
        names = mcp.copilot_hidden(project, env, (cwd / folder).resolve() if folder else cwd, owners,
                                   config_dir=str(cwd / Path(config[-1]).expanduser()) if config else None,
                                   additional=[value for name, value in given if name == "--additional-mcp-config" and value])
    except mcp.Unreadable as error:
        raise unreadable("copilot", project, error) from None
    for name, value in given:
        if name == "--enable-mcp-server" and value in names:
            raise Refused(f"keyfit: this folder belongs to {project.name}; refusing --enable-mcp-server "
                          f"{printable(value)}: that MCP server belongs to another project.\n"
                          f"  Bypass once: KEYFIT_DISABLE=1 copilot …")
    return [f"--disable-mcp-server={name}" for name in names] + args


def gemini_args(project: Project, args: list[str], env: dict, cwd: Path, owners: dict | None = None) -> list[str]:
    """With a Gemini CLI account, Gemini CLI starts without the variables that would pick another sign-in, key or Google
    Cloud project (gemini.scrub). Sessions (`gemini`, `gemini QUERY`, `gemini -p …`, and the sign-in) start with
    --allowed-mcp-server-names naming every server they keep when the project must not see one; an allowlist of your
    own that names such a server is refused. Gemini CLI's own commands (mcp, extensions, …) run as typed."""
    conn = project.connections.get("gemini")
    if isinstance(conn, dict):
        gemini.scrub(conn, env)
    if subcommand("gemini", args, GEMINI_SUBCOMMANDS):
        return args
    try:
        hide, kept = mcp.gemini_hidden(project, env, cwd, owners)
    except mcp.Unreadable as error:
        raise unreadable("gemini", project, error) from None
    if not hide:
        return args
    given = mcp.gemini_allowed(args)
    clash = [name for name in given if name in hide]
    if clash:
        raise Refused(f"keyfit: this folder belongs to {project.name}; refusing --allowed-mcp-server-names "
                      f"{printable(clash[0])}: that MCP server belongs to another project.\n"
                      f"  Bypass once: KEYFIT_DISABLE=1 gemini …")
    if given:  # your own allowlist already leaves them out
        return args
    return [f"--allowed-mcp-server-names={','.join(kept or [mcp.GEMINI_NONE])}"] + args


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
    """Refuse options that would leave the project's Vercel login or team (like `aws --profile`) and a folder to
    work in that is linked to another team (--cwd, `deploy <path>`, `vercel <path>`, `dev <dir>`), then add the
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
    command, paths = vercel.targets(args)
    moved = [value for name, value in given if name == "--cwd" and value]
    base = cwd / moved[-1] if moved and command == "deploy" else cwd  # `dev [dir]` starts from the shell's folder
    for path in paths:
        if (base / path).is_dir():  # the CLI deploys only a folder
            link = vercel.foreign_link(conn, base / path, env)
            if link is not None:
                raise Refused(f"keyfit: {tilde(link[0])} links {path} to another Vercel team ({printable(link[1])}), but "
                              f"this folder belongs to {project.name} (Vercel team {team}); refusing {command} {path}."
                              f"{bypass}")
    names = {name for name, _ in given}
    extra = [] if "--global-config" in names else vercel.config_args(conn, env)
    if team and not names & {"--scope", "--team"}:
        extra += ["--scope", team]
    return extra + args


def supabase_args(project: Project, args: list[str], env: dict, cwd: Path) -> list[str]:
    """Refuse what would leave the project's Supabase login or organization (like `aws --profile`): another profile
    or --org-id, and a project the token reaches in another organization (or cannot reach) named by --project-ref,
    `gen … --project-id`, a word shaped like a ref (`link <ref>`, `projects delete <ref>`) or the link of a
    --workdir. The folder's own link was checked already."""
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
        if name == "--project-ref" or (name == "--project-id" and "gen" in words):  # `stop --project-id`: local
            asked.append((f"{name} {value}", value))
        if name == "--workdir" and not named & {"--project-ref", "--project-id"}:
            for ref, source in supabase.linked_refs(cwd, {**env, "SUPABASE_WORKDIR": value}):
                asked.append((f"--workdir {value} (linked to {ref} in {source})", ref))
    # every word that could be a ref, wherever it stands: an option Keyfit does not know may take the word before
    # the command as its value, or hold one between the command and the ref
    asked += [(" ".join(words[:i + 1]), word) for i, word in enumerate(words) if supabase.REF.fullmatch(word)]
    checked = {ref for ref, _ in supabase.linked_refs(cwd, env)}
    for shown, ref in asked:
        if ref in checked or not supabase.REF.fullmatch(ref):  # checked already; or a branch name, which the CLI
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
    --profile, a --cwd or --config whose folder is bound to another profile or whose config names another
    account_id, an entry file (`wrangler deploy ../other/src/index.ts`) from whose folder wrangler would read such a
    config, and a --cwd folder or --env-file whose .env sets wrangler's API host or sign-in. The folder's own
    binding, config and .env files were checked already."""
    conn = project.connections["cloudflare"]
    title = conn.get("account_name") or conn["account_id"]
    refuse = (f"keyfit: this folder belongs to {project.name} (wrangler profile {conn['profile']}, Cloudflare account "
              f"{printable(title)}); refusing")
    bypass = "\n  Bypass once: KEYFIT_DISABLE=1 wrangler …"
    asked = moved = None
    configs = []  # `wrangler dev` takes several
    for name, value in cloudflare.options(args):
        if value is None:
            continue
        if name == "--profile":
            if value != conn["profile"]:
                raise Refused(f"{refuse} --profile {printable(value)}: it signs in as another wrangler profile.{bypass}")
            asked = value
        elif name == "--cwd":
            moved = value
        elif name == "--config":
            configs.append(value)
    wrangler_endpoints(project, args, cwd)
    # wrangler moves to --cwd first (process.chdir: symlinks resolved), then reads --config, else the config found from
    # an entry file's folder
    base = os.path.realpath(os.path.join(cwd, moved)) if moved else str(cwd)
    entries = [] if configs else cloudflare.entries(args, base)
    if moved is None and not configs and not entries:
        return args
    given = [(config, None) for config in configs] or [(None, entry) for entry in entries] or [(None, None)]
    for config, entry in given:
        path = Path(os.path.abspath(os.path.join(base, config))) if config else None
        problem = cloudflare.mismatch(conn, path.parent if path else base, env, asked=asked, config=path, entry=entry)
        if problem:
            shown = " ".join(part for part in (moved and f"--cwd {moved}", config and f"--config {config}", entry)
                             if part)
            there = path.parent if path else os.path.join(base, os.path.dirname(entry)) if entry else base
            raise Refused(f"{refuse} {printable(shown)}: {problem[0]}.\n  → {elsewhere(project, there) or problem[1]}"
                          f"{bypass}")
    return args


def elsewhere(project: Project, folder) -> str | None:
    """The advice for a refusal about *folder* when it is not one of the project's: never to bind it or change its
    config, which would move another project (or a folder outside every project) to this project's account."""
    there = resolve(folder, Registry.load()).project
    if there is not None and there.name == project.name:
        return None
    whose = f"{tilde(canonical(folder))} belongs to {there.name}" if there else \
        f"{tilde(canonical(folder))} is outside every project"
    return f"run wrangler on {project.name}'s own folders and configs ({printable(whose)})"


def wrangler_endpoints(project: Project, args: list[str], cwd) -> None:
    """Refuse a wrangler that would load its API host, sign-in endpoints or config folder from .env files, which beat
    nothing Keyfit removed: those of the folder it runs in (also after --cwd, symlinks resolved), with --env NAME
    also .env.NAME and .env.NAME.local, or the files named with --env-file."""
    given = cloudflare.options(args)
    moved = [value for name, value in given if name == "--cwd" and value]
    named = [value for name, value in given if name == "--env-file" and value]
    envs = [value for name, value in given if name == "--env" and value]
    folder = os.path.realpath(os.path.join(cwd, moved[-1])) if moved else str(cwd)
    found = cloudflare.endpoints_set(folder, named, envs)
    if found:
        detail, fix = cloudflare.endpoints_problem(found)
        raise Refused(f"keyfit: this folder belongs to {project.name}; refusing wrangler: {detail}.\n  → {fix}\n"
                      f"  Bypass once: KEYFIT_DISABLE=1 wrangler …")


def wrangler_login_check(project: Project | None, args: list[str], registry: Registry, env: dict) -> None:
    """wrangler's sign-in and binding commands run without checks (they are how a failed check gets fixed), except
    those that would move a registered folder off its project's profile: `login`/`logout` (wrangler's default login
    only) in a project with a named profile, `auth activate`/`deactivate` of a folder whose project uses another
    profile or that holds a project's folder (a root or a worktree) it would move off its profile, `auth create` of
    another project's profile and `auth delete` of any project's (it unbinds its folders). Outside every project
    (*project* None) only the `auth activate`, `deactivate` and `delete` rules apply. In a project with a Cloudflare
    account, none runs where a .env file would pick wrangler's API host or sign-in."""
    words = cloudflare.words(args)
    conn = project.connections.get("cloudflare") if project else None
    own = conn.get("profile") if isinstance(conn, dict) else None
    bypass = "\n  Bypass once: KEYFIT_DISABLE=1 wrangler …"
    if isinstance(conn, dict):  # a sign-in runs without checks: never with the folder's endpoints
        wrangler_endpoints(project, args, os.getcwd())
    if words[:1] in (["login"], ["logout"]):
        if project and own and own != cloudflare.DEFAULT:
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
    if project and action == "create" and name and name != own and users.get(name):  # a shared one stays its own
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
    base = os.path.realpath(os.path.join(os.getcwd(), moved[-1])) if moved else os.getcwd()  # wrangler's process.cwd()
    folder = os.path.abspath(os.path.join(base, target or "."))  # path.resolve, as wrangler stores it
    owner = resolve(folder, registry).project
    theirs = owner.connections.get("cloudflare") if owner else None
    if isinstance(theirs, dict) and isinstance(theirs.get("profile"), str):
        if action == "activate" and name and name != theirs["profile"]:
            raise Refused(f"keyfit: refusing `wrangler auth activate {printable(name)}` for {tilde(folder)}: that "
                          f"folder belongs to {owner.name}, which uses wrangler profile {theirs['profile']}.{bypass}")
        if action == "deactivate" and cloudflare.bindings(env).get(folder) == theirs["profile"]:
            raise Refused(f"keyfit: refusing `wrangler auth deactivate` for {tilde(folder)}: {owner.name} uses "
                          f"wrangler profile {theirs['profile']} there.{bypass}")
    if action == "activate" and not name:
        return  # wrangler stops: no profile named
    table = cloudflare.bindings(env)  # a folder that holds a project's folders: ~/code, /, ~/.superset/worktrees/<repo>
    after = {d: p for d, p in table.items() if d != folder}
    if action == "activate":
        after[folder] = name
    for other in registry.projects().values():
        theirs = other.connections.get("cloudflare")
        if not isinstance(theirs, dict) or not isinstance(theirs.get("profile"), str):
            continue
        for place in project_folders(other):
            if cloudflare.bound(place, table)[0] == theirs["profile"] != cloudflare.bound(place, after)[0]:
                shown = f"`wrangler auth {action}{f' {printable(name)}' if action == 'activate' else ''}`"
                raise Refused(f"keyfit: refusing {shown} for {printable(tilde(folder))}: it holds "
                              f"{printable(tilde(place))}, where {other.name} uses wrangler profile "
                              f"{theirs['profile']}.{bypass}")


def project_folders(project: Project) -> list[str]:
    """The project's folders as wrangler sees them when it runs there (symlinks resolved): its roots and the same
    folder in each linked worktree of their repositories (Superset's under ~/.superset/worktrees too)."""
    import subprocess

    found = [str(root) for root in project.canonical_roots()]
    for root in list(found):
        try:
            out = subprocess.run(["git", "-C", root, "worktree", "list", "--porcelain"], capture_output=True,
                                 text=True, timeout=10, check=False)
        except (OSError, subprocess.SubprocessError):
            continue
        trees = [str(canonical(line[9:])) for line in out.stdout.splitlines() if line.startswith("worktree ")]
        if trees and (root == trees[0] or root.startswith(trees[0].rstrip(os.sep) + os.sep)):  # the main one first
            inside = os.path.relpath(root, trees[0])
            found += [os.path.normpath(os.path.join(tree, inside)) for tree in trees[1:]]
    return list(dict.fromkeys(found))


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
        if "|" in value and name in ("--team", "--deployment", "--deployment-name"):  # a deploy key: never printed
            raise Refused(f"{refuse} {name}: its value holds a deploy key (…|…).{bypass}")
        if name == "--team" and value != team:
            raise Refused(f"{refuse} --team {printable(value)}.{bypass}")
        if name == "--deployment" and value.count(":") == 2 and value.split(":")[0] != team:
            raise Refused(f"{refuse} --deployment {printable(value)}: another team.{bypass}")
        if name == "--deployment-name" or (name == "--deployment" and convex.DEPLOYMENT_NAME.fullmatch(value)):
            # any other --deployment is a ref inside the folder's project; only a deployment name is printed or sent
            asked.append((f"{name} {value}" if convex.DEPLOYMENT_NAME.fullmatch(value) else name, value))
        if name == "--env-file":
            values = convex.read_env_file(cwd / value)
            if values is None:
                raise Refused(f"{refuse} --env-file {printable(value)}: Keyfit cannot read it, so it cannot check the "
                              f"deployment it names.{bypass}")
            aim = convex.target(cwd, env, files=values)
            if aim and aim["kind"] == "invalid":
                raise Refused(f"{refuse} --env-file {printable(value)}: {aim['source']} {aim['why']}.{bypass}")
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


def gcloud_options(project: Project, conn: dict, args: list[str]) -> None:
    """Refuse what would leave the project's gcloud configuration, Google account or Google Cloud project (like
    `aws --profile`): another --configuration, --account or --project, --access-token-file (a token instead of the
    account), --impersonate-service-account (another identity) and --flags-file (options Keyfit cannot see)."""
    refuse = (f"keyfit: this folder belongs to {project.name} (gcloud configuration {conn.get('configuration')}, Google "
              f"account {conn.get('account') or 'not set'}, project {conn.get('project')}); refusing")
    bypass = "\n  Bypass once: KEYFIT_DISABLE=1 gcloud …"
    for name, value in gcloud.options(args):
        if name == "--access-token-file":
            raise Refused(f"{refuse} --access-token-file: gcloud would use that token instead of {project.name}'s "
                          f"Google account and its check.{bypass}")
        if name == "--impersonate-service-account":
            raise Refused(f"{refuse} --impersonate-service-account: gcloud would act as that service account instead of "
                          f"{project.name}'s Google account.{bypass}")
        if name == "--flags-file":
            raise Refused(f"{refuse} --flags-file: Keyfit cannot see the --account or --project it may hold.{bypass}")
        if value is None:
            continue
        if (name == "--configuration" and value != conn.get("configuration")) or \
                (name == "--account" and not same(value, conn.get("account"))) or \
                (name == "--project" and value != conn.get("project")):
            raise Refused(f"{refuse} {name} {printable(value)}.{bypass}")


def gcloud_guard(project: Project, args: list[str], registry: Registry, env: dict) -> None:
    """Inside a registered project: refuse the options that leave the project's gcloud account (gcloud_options),
    `gcloud auth login` and `auth activate-service-account` as another person or without naming the project's account
    (they would write that account into the project's configuration), and what would change a configuration a
    registered project uses away from that project's account or project: `config set|unset account|project`,
    `config set auth/…|api_endpoint_overrides/…` (another identity or server; unsetting them is the fix) and
    `config configurations delete|rename`."""
    conn = project.connections.get("gcloud")
    own = conn if isinstance(conn, dict) else None
    bypass = "\n  Bypass once: KEYFIT_DISABLE=1 gcloud …"
    if own:
        gcloud_options(project, own, args)
    words = gcloud.words(args)
    if own and own.get("account") and words[:2] in (["auth", "login"], ["auth", "activate-service-account"]):
        refuse = f"keyfit: this folder belongs to {project.name}, whose Google account is {own['account']}; refusing"
        if len(words) > 2 and not same(words[2], own["account"]):
            raise Refused(f"{refuse} `gcloud {words[0]} {words[1]} {printable(words[2])}`: gcloud would make "
                          f"configuration {own.get('configuration')} use that account.{bypass}")
        if len(words) == 2:
            raise Refused(f"{refuse} `gcloud {words[0]} {words[1]}` without {own['account']}: gcloud would make "
                          f"configuration {own.get('configuration')} use whichever account signs in.\n  Sign in as "
                          f"the project's account: {gcloud.sign_in(own['account'])}{bypass}")
    users: dict[str, list[tuple[str, dict]]] = {}  # configuration → (project, its gcloud account) of those using it
    for other in registry.projects().values():
        theirs = other.connections.get("gcloud")
        if isinstance(theirs, dict) and isinstance(theirs.get("configuration"), str):
            users.setdefault(theirs["configuration"], []).append((other.name, theirs))
    if words[:2] in (["config", "set"], ["config", "unset"]) and len(words) > 2:
        setting = words[2].removeprefix("core/")
        named = [value for name, value in gcloud.options(args) if name == "--configuration" and value]
        target = named[-1] if named else own.get("configuration") if own else gcloud.active(env)
        value = words[3] if words[1] == "set" and len(words) > 3 else None
        if words[1] == "set" and setting.startswith(("auth/", "api_endpoint_overrides/")) and users.get(target):
            raise Refused(f"keyfit: refusing `gcloud config set {printable(setting)}` for configuration {target}: "
                          f"{', '.join(name for name, _ in users[target])} uses it, and that setting changes whom gcloud "
                          f"acts as or where it sends the account's requests.{bypass}")
        for name, theirs in users.get(target, []):
            # the project's Google account, or its Google Cloud project
            wanted = theirs.get(setting) if setting in ("account", "project") else None
            if wanted and not (value and (same(value, wanted) if setting == "account" else value == wanted)):
                shown = " ".join(printable(w) for w in words[:4])
                raise Refused(f"keyfit: refusing `gcloud {shown}` for configuration {target}: {name} uses it with "
                              f"{setting} {wanted}.\n  Map {name} to another one first: keyfit add "
                              f"{'google --email' if setting == 'account' else 'gcloud --project-id'} …{bypass}")
    if words[:3] in (["config", "configurations", "delete"], ["config", "configurations", "rename"]):
        for target in words[3:] if words[2] == "delete" else words[3:4]:
            if users.get(target):
                raise Refused(f"keyfit: refusing `gcloud config configurations {words[2]} {printable(target)}`: "
                              f"{', '.join(name for name, _ in users[target])} uses that configuration.{bypass}")


def firebase_options(project: Project, conn: dict, args: list[str]) -> None:
    """Refuse what would leave the project's Google account or Firebase project (like `aws --profile`): another
    --account, another --project (an alias counts as the project .firebaserc names) and --token (a token instead of
    the account)."""
    refuse = (f"keyfit: this folder belongs to {project.name} (Google account {conn.get('account') or 'not set'}, "
              f"Firebase project {conn.get('project')}); refusing")
    bypass = "\n  Bypass once: KEYFIT_DISABLE=1 firebase …"
    given = firebase.options(args)
    configs = [value for name, value in given if name == "--config" and value]
    names = firebase.aliases(firebase.project_root(os.getcwd(), configs[-1] if configs else None) or os.getcwd())
    for name, value in given:
        if name == "--token":
            raise Refused(f"{refuse} --token: the firebase CLI would use that token instead of {project.name}'s Google "
                          f"account and its check.{bypass}")
        if value is None:
            continue
        if (name == "--account" and not same(value, conn.get("account"))) or \
                (name == "--project" and names.get(value, value) != conn.get("project")):
            raise Refused(f"{refuse} {name} {printable(value)}.{bypass}")


def firebase_guard(project: Project, args: list[str], registry: Registry, env: dict) -> None:
    """Inside a registered project: refuse the options that leave the project's Google account or Firebase project
    (firebase_options), `firebase use` of another project, a `firebase login:use` that would bind a registered
    project's folder to another account (outside every Firebase folder it would change the default login instead),
    and a `firebase login --reauth` that would replace the default login (another account) with the project's."""
    conn = project.connections.get("firebase")
    own = conn if isinstance(conn, dict) else None
    bypass = "\n  Bypass once: KEYFIT_DISABLE=1 firebase …"
    if own:
        firebase_options(project, own, args)
    words = firebase.words(args)
    configs = [value for name, value in firebase.options(args) if name == "--config" and value]
    root = firebase.project_root(os.getcwd(), configs[-1] if configs else None)
    if words[:1] == ["login:use"] and len(words) > 1:
        if root is None and own:
            raise Refused(f"keyfit: this folder belongs to {project.name} and has no firebase.json (nor any folder "
                          f"above it): `firebase login:use` would change the firebase CLI's default login, which every "
                          f"folder without its own account uses.\n  Run it in the project's Firebase folder (where "
                          f"firebase.json is).{bypass}")
        owner = resolve(root, registry).project if root else None
        for who in {p.name: p for p in (project, owner) if p is not None}.values():
            theirs = who.connections.get("firebase")
            if isinstance(theirs, dict) and theirs.get("account") and not same(words[1], theirs["account"]):
                raise Refused(f"keyfit: refusing `firebase login:use {printable(words[1])}` for {tilde(root)}: "
                              f"{who.name} uses Google account {theirs['account']} there.{bypass}")
    if own and words[:1] == ["use"] and len(words) > 1:
        names = firebase.aliases(root or os.getcwd())
        if names.get(words[1], words[1]) != own.get("project"):
            raise Refused(f"keyfit: this folder belongs to {project.name}, whose Firebase project is "
                          f"{own.get('project')}; refusing `firebase use {printable(words[1])}`.{bypass}")
    before = args[:args.index("--")] if "--" in args else args
    if own and own.get("account") and words[:1] == ["login"] and "--reauth" in before:
        default = firebase.default_login(firebase.read_store(env) or {})
        if default and not same(default, own["account"]):
            raise Refused(f"keyfit: refusing `firebase login --reauth`: it would replace the firebase CLI's default "
                          f"login ({printable(default)}), which folders without their own account use, with "
                          f"{own['account']}.\n  Renew {own['account']}'s sign-in instead: firebase login:add   "
                          f"(choose {own['account']}){bypass}")


def firebase_args(project: Project, args: list[str], env: dict, cwd: Path) -> list[str]:
    """With --config the CLI's folder is that file's: it is checked like the folder you are in. Outside every Firebase
    folder the CLI uses its default login: when that is another account, the project's is named with --account (only
    before a command: firebase 15.26 fails on --account with none)."""
    conn = project.connections["firebase"]
    given = firebase.options(args)
    configs = [value for name, value in given if name == "--config" and value]
    if configs:
        check = ADAPTERS["firebase"].at(conn, env, str(cwd), configs[-1])
        if check.failed:
            raise Refused("\n".join([f"keyfit: firebase not started, {project.name} needs attention", *check.lines(),
                                     "  (bypass once: KEYFIT_DISABLE=1 firebase …)"]))
    store = firebase.read_store(env) or {}
    used, _ = firebase.bound(store, firebase.project_root(cwd, configs[-1] if configs else None))
    account = firebase.account_of(store, str(conn.get("account")))
    before = args[:args.index("--")] if "--" in args else args
    command = any(not a.startswith("-") and not (i and before[i - 1] in firebase.VALUED) for i, a in enumerate(before))
    if command and account and not same(used, conn.get("account")) and not any(n == "--account" for n, _ in given):
        return ["--account", account["user"]["email"], *args]
    return args


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


def start(real: str, tool: str, args: list[str], env: dict, run_env: dict, why: str | None = None) -> int:
    """Every start of the real tool goes through here. Under KEYFIT_DRY_RUN=1 a start Keyfit decided (`why`: the
    checks passed, or the command runs unchecked in a project) is only printed: why, the command, and the names of
    the variables Keyfit changes, never their values. Starts without a reason always run, as the docs say: with
    KEYFIT_DISABLE, in a folder outside every project, and sign-in commands."""
    if why and env.get("KEYFIT_DRY_RUN") == "1":
        changed = sorted(k for k in set(env) | set(run_env) if env.get(k) != run_env.get(k))
        _say([f"keyfit: {why}", f"  would run: {real} {' '.join(args)}",
              f"  environment changes (names only): {', '.join(changed) or 'none'}"])
        return 0
    os.execve(real, [tool, *args], run_env)
    return 0  # not reached: execve replaces this process


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


def binding_command(check, project: Project, registry: Registry, env: dict,
                    here: str | None = None) -> tuple[str, str] | None:
    """(profile, folder) when the check's fix is exactly `wrangler auth activate NAME FOLDER` for the project's own
    wrangler profile and a folder Keyfit may bind: one of this project's (never another project's, nor a folder outside
    every project), not the home folder or a folder above it, and one whose binding fixes the folder you are in
    (*here*, else the current one)."""
    import shlex

    conn = project.connections.get("cloudflare")
    if check.kind != "cloudflare" or not isinstance(conn, dict):
        return None
    try:
        words = shlex.split(check.fix)
    except ValueError:
        return None
    if words[:3] != ["wrangler", "auth", "activate"] or len(words) != 5 or words[3] != conn.get("profile"):
        return None
    profile, folder = words[3], os.path.expanduser(words[4])
    if profile == cloudflare.DEFAULT or not os.path.isabs(folder) or not bindable(profile, folder, env, here):
        return None
    there = resolve(folder, registry).project
    return (profile, folder) if there is not None and there.name == project.name else None


def bindable(profile: str, folder: str, env: dict, here: str | None = None) -> bool:
    """True when *folder* is a folder, not the home folder or one above it (everything under it would follow), and
    binding it makes wrangler use *profile* in the folder you are in (*here*, else the current one)."""
    home = canonical(env.get("HOME") or Path.home())
    target = canonical(folder)
    if not target.is_dir() or target == home or target in home.parents:
        return False
    return cloudflare.bound(here or os.getcwd(), {**cloudflare.bindings(env), folder: profile})[0] == profile


def superset_folder(profile: str, folder: str, project: Project, registry: Registry, env: dict) -> str | None:
    """The folder that holds *folder* when it is a Superset worktree and that folder may be bound to the project's
    profile instead: nothing in it belongs to another project (each worktree there resolves to this project or to
    none, and no other project's root lies inside), and it does not belong to another project itself."""
    parent = cloudflare.superset_parent(folder, env)
    if parent is None or not bindable(profile, parent, env):
        return None
    inside = canonical(parent)
    for other in registry.projects().values():
        if other.name != project.name and any(root == inside or inside in root.parents
                                              for root in other.canonical_roots()):
            return None
    try:
        entries = [entry.path for entry in os.scandir(parent) if entry.is_dir()]
    except OSError:
        return None
    for path in (parent, *entries):
        try:
            there = resolve(path, registry).project
        except GitError:  # git cannot tell where that worktree belongs: bind only the folder you are in
            return None
        if there is not None and there.name != project.name:
            return None
    return parent


def _confirm(question: str) -> bool:
    sys.stderr.write(f"  {question} [Y/n] ")
    sys.stderr.flush()
    answer = sys.stdin.readline()
    return bool(answer) and answer.strip().lower() in ("", "y", "yes")  # end of input counts as no


def offer_fixes(failed: list, project: Project, env: dict, registry: Registry, tried: set) -> str | bool | None:
    """In a terminal, when every failed check is fixed by a sign-in or by binding a folder of the project to its wrangler
    profile, ask (`Sign in now?`, then `Bind this folder now?`) and run them. What ran ("signed in", "bound …") when
    they all finished; False: one failed; None: nothing was offered or an answer was no. Never asked without a
    terminal (agents, scripts, CI) or inside a session Keyfit started (KEYFIT_PROJECT set: agents run commands in a
    terminal too), so nothing waits for an answer that cannot come, and never for a fix in *tried* (the fixes offered
    before, which are added to it): a fix that ran and did not help is only printed."""
    if not failed or env.get("KEYFIT_DRY_RUN") == "1" or env.get("CI") or env.get("KEYFIT_PROJECT") or \
            any(c.fix in tried for c in failed):
        return None
    if not (sys.stdin.isatty() and sys.stderr.isatty()):
        return None
    sign_ins, binds = [], []
    for check in failed:
        command = sign_in_command(check.fix)
        bind = None if command else binding_command(check, project, registry, env)
        if command is None and bind is None:
            return None
        if command:
            sign_ins.append(command)
        elif bind not in binds:
            binds.append(bind)
    tried.update(check.fix for check in failed)
    done = []
    if sign_ins:
        binding = all(tuple(words[:2]) == ("firebase", "login:use") for _, words in sign_ins)
        if not _confirm("Bind this folder now?" if binding else "Sign in now?"):
            return None
        import subprocess

        for assignments, words in {(tuple(a.items()), tuple(w)): None for a, w in sign_ins}:
            name = words[0]
            real = cloudflare.wrangler_bin(env, os.getcwd()) if name == "wrangler" else \
                firebase.firebase_bin(env, os.getcwd()) if name == "firebase" else \
                find_real(name, env, skip_wrappers=True)
            if real is None:
                _say([f"keyfit: {name} is not installed. Install it: {INSTALL[name]}"])
                return False
            login_env = login_environment(name, project, env)  # as when you type the sign-in
            login_env.update(assignments)
            if name == "gemini":
                words = (name, *gemini_args(project, list(words[1:]), login_env, Path.cwd(), registry.mcp_owners()))
            if subprocess.run(list(words), executable=real, env=login_env).returncode != 0:
                _say([f"keyfit: {name} {'login:use' if binding else 'sign-in'} did not finish"])
                return False
        done.append("bound this folder" if binding else "signed in")
    for profile, folder in binds:
        wider = superset_folder(profile, folder, project, registry, env)
        if not _confirm(f"Bind {tilde(wider)} now, for every Superset worktree of {project.name}?" if wider
                        else "Bind this folder now?"):
            return None
        real = cloudflare.wrangler_bin(env, os.getcwd())  # the project's own wrangler, as npx would run it
        if real is None:
            _say([f"keyfit: wrangler is not installed. Install it: {INSTALL['wrangler']}"])
            return False
        reason = ADAPTERS["cloudflare"].activate(real, profile, wider or folder, env)
        if reason:
            _say([f"keyfit: wrangler auth activate did not finish: {reason}"])
            return False
        done.append(f"bound {tilde(wider or folder)} to wrangler profile {profile}")
    return ", ".join(done)


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
        return start(real, tool, args, env, env)
    # counted on every path, not only where checks run: a wrapper later on PATH that calls the tool by name
    # comes back through here also in folders that are not registered
    env["KEYFIT_SHIM_DEPTH"] = str(depth + 1)

    try:
        sub = subcommand("opencode", args, OPENCODE_SUBCOMMANDS) if tool == "opencode" else None
        if sub in OPENCODE_SHARED:
            return start(real, tool, opencode_private(args, env), env, env,
                         f"opencode {sub} is OpenCode's own command: it runs without a project's accounts or checks")
        registry = Registry.load()
        shared = tool == "aws" and requested_profiles(args)
        if shared and set(shared) <= registry.shared_aws_profiles():
            # profiles of machine-wide tools rather than a project ([settings]): they run unchanged anywhere
            return start(real, tool, args, env, env, f"AWS profile {', '.join(dict.fromkeys(shared))} is shared "
                         f"([settings] shared_aws_profiles): aws runs without a project's accounts or checks")
        pinned = env.get("KEYFIT_PROJECT") if tool in PINNED else None
        project = registry.get(pinned) if pinned else resolve(".", registry).project
        if pinned and project is None:  # the session's environment is that project's: never run it unchecked
            raise Refused(f"keyfit: this session belongs to project {printable(pinned)}, which is not in the registry "
                          f"(renamed or removed?), so {tool} was not started.\n  Start the session again, or bypass "
                          f"once: KEYFIT_DISABLE=1 {tool} …")
        folders = {"opencode": opencode_folder, "copilot": copilot_folder, "codex": codex_folder}
        if tool in folders:
            folder_check(tool, folders[tool](args), registry, project)
        if tool == "claude":  # before the checks, and inside a session outside every project too
            claude_background(args, env, project)
        if project is None:  # not a registered project: exactly as before Keyfit
            if tool == "codex":
                args = codex_private(args, env)
            if tool == "wrangler" and is_login_command(tool, args):  # except binding a project's folder elsewhere
                wrangler_login_check(None, args, registry, env)
            return start(real, tool, opencode_private(args, env) if tool == "opencode" else args, env, env)
        if tool == "gcloud":  # before sign-in commands too: they write into configurations
            gcloud_guard(project, args, registry, env)
        if tool == "firebase":  # before sign-in commands too: login:use and use bind folders
            firebase_guard(project, args, registry, env)

        relevant = RELEVANT.get(tool)
        if is_login_command(tool, args):
            # run sign-in/setup with the project's environment (so `aws sso login` / `claude auth login`
            # target the project's profile / account) but without checks
            if tool == "aws":  # not another profile's sign-in, settings or keys
                aws_profiles(project, args)
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
            if tool == "gemini":  # a session, until it quits: without other projects' MCP servers
                args = gemini_args(project, args, login_env, Path.cwd(), registry.mcp_owners())
            return start(real, tool, args, env, login_env)
        if relevant and not any(kind in project.connections for kind in relevant):
            if tool == "gh" and positionals(args)[:2] == ["auth", "status"] and \
                    not gh_switch(gh_options(args), "--show-token"):  # it lists the logins to map: as a sign-in
                return start(real, tool, args, env, login_environment(tool, project, env))
            if tool in OPTIONAL:  # no account of its kind: it runs as before Keyfit, minus other projects' tokens
                return start(real, tool, args, env, environment.build(project, env, kinds=relevant),
                             f"project {project.name} has no {relevant[0]} account mapped: {tool} runs without "
                             f"checks, as before Keyfit")
            raise Refused(f"keyfit: project {project.name} has no {relevant[0]} account mapped, so {tool} was not "
                          f"started.\n  Map one: {MAP[relevant[0]]}   (bypass once: KEYFIT_DISABLE=1 {tool} …)")
        run_env = environment.build(project, env, kinds=relevant)  # aws/gh/vercel/…/convex: only their own account
        checks = environment.verify(project, run_env, remote=True, kinds=relevant, use_cache=True)
        failed = [c for c in checks if c.failed]
        if failed:
            _say([f"keyfit: {tool} not started, {project.name} needs attention"])
            for check in failed:
                _say(check.lines())
            tried: set[str] = set()
            done = offer_fixes(failed, project, env, registry, tried)
            while done:  # a binding lets the online check run, which may then ask for a sign-in
                run_env = environment.build(project, env, kinds=relevant)
                checks = environment.verify(project, run_env, remote=True, kinds=relevant, use_cache=True)
                failed = [c for c in checks if c.failed]
                if not failed:
                    _say([f"keyfit: {done}, starting {tool}"])
                    break
                _say([f"keyfit: {tool} not started, {project.name} still needs attention"])
                for check in failed:
                    _say(check.lines())
                done = offer_fixes(failed, project, env, registry, tried)
            if failed:
                _say([f"  (bypass once: KEYFIT_DISABLE=1 {tool} …)"])
                return 1
        # agents: every warning; every tool: a success trusted from the cache because the provider did not answer
        for check in (c for c in checks if c.status == "warn" and (tool in AGENTS or c.transient)):
            _say([f"keyfit: {check.lines()[0].strip()}"])
        seen = [var for check in checks if check.kind == tool for var in check.scrub]
        args = adjust_args(tool, project, args, run_env, Path.cwd(), registry.mcp_owners(), seen)
    except Refused as refusal:
        print(refusal, file=sys.stderr)
        return 1
    except GitError as err:  # inside a repository git cannot read, the folder may be a project's: nothing starts
        print(f"keyfit: {tool} not started: {printable(err)}, so Keyfit cannot tell which project that folder "
              f"belongs to.\n  Fix git (keyfit doctor shows how), or bypass once: KEYFIT_DISABLE=1 {tool} …",
              file=sys.stderr)
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
    return start(real, tool, args, env, run_env, f"project {project.name}, checks ok")
