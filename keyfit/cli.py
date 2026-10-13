"""keyfit — setup and diagnosis commands. Day to day, the shims do the work."""

from __future__ import annotations

import argparse
import fnmatch
import os
import re
import sys
from pathlib import Path

from . import __version__, environment, mcp, secrets
from .adapters import ADAPTERS
from .adapters import claude as claude_adapter
from .adapters import linear as linear_adapter
from .adapters import vercel as vercel_adapter
from .adapters.aws import ACCOUNT_RE, parse_envrc, read_profile
from .checks import Check, mask_account
from .registry import Project, Registry, RegistryError, canonical, config_dir, default_path, tilde  # noqa: F401
from .resolve import git_main_checkout, resolve
from .shim import INSTALL, MAP, RELEVANT

TOOL_OF = {kinds[0]: tool for tool, kinds in RELEVANT.items() if kinds}  # github → gh


def _project_from_args(reg: Registry, args) -> Project | None:
    if getattr(args, "project", None):
        project = reg.get(args.project)
        if project is None:
            print(f"no project named '{args.project}' (keyfit projects lists them)", file=sys.stderr)
        return project
    r = resolve(".", reg)
    if not r.registered:
        print(f"this folder is not part of a registered project ({r.how}).\n"
              "  Run this inside the project's repository, name it with --project NAME, or register it: keyfit init",
              file=sys.stderr)
    return r.project


def _import_envrc(reg: Registry, name: str, root: Path) -> list[str]:
    """Import `use aws PROFILE ACCOUNT` and `export LINEAR_API_KEY=$VAR` lines from the repository's .envrc
    (direnv), so nothing has to be typed twice. No .envrc: nothing to import, nothing printed."""
    imported = []
    try:
        text = (root / ".envrc").read_text()
    except (OSError, UnicodeDecodeError):
        return imported
    current = reg.data["projects"][name]
    aws = parse_envrc(text)
    if aws and "aws" not in current:
        reg.set_connection(name, "aws", aws)
        imported.append(f"aws {aws['profile']} → {mask_account(aws['account'])} (from .envrc)")
    ref = linear_adapter.parse_envrc(text, os.environ)
    if ref and "linear" not in current:
        key = secrets.resolve(ref, os.environ)
        workspace, reason = ADAPTERS["linear"].workspace_of(key) if key else (None, f"{secrets.describe(ref)} is not set")
        if workspace:
            reg.set_connection(name, "linear", {"workspace": workspace, "key": ref})
            imported.append(f"linear {workspace} via {secrets.describe(ref)} (from .envrc)")
        else:
            imported.append(f"linear: skipped .envrc key ({reason})")
    return imported


def _not_active() -> str:
    """Why the shims would not run in this terminal yet, with the fix; "" when they are on PATH."""
    from .realbin import MARKER, shim_dir

    folder = canonical(shim_dir())
    if not (folder / MARKER).is_file():
        return "Keyfit is not set up yet, so claude, codex, aws, gh and vercel still run without checks.\n" \
               "  Set it up: keyfit setup --rc, then open a new terminal"
    if folder not in [canonical(e) for e in os.environ.get("PATH", "").split(os.pathsep) if e]:
        return f"{tilde(folder)} is not on PATH in this terminal, so tools here still run without checks.\n" \
               "  Open a new terminal (or, if you never ran it: keyfit setup --rc)"
    return ""


def cmd_init(args) -> int:
    reg = Registry.load(args.registry)
    start = canonical(args.root or ".")
    if not start.is_dir():
        print(f"no folder {args.root}", file=sys.stderr)
        return 1
    git = git_main_checkout(start)
    root = git or start
    home = Path.home().resolve()
    if root == home or root in home.parents:
        print(f"Not registering {'your home folder' if root == home else root}: every folder under it "
              f"{'' if root == home else '(your home folder too) '}would belong to one project.\n"
              "  Go to a project's repository first (cd ~/code/acme-api), or pass --root PATH", file=sys.stderr)
        return 1
    name = args.name or root.name
    existing = reg.project_for_root(root)
    if existing is not None and existing.name != name:
        print(f"{tilde(root)} is already registered as project '{existing.name}'", file=sys.stderr)
        return 1
    reg.add_project(name, root)
    imported = _import_envrc(reg, name, root)
    reg.save()
    print(f"Registered project '{name}' → {tilde(root)}")
    for line in imported:
        print(f"  imported {line}")
    if git is None:
        print(f"  {tilde(root)} is not a git repository: every folder under it belongs to {name}")
    worktrees = "Worktrees of this repository resolve to it automatically." if git else ""
    note = _not_active()
    if args.yes or not sys.stdin.isatty():
        print(f"{worktrees} Check it with: keyfit status".lstrip())
        if note:
            print(note)
        return 0
    if worktrees:
        print(worktrees)
    code = _guided_init(args, name, root)
    if note:
        print(f"\n{note}")
    return code


# Guided `keyfit init` (a terminal on stdin): offer what this machine already has, one account at a time.
# Every answer goes through the matching `keyfit add …` command, so nothing is recorded any other way.

def _ask(prompt: str) -> str:
    """One answer typed at the terminal; Ctrl-D counts as skipping. Tests replace this."""
    try:
        return input(prompt).strip()
    except EOFError:
        print()
        return ""


def _yes(question: str) -> bool:
    return _ask(f"{question} [y/N] ").lower() in ("y", "yes")


def _choose(question: str, options: list[str]) -> str | None:
    """Pick one option by number (or name); Enter skips."""
    for i, option in enumerate(options, 1):
        print(f"  {i}. {option}")
    numbers = "1" if len(options) == 1 else f"1-{len(options)}"
    while True:
        answer = _ask(f"{question} [{numbers}, Enter to skip]: ")
        if not answer:
            return None
        if answer in options:
            return answer
        if answer.isdigit() and 1 <= int(answer) <= len(options):
            return options[int(answer) - 1]
        print(f"  type a number from {numbers}, or press Enter to skip")


def _offer_aws(name: str, base: dict) -> None:
    from .adapters.aws import config_path, profile_names
    from .realbin import find_real

    where = tilde(config_path(os.environ))
    profiles = profile_names(os.environ)
    if not profiles and find_real("aws", dict(os.environ), skip_wrappers=True) is None:
        print("AWS: aws not found on PATH — skipped")
        return
    if not profiles:
        print(f"AWS: no named profiles in {where} — create one (aws configure sso --profile <name>), "
              "then: keyfit add aws --profile <name>")
        return
    print(f"AWS profiles in {where}:")
    profile = _choose(f"AWS profile for {name}", profiles)
    if profile:
        cmd_add_aws(argparse.Namespace(**base, profile=profile, account=None, region=None, guided=True))


def _offer_github(name: str, base: dict) -> None:
    from .adapters.github import logins

    found, reason = logins(dict(os.environ))
    if reason:
        print(f"GitHub: {reason} — skipped")
        return
    if not found:
        print("GitHub: gh is not signed in to github.com — sign in (gh auth login --hostname github.com --web), "
              "then: keyfit add github --login <login>")
        return
    print("GitHub accounts gh is signed in to:")
    login = _choose(f"GitHub account for {name}", found)
    if login:
        cmd_add_github(argparse.Namespace(**base, login=login, name=None, email=None, ssh_host=None))


def _offer_claude(name: str, base: dict) -> None:
    folder = claude_adapter.default_dir(os.environ)
    status, reason = _claude_status(folder)
    if status is None:
        print(f"Claude: {reason} — skipped")
        return
    if not status.get("loggedIn") or status.get("authMethod") != "claude.ai":
        print(f"Claude: no claude.ai login in {tilde(folder)} — sign in ({claude_adapter.login_command(folder, os.environ)}), "
              "then: keyfit add claude")
        return
    plan = status.get("subscriptionType")
    print(f"Claude: signed in to {status.get('orgName') or 'a personal account'}" + (f" ({plan})" if plan else "")
          + f" in {tilde(folder)}")
    if _yes(f"Use this Claude login for {name}?"):
        cmd_add_claude(argparse.Namespace(**base, name=None, config_dir=None, new=False, pin_email=False))


def _offer_codex(name: str, base: dict) -> None:
    from .adapters.codex import home

    folder = home({}, dict(os.environ))
    login, problem = _chatgpt_login(folder)
    if login is None:
        print(f"Codex: {problem} — sign in (codex login), then: keyfit add codex")
        return
    print(f"Codex: signed in with ChatGPT {login['plan'] or ''}".rstrip() + f" in {tilde(folder)}")
    if _yes(f"Use this Codex login for {name}?"):
        cmd_add_codex(argparse.Namespace(**base, name=None, codex_home=None, pin_account=False))


def _offer_vercel(name: str, base: dict) -> None:
    from .realbin import find_real

    env = dict(os.environ)
    if find_real("vercel", env, skip_wrappers=True) is None:
        print("Vercel: vercel not found on PATH — skipped")
        return
    folder = canonical(vercel_adapter.cli_default(env))
    record, problem = _vercel_identity(folder, None)
    if record is None:
        print(f"Vercel: {problem} — sign in (vercel login), then: keyfit add vercel")
        return
    team = f", team {record['team']}" if record.get("team") else ""
    print(f"Vercel: signed in as {record['user']}{team} in {tilde(folder)}")
    if _yes(f"Use this Vercel login for {name}?"):
        cmd_add_vercel(argparse.Namespace(**base, name=None, config_dir=None, new=False, team=None))


def _offer_linear(name: str, base: dict) -> None:
    if not secrets.can_store():
        print("Linear: later, with a key in an environment variable: keyfit add linear --key env:<VARIABLE>")
        return
    print("Linear: a personal API key (Linear → Settings → Security & access) is checked with Linear, "
          "then kept in the macOS Keychain.")
    if _yes(f"Add a Linear API key for {name}?"):
        cmd_add_linear(argparse.Namespace(**base, key=None, workspace=None, readonly=False))


GUIDED = (("aws", "AWS", _offer_aws), ("github", "GitHub", _offer_github), ("claude", "Claude", _offer_claude),
          ("codex", "Codex", _offer_codex), ("vercel", "Vercel", _offer_vercel), ("linear", "Linear", _offer_linear))


def _guided_init(args, name: str, root: Path) -> int:
    print("\nNow its accounts. Press Enter to skip any of them.")
    base = {"registry": args.registry, "project": name}
    try:
        for kind, title, offer in GUIDED:
            print()
            conn = Registry.load(args.registry).get(name).connections.get(kind)
            if conn is not None:  # imported from .envrc or set earlier: left as it is
                print(f"{title}: already set — {environment.label(kind, conn)}")
                continue
            offer(name, base)
    except KeyboardInterrupt:
        print(f"\nStopped. '{name}' is registered; add the rest any time with: keyfit add …")
        return 130
    print()
    return cmd_status(argparse.Namespace(registry=args.registry, path=str(root), verbose=False, offline=False))


def cmd_which(args) -> int:
    reg = Registry.load(args.registry)
    r = resolve(args.path or ".", reg)
    if r.registered:
        print(f"{r.project.name}  ({r.how})")
        if args.verbose:
            for kind, value in r.project.connections.items():
                print(f"  {kind:<10} {environment.label(kind, value)}")
            if r.project.mcp_servers:
                print(f"  {'mcp':<10} owns {', '.join(r.project.mcp_servers)}")
        return 0
    print(f"not registered  ({r.how})")
    return 1


def cmd_projects(args) -> int:
    reg = Registry.load(args.registry)
    projects = reg.projects()
    if not projects:
        print(f"no projects registered yet ({tilde(reg.path)}) — register one inside its repository: keyfit init")
        return 0
    for p in projects.values():
        kinds = ", ".join(p.connections) or "no connections yet"
        owned = f"  mcp: {', '.join(p.mcp_servers)}" if p.mcp_servers else ""
        print(f"{p.name:<16} {', '.join(p.roots)}  [{kinds}]{owned}")
    return 0


def cmd_status(args) -> int:
    reg = Registry.load(args.registry)
    r = resolve(args.path or ".", reg)
    if not r.registered:
        print(f"not registered  ({r.how})\n  Tools run here unchanged. To register a repository, run inside it: "
              "keyfit init")
        return 1
    print(r.project.name + (f"  ({r.how})" if args.verbose else ""))
    if r.project.legacy:
        print(f"  note: {', '.join(r.project.legacy)} no longer supported (ignored) — "
              f"can be removed from {tilde(reg.path)}")
    checks = []
    if not r.project.connections:
        print("  no accounts yet — keyfit init offers the ones on this Mac, or map one: keyfit add aws|github|"
              "claude|codex|vercel|linear …")
    else:
        env = environment.build(r.project)
        checks = environment.verify(r.project, env, remote=not args.offline)  # fresh; refreshes the cache
        for check in checks:
            for line in check.lines():
                print(line)
    waiting = [kind for kind in MAP if kind not in r.project.connections]
    if waiting:  # inside a project, `aws`, `gh` and `vercel` start only once it has their account
        tools = " and ".join(", ".join(TOOL_OF[k] for k in waiting).rsplit(", ", 1))
        print(f"  {tools} won't start here until mapped: keyfit add {'|'.join(waiting)} …")
    return 1 if any(c.failed for c in checks) else 0


def cmd_add_aws(args) -> int:
    from .adapters.aws import config_path, fix_for, profile_names

    reg = Registry.load(args.registry)
    project = _project_from_args(reg, args)
    if project is None:
        return 1
    found = f"named profiles in {tilde(config_path(os.environ))}: {', '.join(profile_names(os.environ)) or 'none'}"
    if args.profile == "default":
        print(f"use a named profile, never `default` ({found})", file=sys.stderr)
        return 1
    profile = read_profile(args.profile, os.environ)
    if profile.kind == "unreadable":
        print("your AWS config or credentials file does not parse — find it with: "
              f"aws configure list --profile {args.profile}", file=sys.stderr)
        return 1
    if profile.kind == "missing":
        print(f"no AWS profile named '{args.profile}' ({found})\n"
              f"  Create it first: aws configure sso --profile {args.profile}", file=sys.stderr)
        return 1
    conn = {"profile": args.profile}
    if os.environ.get("AWS_CONFIG_FILE"):  # the shims scrub the variable: the project keeps the file the profile is in
        conn["config_file"] = tilde(os.environ["AWS_CONFIG_FILE"])
    asked_aws = False
    if args.account:
        if not ACCOUNT_RE.match(args.account):
            print("--account must be a 12-digit AWS account ID", file=sys.stderr)
            return 1
        conn["account"] = args.account
    elif profile.account_hint:
        conn["account"] = profile.account_hint  # sso_account_id or the ARN in the profile — no network needed
        print(f"Account taken from the profile's config ({profile.kind} profile)")
    else:
        asked_aws = True
        print(f"Asking AWS which account profile '{args.profile}' belongs to …", flush=True)
        probe = dict(os.environ)
        ADAPTERS["aws"].inject(dict(conn, account="000000000000"), probe)
        account, reason, _ = ADAPTERS["aws"].identify(args.profile, probe)
        if account is None:
            print(f"could not read the account: {reason}\n  → {fix_for(profile, reason)}", file=sys.stderr)
            return 1
        conn["account"] = account
    if args.region:
        conn["region"] = args.region
    reg.set_connection(project.name, "aws", conn)
    reg.save()
    print(f"{project.name}: aws {args.profile} → {mask_account(conn['account'])}")
    if getattr(args, "guided", False):
        return 0  # `keyfit init` checks every connection once at the end
    return cmd_status(argparse.Namespace(registry=args.registry, path=project.roots[0], verbose=False,
                                         offline=asked_aws))


def _read_secret(prompt: str) -> str | None:
    """Hidden prompt on a terminal, else one line from stdin (scripts). Never echoed, never in argv."""
    import getpass
    import warnings

    try:
        if sys.stdin.isatty():
            with warnings.catch_warnings():
                # getpass falls back to reading with echo when it cannot control the terminal: stop instead
                warnings.simplefilter("error", getpass.GetPassWarning)
                return getpass.getpass(prompt)
        return sys.stdin.readline().rstrip("\r\n")
    except getpass.GetPassWarning:
        print("\nthis terminal cannot hide what you type, so nothing was read; pipe the key on stdin instead",
              file=sys.stderr)
        return None
    except (EOFError, KeyboardInterrupt):
        print(file=sys.stderr)  # end the prompt line; the caller says nothing was stored
        return None


def cmd_add_linear(args) -> int:
    reg = Registry.load(args.registry)
    project = _project_from_args(reg, args)
    if project is None:
        return 1
    if args.key is not None:
        if secrets.looks_like_key(args.key):
            print("--key takes where the key lives (env:VAR or keychain:service/account), not the key itself: "
                  "run `keyfit add linear` without --key to paste it at a hidden prompt "
                  "(the key may now be in your shell history)", file=sys.stderr)
            return 1
        ref = secrets.normalise(args.key)
        key = secrets.resolve(ref, os.environ)
        if not key:
            print(f"{secrets.describe(ref)} is empty or missing in this shell.\n  Set it first, or paste the key "
                  "instead (stored in the Keychain): keyfit add linear", file=sys.stderr)
            return 1
    else:  # paste the key: verified with Linear first, then stored in the Keychain
        if not secrets.can_store():
            print(secrets.NOT_MACOS, file=sys.stderr)
            return 1
        ref, key = None, (_read_secret("Linear API key (input hidden): ") or "").strip()
        if not key:
            print("no key given — paste it at the prompt, or pipe it on stdin", file=sys.stderr)
            return 1
        if not linear_adapter.KEY_SHAPE.fullmatch(key):
            print("that is not a Linear API key (lin_api_…) — create one in Linear → Settings → "
                  "Security & access → Personal API keys", file=sys.stderr)
            return 1
    workspace, reason = ADAPTERS["linear"].workspace_of(key)
    if workspace is None:
        fix = ("check the network and retry" if "did not answer" in reason else
               "create a key in Linear → Settings → Security & access → Personal API keys, then: keyfit add linear")
        print(f"could not read the workspace: {reason}\n  → {fix}", file=sys.stderr)
        return 1
    if args.workspace and args.workspace != workspace:
        print(f"that key belongs to workspace '{workspace}', not '{args.workspace}'", file=sys.stderr)
        return 1
    if ref is None:
        account = f"linear-{workspace}"
        reason = secrets.store(secrets.SERVICE, account, key)
        if reason:
            print(f"could not store the key in the Keychain: {reason}", file=sys.stderr)
            return 1
        reg.add_stored_secret(account)
        ref = f"keychain:{secrets.SERVICE}/{account}"
    conn = {"workspace": workspace, "key": ref}
    if args.readonly:
        conn["readonly"] = True
    reg.set_connection(project.name, "linear", conn)
    reg.save()
    print(f"{project.name}: linear {workspace} via {secrets.describe(ref)}")
    return 0


def cmd_secret_set(args) -> int:
    """Store a value typed at a hidden prompt as Keychain item keyfit/NAME and print its reference."""
    if not secrets.can_store():
        print(secrets.NOT_MACOS, file=sys.stderr)
        return 1
    if secrets.looks_like_key(args.name):
        print("NAME is a label such as linear-acme; the key itself goes in at the hidden prompt "
              "(it may now be in your shell history)", file=sys.stderr)
        return 1
    if not secrets.NAME.fullmatch(args.name):
        print("a name is letters, digits, '.', '_' and '-' (at most 64)", file=sys.stderr)
        return 1
    value = _read_secret(f"{args.name} (input hidden): ")
    if not value:
        print("nothing given — nothing stored", file=sys.stderr)
        return 1
    reason = secrets.store(secrets.SERVICE, args.name, value)
    if reason:
        print(f"could not store {args.name} in the Keychain: {reason}", file=sys.stderr)
        return 1
    reg = Registry.load(args.registry)
    reg.add_stored_secret(args.name)
    reg.save()
    print(f"Stored {args.name} in the Keychain. Refer to it as:\n  keychain:{secrets.SERVICE}/{args.name}")
    return 0


def cmd_secret_list(args) -> int:
    """Names of the Keychain items Keyfit stored — never their values."""
    names = Registry.load(args.registry).stored_secrets()
    if not names:
        print("no keys stored by Keyfit yet — store one with: keyfit secret set NAME")
        return 0
    for name in names:
        ref = f"keychain:{secrets.SERVICE}/{name}"
        print(f"{name:<24} {ref}" + ("" if secrets.exists(ref, {}) else "   (missing from the Keychain)"))
    return 0


def cmd_add_mcp(args) -> int:
    """Mark MCP servers (name patterns) as belonging to this project: other projects stop seeing them."""
    reg = Registry.load(args.registry)
    project = _project_from_args(reg, args)
    if project is None:
        return 1
    owned = reg.add_mcp_servers(project.name, args.servers)
    reg.save()
    found = {**{n: "claude" for n in mcp.claude_user_servers(dict(os.environ))},
             **{n: "codex" for n in mcp.codex_servers(dict(os.environ))}}
    print(f"{project.name}: owns MCP servers {', '.join(owned)} — hidden in every other project")
    for pattern in args.servers:
        hits = sorted(n for n in found if fnmatch.fnmatchcase(n, pattern))
        print(f"  {pattern}: " + (", ".join(hits) if hits else "matches no server configured right now"))
    return 0


def _claude_status(folder: Path) -> tuple[dict | None, str]:
    """`claude auth status` for the login in this config directory, every other identity scrubbed."""
    env = dict(os.environ)
    ADAPTERS["claude"].inject({"config_dir": str(folder)}, env)
    return claude_adapter.auth_status(env)


def cmd_add_claude(args) -> int:
    """Record which Claude login a project uses. Default: the login already in ~/.claude."""
    reg = Registry.load(args.registry)
    project = _project_from_args(reg, args)
    if project is None:
        return 1
    if args.new:
        if not args.name:
            print("--new needs --name (the account's short name)", file=sys.stderr)
            return 1
        folder = Path(args.config_dir).expanduser() if args.config_dir else config_dir() / "claude" / args.name
        made = claude_adapter.seed(folder, dict(os.environ), [Path(r).expanduser() for r in project.roots])
        print(f"Created {tilde(folder)}" + (f" ({', '.join(made)})" if made else ""))
        reg.set_account("claude", args.name, {"config_dir": tilde(folder)})
        reg.set_connection(project.name, "claude", args.name)
        reg.save()
        print(f"Sign in once in that folder, then record the identity:\n"
              f"  {claude_adapter.login_command(folder, os.environ)}\n"
              f"  keyfit add claude --name {args.name} --config-dir {tilde(folder)}")
        return 0

    folder = Path(args.config_dir).expanduser() if args.config_dir else claude_adapter.default_dir(os.environ)
    status, reason = _claude_status(folder)
    if status is None:
        install = f"\n  Install it: {INSTALL['claude']}" if "not found" in reason else ""
        print(f"could not ask Claude Code: {reason}{install}", file=sys.stderr)
        return 1
    if not status.get("loggedIn") or status.get("authMethod") != "claude.ai":
        print(f"no claude.ai login in {tilde(folder)} (auth method: {status.get('authMethod')})", file=sys.stderr)
        print(f"  sign in with: {claude_adapter.login_command(folder, os.environ)}", file=sys.stderr)
        return 1
    name = args.name or claude_adapter.slug(status.get("orgName") or "personal")
    reg.set_account("claude", name, claude_adapter.identity_from(status, tilde(folder), pin_email=args.pin_email))
    reg.set_connection(project.name, "claude", name)
    reg.save()
    print(f"{project.name}: claude account '{name}' → {status.get('orgName') or 'personal'} "
          f"({status.get('subscriptionType')}) in {tilde(folder)}")
    return 0


def cmd_add_github(args) -> int:
    """Record which GitHub account a project acts as. The login must already be in `gh auth status`."""
    from .adapters.github import git_identity, read_token

    reg = Registry.load(args.registry)
    project = _project_from_args(reg, args)
    if project is None:
        return 1
    token, reason = read_token(args.login, dict(os.environ))
    if token is None:
        fix = (f"install it ({INSTALL['gh']}), then sign in" if "not found" in reason else
               f"sign in first as {args.login}")
        print(f"{reason} — {fix}: gh auth login --hostname github.com --web", file=sys.stderr)
        return 1
    actual = ADAPTERS["github"].whoami(token)
    del token
    if actual is None:
        print("GitHub did not confirm the stored token", file=sys.stderr)
        return 1
    if actual.lower() != args.login.lower():
        print(f"gh's stored login for {args.login} actually authenticates as {actual}.\n"
              f"  Sign in again as {args.login}: gh auth login --hostname github.com --git-protocol ssh --web\n"
              f"  then switch back: gh auth switch --user <your usual account>", file=sys.stderr)
        return 1
    root = Path(project.roots[0]).expanduser()
    email, host = git_identity(str(root))
    name = args.name or args.login.lower()
    # accounts are shared by projects: merge, so one project's repo details never erase another's
    account = dict(reg.data["accounts"].get("github", {}).get(name, {}), login=args.login)
    for key, explicit, found in (("email", args.email, email), ("ssh_host", args.ssh_host, host)):
        if explicit:
            account[key] = explicit
        elif found and key not in account:
            account[key] = found
    reg.set_account("github", name, account)
    reg.set_connection(project.name, "github", name)
    reg.save()
    extras = ", ".join(f"{k} {v}" for k, v in account.items() if k != "login")
    print(f"{project.name}: github account '{name}' → {args.login}" + (f" ({extras})" if extras else ""))
    return 0


def _chatgpt_login(folder: Path) -> tuple[dict | None, str]:
    """The ChatGPT login in a CODEX_HOME (labels only), or None and why not."""
    from .adapters.codex import read_login

    login, reason = read_login(folder)
    if login is None or login["mode"] != "chatgpt" or login["api_key"]:
        return None, f"no clean ChatGPT login in {tilde(folder)} ({reason or 'API key login'})"
    return login, ""


def cmd_add_codex(args) -> int:
    """Record which Codex (ChatGPT) login a project uses. Default: the login in ~/.codex."""
    from .adapters.codex import home

    reg = Registry.load(args.registry)
    project = _project_from_args(reg, args)
    if project is None:
        return 1
    conn = {"codex_home": args.codex_home} if args.codex_home else {}
    folder = home(conn, dict(os.environ))
    login, problem = _chatgpt_login(folder)
    if login is None:
        print(f"{problem} — run: codex login", file=sys.stderr)
        return 1
    name = args.name or (login["plan"] or "chatgpt")
    account = {"codex_home": tilde(folder), "plan": login["plan"]}
    if args.pin_account:
        account.update(account_id=login["account_id"], pin_account=True)
    reg.set_account("codex", name, {k: v for k, v in account.items() if v})
    reg.set_connection(project.name, "codex", name)
    reg.save()
    print(f"{project.name}: codex account '{name}' → ChatGPT {login['plan']} in {tilde(folder)}"
          + (" (pinned to this account)" if args.pin_account else ""))
    return 0


def _vercel_identity(folder: Path, team: str | None) -> tuple[dict | None, str]:
    """Who is signed in in this Vercel folder, and the team (`team`, else the one selected there): labels only."""
    adapter = ADAPTERS["vercel"]
    login, reason, _ = adapter.current_login(folder, dict(os.environ))
    if login is None:
        return None, f"{reason} ({tilde(folder)})"
    token = login["token"]
    user, reason, _ = adapter.whoami(token)
    if user is None:
        return None, reason
    record = {"user": user["username"]}
    wanted = team or login["current_team"]
    if wanted:
        found, reason, _ = adapter.team_of(token, wanted)
        if found is None:
            return None, reason + ("" if team else " (the team selected in that login) — pick one with --team SLUG")
        record.update(team=found["slug"], team_id=found["id"])
    return record, ""


def cmd_add_vercel(args) -> int:
    """Record which Vercel login (global config folder) and team a project uses. Default: the CLI's own login."""
    reg = Registry.load(args.registry)
    project = _project_from_args(reg, args)
    if project is None:
        return 1
    if args.new:
        if not args.name:
            print("--new needs --name (the account's short name)", file=sys.stderr)
            return 1
        folder = config_dir() / "vercel" / args.name
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)  # the CLI keeps the login's token here
        reg.set_account("vercel", args.name, {"config_dir": tilde(folder)})
        reg.set_connection(project.name, "vercel", args.name)
        reg.save()
        team = f" --team {args.team}" if args.team else ""
        print(f"Created {tilde(folder)}. Sign in once from inside {project.name} (Keyfit sends the login to that "
              f"folder), then record who it is:\n  vercel login\n"
              f"  keyfit add vercel --name {args.name} --config-dir {tilde(folder)}{team}")
        return 0
    env = dict(os.environ)
    folder = canonical(args.config_dir) if args.config_dir else canonical(vercel_adapter.cli_default(env))
    record, problem = _vercel_identity(folder, args.team)
    if record is None:
        print(f"could not record the Vercel login: {problem}\n  sign in with: "
              f"{vercel_adapter.login_command(folder, env)}", file=sys.stderr)
        return 1
    name = args.name or record.get("team") or record["user"]
    reg.set_account("vercel", name, {"config_dir": tilde(folder), **record})
    reg.set_connection(project.name, "vercel", name)
    reg.save()
    team = f", team {record['team']}" + ("" if args.team else " (selected in that login; another: --team SLUG)") \
        if record.get("team") else ""
    print(f"{project.name}: vercel account '{name}' → {record['user']}{team} in {tilde(folder)}")
    return 0


def cmd_run(args) -> int:
    """Run a command with the project's environment (its accounts, checked first)."""
    reg = Registry.load(args.registry)
    r = resolve(".", reg)
    if not r.registered:
        print(f"keyfit run: this folder is not part of a registered project ({r.how}).\n"
              "  Run it inside the project's repository, or register this one: keyfit init", file=sys.stderr)
        return 1
    project = r.project
    env = environment.build(project)
    failed = [c for c in environment.verify(project, env, use_cache=True) if c.failed]
    if failed:
        print(f"keyfit run: {project.name} needs attention", file=sys.stderr)
        for check in failed:
            for line in check.lines():
                print(line, file=sys.stderr)
        return 1
    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        print(f"{project.name}: checks ok")
        return 0
    try:
        os.execvpe(command[0], command, env)
    except OSError as err:
        print(f"keyfit run: cannot run {command[0]}: {err.strerror}", file=sys.stderr)
        return 127


OLD_PATH_LINE = 'export PATH="$HOME/.config/keyfit/bin:$PATH"  # keyfit shims: claude, codex, aws, gh'
NOT_MACOS = "Keyfit supports macOS for now (it stores keys in the Keychain); Linux is planned."

# Superset's agent wrapper (when it is on PATH) first, Keyfit right behind it, then everything else, so the chain
# is Superset wrapper → Keyfit shim → real tool (Superset finds "the next claude"). Both move to the very front:
# a login profile that prepends another folder after Superset set PATH (e.g. `brew shellenv` in ~/.zprofile)
# must not put real tools ahead of the shims.
SHELL_ZSH = """# Keyfit: put the claude/codex/aws/gh/vercel shims on PATH (written by `keyfit setup`).
# Superset's agent wrapper first when present, then Keyfit, then everything else.
() {
  local kf="@SHIMS@" ss="$HOME/.superset/bin"
  if (( ${path[(Ie)$ss]} )); then path=($ss $kf ${${path:#$kf}:#$ss}); else path=($kf ${path:#$kf}); fi
}
"""

# The same rule in POSIX sh, for bash (macOS ships 3.2): no arrays or `local`, so _kf_ variables unset at the
# end; PATH is walked with parameter expansion (never word-split or globbed). Like zsh, it drops empty entries.
SHELL_SH = """# Keyfit: put the claude/codex/aws/gh/vercel shims on PATH (written by `keyfit setup`).
# Superset's agent wrapper first when present, then Keyfit, then everything else.
_kf="@SHIMS@"; _kf_ss="$HOME/.superset/bin"; _kf_rest="${PATH:+$PATH:}"; _kf_out=""; _kf_has=""
while [ -n "$_kf_rest" ]; do
  _kf_p="${_kf_rest%%:*}"; _kf_rest="${_kf_rest#*:}"
  if [ -z "$_kf_p" ] || [ "$_kf_p" = "$_kf" ]; then continue; fi
  if [ "$_kf_p" = "$_kf_ss" ]; then _kf_has=1; else _kf_out="$_kf_out:$_kf_p"; fi
done
if [ -n "$_kf_has" ]; then PATH="$_kf_ss:$_kf$_kf_out"; else PATH="$_kf$_kf_out"; fi
export PATH
unset _kf _kf_ss _kf_rest _kf_out _kf_has _kf_p
"""

# shell → (PATH file in the config dir, its template, the command that loads it)
SHELLS = {"zsh": ("shell.zsh", SHELL_ZSH, "source"), "bash": ("shell.sh", SHELL_SH, ".")}

# One shim per tool. When the Python or the package it names is gone (Keyfit uninstalled before `keyfit setup
# --remove`, or moved), it says so instead of sh's "No such file or directory", and the tool still doesn't start.
# -I: no PYTHONPATH, user site or current folder; -S: no site-packages and their .pth files (Keyfit needs only
# the standard library), which also saves ~5 ms per launch.
SHIM = """#!/bin/sh
[ -x "@PYTHON@" ] && [ -f "@ENTRY@" ] || { printf '%s\\n' \\
  "keyfit: @TOOL@ not started: Keyfit is not installed where its shims expect it (@ENTRY@)." \\
  "  Upgraded or reinstalled Keyfit? Run: keyfit setup" \\
  "  Uninstalled it? Remove the line ending in '# keyfit: claude/codex/aws/gh shims on PATH' from your shell startup \\
file, then open a new terminal." >&2; exit 127; }
exec "@PYTHON@" -I -S "@ENTRY@" @TOOL@ "$@"
"""


def _rc_files(shell: str) -> list[Path]:
    """Where `--rc` puts the line by default.

    bash: macOS terminals start login shells, which read only the first of ~/.bash_profile, ~/.bash_login and
    ~/.profile that exists (so creating ~/.bash_profile would hide an existing ~/.profile). The line goes at the
    end of that profile, after its own PATH changes. When the profile sources ~/.bashrc, ~/.bashrc gets the line
    too, so non-login shells (`bash` typed in a terminal) have the shims as well; running the file twice is harmless.
    """
    home = Path.home()
    if shell == "zsh":
        return [Path(os.environ.get("ZDOTDIR") or home) / ".zshrc"]
    profile = next((p for p in (home / ".bash_profile", home / ".bash_login", home / ".profile") if p.exists()),
                   home / ".bash_profile")
    text = profile.read_text() if profile.exists() else ""
    return [profile, home / ".bashrc"] if re.search(r"(?:source|\.)\s+\S*\.bashrc\b", text) else [profile]


def _setup_line(shell: str) -> str:
    """The line `keyfit setup --rc` writes into a startup file of this shell. Its text is how Keyfit finds the line
    again (setup, --remove, doctor), so it stays as it is although the shims now include vercel."""
    filename, _, load = SHELLS[shell]
    return f'{load} "{config_dir() / filename}"  # keyfit: claude/codex/aws/gh shims on PATH'


def _startup_files() -> list[Path]:
    """Every existing startup file `keyfit setup --rc` may have written to, for zsh and for bash."""
    home = Path.home()
    names = (".bash_profile", ".bash_login", ".profile", ".bashrc")
    files = [Path(os.environ.get("ZDOTDIR") or home) / ".zshrc", home / ".zshrc", *(home / n for n in names)]
    return [p for p in dict.fromkeys(files) if p.is_file()]


def _keyfit_lines() -> set[str]:
    """Every line `keyfit setup` writes into startup files: zsh's, bash's, and the PATH line of earlier versions."""
    return {_setup_line(shell) for shell in SHELLS} | {OLD_PATH_LINE}


def _loads_keyfit(rc: Path) -> bool:
    lines = _keyfit_lines()
    return any(line.strip() in lines for line in rc.read_text(errors="replace").splitlines())


def _add_line(rc: Path, line: str) -> None:
    text = rc.read_text() if rc.exists() else ""
    if line in text:
        print(f"{tilde(rc)} already loads them")
    elif OLD_PATH_LINE in text:
        rc.write_text(text.replace(OLD_PATH_LINE, line))
        print(f"Updated the Keyfit line in {tilde(rc)}:\n  {line}")
    else:
        with rc.open("a") as fh:
            fh.write(f"\n{line}\n")
        print(f"Added to the end of {tilde(rc)} (new terminals pick it up):\n  {line}")


def cmd_setup(args) -> int:
    """Write the shims and the PATH files; only with --rc (or --zshrc) touch a shell startup file."""
    from .realbin import MARKER, shim_dir, stable_path
    from .shim import TOOLS

    if args.remove or args.purge:
        return _remove_setup(args)
    if sys.platform != "darwin":
        print(NOT_MACOS, file=sys.stderr)
        return 1
    shell = "zsh" if args.zshrc else args.shell
    if shell == "auto":
        shell = Path(os.environ.get("SHELL", "")).name
    rc = args.zshrc or args.rc
    if rc is not None and shell not in SHELLS:
        print(f"keyfit setup: no PATH file for the shell '{shell}' yet; run again with --shell zsh or --shell bash",
              file=sys.stderr)
        return 1
    folder = shim_dir()
    folder.mkdir(parents=True, exist_ok=True)
    python, entry = stable_path(sys.executable), stable_path(Path(__file__).resolve().parent / "_shim_entry.py")
    (folder / MARKER).write_text("Keyfit shims live here; Keyfit never resolves a real binary from this folder.\n")
    for tool in TOOLS:
        script = folder / tool
        script.write_text(SHIM.replace("@PYTHON@", python).replace("@ENTRY@", entry).replace("@TOOL@", tool))
        script.chmod(0o755)
    for filename, template, _ in SHELLS.values():
        (config_dir() / filename).write_text(template.replace("@SHIMS@", str(folder)))
    print(f"Shims written to {tilde(folder)}: {', '.join(TOOLS)}")
    if shell not in SHELLS:
        print(f"Keyfit has PATH files for zsh and bash; in {shell or 'your shell'}, put {folder} first on PATH "
              f"(after Superset's wrapper, if you use Superset).")
        return 0
    line = _setup_line(shell)
    if rc is None:
        again = "keyfit setup" + (f" --shell {shell}" if args.shell != "auto" else "") + " --rc"
        files = " and ".join(tilde(p) for p in _rc_files(shell))
        print(f"To activate, add this as the LAST line of {files} (or run: {again}):\n  {line}")
    else:
        for path in [Path(rc).expanduser()] if rc else _rc_files(shell):
            _add_line(path, line)
        print("Next: open a new terminal, go to a project's repository and run: keyfit init")
    undo = "keyfit setup --remove" + (f" --rc {tilde(Path(rc).expanduser())}" if rc else "")
    print(f"Undo any time: {undo}; run a tool once without checks: KEYFIT_DISABLE=1 claude")
    return 0


# What Keyfit itself keeps in its config dir; --purge deletes the folder only when it holds nothing else.
KEYFIT_FILES = {"registry.toml", "registry.toml.tmp", "cache", "bin", "shell.zsh", "shell.sh", "claude", "vercel",
                ".DS_Store"}


def _drop_lines(rc: Path, lines: set[str]) -> bool:
    """Remove these exact lines from a startup file, with the blank line `keyfit setup` put before each."""
    try:
        old = rc.read_text().splitlines(keepends=True)
    except (OSError, UnicodeDecodeError):
        return False
    keep = []
    for i, line in enumerate(old):
        if line.strip() not in lines:
            keep.append(line)
        elif keep and not keep[-1].strip() and (i + 1 == len(old) or not old[i + 1].strip()):
            keep.pop()
    if len(keep) == len(old):
        return False
    rc.write_text("".join(keep))
    return True


def _purge_confirmed(folder: Path, args) -> bool:
    home, target = Path.home().resolve(), folder.resolve()
    foreign = sorted(p.name for p in folder.iterdir() if p.name not in KEYFIT_FILES)
    if target == home or target in home.parents or foreign:
        print(f"{tilde(folder)} also holds {', '.join(foreign) or 'your home folder'}, which Keyfit did not write — "
              "nothing changed; delete it yourself if you are sure", file=sys.stderr)
        return False
    if args.yes:
        return True
    if not sys.stdin.isatty():
        print(f"--purge deletes {tilde(folder)}; confirm with --yes — nothing changed", file=sys.stderr)
        return False
    accounts = [(title, sorted(p.name for p in (folder / kind).glob("*") if p.is_dir()))
                for kind, title in (("claude", "Claude"), ("vercel", "Vercel"))]
    extra = "".join(f" and the {title} account folders {', '.join(names)}" for title, names in accounts if names)
    if _yes(f"Delete {tilde(folder)}: the registry of your projects{extra}?"):
        return True
    print("Nothing changed.")
    return False


def _remove_setup(args) -> int:
    """`keyfit setup --remove`: undo setup — the startup-file lines, the shims and the PATH files. --purge also
    deletes the config dir (registry, cache, account folders); Keychain items are never touched."""
    import shutil

    from .realbin import MARKER, shim_dir
    from .shim import TOOLS

    if not args.remove:
        print("keyfit setup: --purge goes with --remove", file=sys.stderr)
        return 1
    folder = config_dir()
    if args.purge and folder.is_dir() and not _purge_confirmed(folder, args):
        return 1
    rc = args.zshrc or args.rc
    files = [Path(rc).expanduser()] if rc else _startup_files()
    changed = [path for path in files if _drop_lines(path, _keyfit_lines())]
    for path in changed:
        print(f"Removed the Keyfit line from {tilde(path)}")
    if not changed:
        print(f"No Keyfit line in {', '.join(tilde(p) for p in files) or 'any shell startup file'}")
    shims = shim_dir()
    for tool in TOOLS:  # only Keyfit's own shims, never a file someone else put there
        script = shims / tool
        if script.is_file() and "_shim_entry.py" in script.read_text(errors="replace"):
            script.unlink()
    (shims / MARKER).unlink(missing_ok=True)
    try:
        shims.rmdir()
    except OSError:
        pass
    for filename, _, _ in SHELLS.values():
        (folder / filename).unlink(missing_ok=True)
    print(f"Removed the shims and PATH files from {tilde(folder)}")
    if args.purge and folder.is_dir():
        try:
            stored = Registry.load(args.registry).stored_secrets()
        except (OSError, ValueError):
            stored = []
        shutil.rmtree(folder)
        print(f"Deleted {tilde(folder)}")
        if stored:
            print(f"Keychain items Keyfit stored are kept (service {secrets.SERVICE}): {', '.join(stored)} — "
                  "delete them in Keychain Access if you no longer need them")
    elif default_path().exists():
        print(f"Kept the registry ({tilde(default_path())}); delete it too with: keyfit setup --remove --purge")
    else:
        try:
            folder.rmdir()  # only when nothing else is left in it
        except OSError:
            pass
    print("Open a new terminal (or run: hash -r): claude, codex, aws, gh and vercel run directly again.")
    return 0


def _short(path: str) -> str:
    """A path as found (symlinks kept, unlike tilde()), with the home folder shown as ~."""
    home = str(Path.home())
    return "~" + path[len(home):] if path.startswith(home + os.sep) else path


def _check_shims(folder: Path) -> Check:
    from .realbin import MARKER
    from .shim import TOOLS

    label = tilde(folder)
    if not (folder / MARKER).is_file():
        return Check("shims", "fail", label, "not written yet" if not folder.exists() else f"{MARKER} missing",
                     "keyfit setup")
    missing = [tool for tool in TOOLS if not os.access(folder / tool, os.X_OK)]
    if missing:
        return Check("shims", "fail", label, f"missing: {', '.join(missing)}", "keyfit setup")
    for tool in TOOLS:  # each shim runs the Python and the checkout `keyfit setup` was run with
        m = re.search(r'exec "([^"]+)" -I (?:-S )?"([^"]+)"', (folder / tool).read_text(errors="replace"))
        gone = [p for p in (m.groups() if m else ()) if not Path(p).exists()]
        if gone:
            return Check("shims", "fail", label, f"{tool} runs {gone[0]}, which no longer exists",
                         "keyfit setup   (rewrites them for this Python and this checkout)")
    return Check("shims", "ok", label, ", ".join(TOOLS))


def _check_path(env: dict) -> Check:
    """The shims come before every real tool — right after Superset's agent wrapper when it is on PATH."""
    from .realbin import is_shim_dir, shim_dir, wrapper_dirs
    from .shim import TOOLS

    folder = canonical(shim_dir())
    label = tilde(folder)
    entries = [e for e in env.get("PATH", "").split(os.pathsep) if e]
    places = [canonical(e) for e in entries]
    loading = [rc for rc in _startup_files() if _loads_keyfit(rc)]
    if folder not in places:
        return Check("PATH", "fail", label, "not on PATH",
                     f"open a new terminal ({tilde(loading[0])} loads Keyfit)" if loading
                     else "keyfit setup --rc, then open a new terminal")
    last_line = (f"make Keyfit's line (`…  # keyfit: …`) the last line of "
                 f"{tilde(loading[0]) if loading else 'your shell startup file'}, then open a new terminal")
    at = places.index(folder)
    superset = canonical(Path(env.get("HOME") or Path.home()) / ".superset" / "bin")
    if superset in places[at:] and superset not in places[:at]:
        return Check("PATH", "fail", label, "comes before Superset's agent wrapper (~/.superset/bin)", last_line)
    wrappers = {canonical(w) for w in wrapper_dirs(env)}
    for entry, place in zip(entries[:at], places[:at]):
        if place in wrappers or is_shim_dir(place):
            continue
        ahead = [tool for tool in TOOLS if (place / tool).is_file() and os.access(place / tool, os.X_OK)]
        if ahead:
            return Check("PATH", "fail", label, f"{_short(entry)} comes first: {', '.join(ahead)} would run without Keyfit",
                         last_line)
    where = ("first" if superset not in places
             else "right after Superset's agent wrapper" if places.index(superset) == at - 1
             else "after Superset's agent wrapper")
    return Check("PATH", "ok", label, f"{where}, before every real tool")


def cmd_doctor(args) -> int:
    """Is Keyfit installed and wired up? One line per check, the fix under each failure. Never reads a secret."""
    import platform

    from .realbin import find_real, shim_dir
    from .shim import TOOLS

    env = dict(os.environ)
    checks = [
        Check("macOS", "ok", platform.mac_ver()[0] or "macOS") if sys.platform == "darwin"
        else Check("macOS", "fail", sys.platform, "Keyfit runs on macOS only for now", "use it on a Mac (Linux is planned)"),
        Check("python", "ok" if sys.version_info >= (3, 11) else "fail", platform.python_version(), _short(sys.executable),
              "install Python 3.11 or newer, then run keyfit setup with it"),
        _check_shims(shim_dir()),
        _check_path(env),
    ]
    for tool in TOOLS:
        real = find_real(tool, env, skip_wrappers=True)
        checks.append(Check(tool, "ok", _short(real)) if real else
                      Check(tool, "warn", "not found on PATH", "needed only if you use it", INSTALL[tool]))
    path = Path(args.registry) if args.registry else default_path()
    try:
        reg = Registry.load(path)
        count = len(reg.projects())
        checks.append(Check("registry", "ok", tilde(path), f"{count} project{'s' if count != 1 else ''}"
                            if path.exists() else "no projects yet"))
    except RegistryError as err:
        reg = None
        checks.append(Check("registry", "fail", tilde(path), f"unreadable ({err})", err.fix))
    reason = secrets.keychain_reachable()
    checks.append(Check("keychain", "fail", "macOS Keychain", reason,
                        "/usr/bin/security list-keychains   (then repair the login keychain in Keychain Access)")
                  if reason else Check("keychain", "ok", "macOS Keychain", "reachable"))
    if reg is not None:
        r = resolve(".", reg)
        if r.registered:
            checks.append(Check("project", "ok", r.project.name, r.how))
        else:  # outside a repository, `keyfit init` would register the whole folder (the home folder, say)
            fix = "keyfit init" if git_main_checkout(r.path) else "cd into a project's repository, then: keyfit init"
            checks.append(Check("project", "warn", "this folder", f"not registered ({r.how}): tools run here unchanged",
                                fix))
    for check in checks:
        for line in check.lines():
            print(line)
    failed = sum(c.failed for c in checks)
    print(f"{failed} problem{'s' if failed != 1 else ''} — the fix is under each ✘" if failed else "All good.")
    return 1 if failed else 0


START = """start here:
  keyfit setup --rc     put Keyfit in front of claude, codex, aws, gh and vercel, then open a new terminal
  keyfit init           inside a project's repository: register it and pick its accounts
  claude                starts with that project's accounts, checked
something off? keyfit doctor
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="keyfit", description="Per-project, verified environments for AI coding tools.",
                                     epilog=START, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--registry", type=Path, help="registry file (default: ~/.config/keyfit/registry.toml)")
    parser.add_argument("--version", action="version", version=f"keyfit {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    def command(parent, name: str, text: str):  # the one-line help is also the command's own --help description
        return parent.add_parser(name, help=text, description=text[0].upper() + text[1:] + ".")

    p = command(sub, "setup", "write the claude/codex/aws/gh/vercel shims; with --rc also put them on PATH")
    p.add_argument("--shell", choices=("auto", "zsh", "bash"), default="auto",
                   help="shell to set up (default: auto, from $SHELL)")
    p.add_argument("--rc", nargs="?", const="", metavar="FILE",
                   help="add Keyfit's line to the end of FILE; without FILE, to the shell's startup file: ~/.zshrc, "
                        "or for bash the login profile (plus ~/.bashrc when the profile sources it)")
    p.add_argument("--zshrc", metavar="FILE", help="same as --shell zsh --rc FILE")
    p.add_argument("--remove", action="store_true",
                   help="undo setup: delete the shims and PATH files and take Keyfit's line out of ~/.zshrc, "
                        "~/.bash_profile, ~/.bash_login, ~/.profile and ~/.bashrc (or only --rc FILE); "
                        "the registry stays")
    p.add_argument("--purge", action="store_true",
                   help="with --remove: also delete ~/.config/keyfit (registry, cache, Claude and Vercel account "
                        "folders it made), after asking; Keychain items stay")
    p.add_argument("-y", "--yes", action="store_true", help="with --purge: do not ask")
    p.set_defaults(func=cmd_setup)

    p = command(sub, "init", "register the current repository as a project, then offer the AWS, GitHub, Claude, "
                             "Codex, Vercel and Linear accounts found on this Mac (imports `use aws` / "
                             "LINEAR_API_KEY lines from its .envrc, if any)")
    p.add_argument("--name", help="project name (default: folder name of the main checkout)")
    p.add_argument("--root", help="path inside the repository (default: current folder)")
    p.add_argument("-y", "--yes", action="store_true",
                   help="ask nothing: register and import from .envrc only (also when stdin is not a terminal)")
    p.set_defaults(func=cmd_init)

    p = command(sub, "status", "check every account of the current project now (online, unless --offline)")
    p.add_argument("path", nargs="?", help="a folder of the project (default: the current folder)")
    p.add_argument("-v", "--verbose", action="store_true", help="also say how the folder was matched to the project")
    p.add_argument("--offline", action="store_true", help="local checks only, no network")
    p.set_defaults(func=cmd_status)

    add = command(sub, "add", "add or change one account of the current project")
    kinds = add.add_subparsers(dest="kind", required=True, metavar="ACCOUNT")
    project = {"help": "project name (default: the project of the current folder)"}
    p = command(kinds, "aws", "AWS profile and the account it must resolve to")
    p.add_argument("--profile", required=True, help="a named profile from ~/.aws/config (never `default`)")
    p.add_argument("--account", help="expected 12-digit account (default: from the profile, else ask AWS once)")
    p.add_argument("--region", help="AWS region the tools get (default: the profile's own)")
    p.add_argument("--project", **project)
    p.set_defaults(func=cmd_add_aws)
    p = command(kinds, "github", "GitHub account (from `gh auth status`) this project acts as")
    p.add_argument("--login", required=True, help="GitHub login, e.g. octocat")
    p.add_argument("--name", help="short account name, reusable across projects (default: the login)")
    p.add_argument("--email", help="expected git author email (default: the repo's current user.email)")
    p.add_argument("--ssh-host", help="expected ssh host alias of origin (default: the current one)")
    p.add_argument("--project", **project)
    p.set_defaults(func=cmd_add_github)
    p = command(kinds, "claude", "Claude Code login (config directory) for this project")
    p.add_argument("--name", help="short account name, reusable across projects (default: from the org name)")
    p.add_argument("--config-dir", help="the account's config directory (default: ~/.claude, your current login)")
    p.add_argument("--new", action="store_true", help="create a new config directory for another account")
    p.add_argument("--pin-email", action="store_true",
                   help="require this exact login, not just the org (default: any login of the org, e.g. claude-swap pools)")
    p.add_argument("--project", **project)
    p.set_defaults(func=cmd_add_claude)
    p = command(kinds, "codex", "Codex (ChatGPT) login for this project")
    p.add_argument("--name", help="short account name, reusable across projects (default: the plan, e.g. pro)")
    p.add_argument("--codex-home", help="the account's CODEX_HOME (default: ~/.codex, your current login)")
    p.add_argument("--pin-account", action="store_true",
                   help="require this exact ChatGPT account (default: any ChatGPT login, e.g. codex-multi-auth pools)")
    p.add_argument("--project", **project)
    p.set_defaults(func=cmd_add_codex)
    p = command(kinds, "vercel", "Vercel login (global config folder) and team for this project")
    p.add_argument("--name", help="short account name, reusable across projects (default: the team slug, "
                                  "else the username)")
    where = p.add_mutually_exclusive_group()
    where.add_argument("--config-dir", help="the login's global config folder (default: the one the vercel CLI uses, "
                                            "your current login)")
    where.add_argument("--new", action="store_true",
                       help="create a folder for another login (~/.config/keyfit/vercel/NAME); sign in afterwards")
    p.add_argument("--team", help="team slug or ID the project deploys to (default: the team selected in that login)")
    p.add_argument("--project", **project)
    p.set_defaults(func=cmd_add_vercel)
    p = command(kinds, "linear", "Linear workspace and where its API key lives")
    p.add_argument("--key", help="env:VAR, keychain:service/account, or a bare variable name "
                                 "(default: paste the key; it is verified, then stored in the Keychain)")
    p.add_argument("--workspace", help="expected workspace slug (default: ask Linear once and record it)")
    p.add_argument("--readonly", action="store_true", help="expose Linear's read-only MCP endpoint")
    p.add_argument("--project", **project)
    p.set_defaults(func=cmd_add_linear)
    p = command(kinds, "mcp", "MCP servers that belong to this project (hidden in all others)")
    p.add_argument("servers", nargs="+", metavar="NAME", help="server name or pattern, e.g. 'globex_cloud_*'")
    p.add_argument("--project", **project)
    p.set_defaults(func=cmd_add_mcp)

    p = command(sub, "doctor", "check that Keyfit is installed and on PATH, with the fix for each problem "
                               "(names only, never secrets)")
    p.set_defaults(func=cmd_doctor)

    p = command(sub, "which", "which project this folder belongs to, and why")
    p.add_argument("path", nargs="?", help="a folder (default: the current folder)")
    p.add_argument("-v", "--verbose", action="store_true", help="also list the project's accounts")
    p.set_defaults(func=cmd_which)

    p = command(sub, "projects", "list registered projects")
    p.set_defaults(func=cmd_projects)

    p = command(sub, "run", "run any other command with the project's accounts, checked first "
                            "(keyfit run -- terraform plan)")
    p.add_argument("command", nargs=argparse.REMAINDER, metavar="-- COMMAND …", help="the command and its arguments")
    p.set_defaults(func=cmd_run)

    secret = command(sub, "secret", "store keys in the macOS Keychain (values never shown)")
    actions = secret.add_subparsers(dest="action", required=True, metavar="ACTION")
    p = command(actions, "set", "store a key from a hidden prompt as keychain:keyfit/NAME")
    p.add_argument("name", metavar="NAME", help="letters, digits, '.', '_' and '-', e.g. openai-acme")
    p.set_defaults(func=cmd_secret_set)
    p = command(actions, "list", "names of the keys Keyfit stored")
    p.set_defaults(func=cmd_secret_list)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:  # plain `keyfit`: what it is and where to start
        parser.print_help()
        return 0
    try:
        return args.func(args)
    except RegistryError as err:  # a hand edit: say where and how to fix it, not a traceback
        print(f"keyfit: cannot use {tilde(err.path)}: {err}\n  → {err.fix}", file=sys.stderr)
        return 1
