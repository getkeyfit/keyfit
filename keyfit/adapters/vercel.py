"""Vercel: which Vercel login (global config folder) and team a project deploys with.

Account:  [accounts.vercel.acme]
          config_dir = "~/Library/Application Support/com.vercel.cli"   user = "octocat"
          optional: team = "acme" (slug), team_id = "team_…"
Project:  vercel = "acme"

The CLI reads no token from the environment. It keeps its login in a "global config" folder
(auth.json: the token; config.json: currentTeam, the default scope), and `--global-config DIR`
picks another folder. Its default folder means "no flag", like Claude's ~/.claude.

inject  — remove inherited variables that point the CLI at another project or team: VERCEL_ORG_ID,
          VERCEL_PROJECT_ID (and their older NOW_ names) and VERCEL_TEAM_ID. Also VERCEL_TOKEN: the CLI
          ignores it, but Vercel's SDK and other tools an agent may start read it.
          The shim adds the arguments: `--global-config DIR` unless DIR is the folder the CLI picks by
          itself, and `--scope TEAM` when the account has a team (keyfit/shim.py). It refuses a folder
          named on the command line (--cwd, `deploy <path>`, `dev <dir>`) that is linked to another team.
local   — auth.json holds a token (an expired one only with a refresh token); the repository is not
          linked (.vercel/project.json, .vercel/repo.json: the entry for this folder) to another team than
          the account's.
remote  — GET api.vercel.com/v2/user must return the expected username; with a team,
          GET /v2/teams/<team> must succeed with the same token (membership) and match its ID and slug.
          An expired access token is first renewed by the real CLI (`vercel whoami`), as every vercel
          command does before it starts.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import subprocess
import time
import urllib.parse
from pathlib import Path

from ..checks import Check
from ..realbin import find_real
from ..registry import canonical, tilde
from .base import Adapter

# What the CLI reads (getPlatformEnv("ORG_ID") etc. accept the NOW_ names too; VERCEL_TEAM_ID adds ?teamId=
# to every deployment request), plus the token variable of Vercel's other tools.
SCRUB = ("VERCEL_TOKEN", "VERCEL_ORG_ID", "VERCEL_PROJECT_ID", "NOW_ORG_ID", "NOW_PROJECT_ID", "VERCEL_TEAM_ID")
API = "https://api.vercel.com"
APP = "com.vercel.cli"
LINKS = (".vercel/project.json", ".vercel/repo.json", ".now/project.json")
# The CLI's global options that choose the login, the team or the folder it works on. Its parser (arg) reads
# `--name value`, `--name=value` and `-x value`, also as the last letter of a group (`-dS acme`); `--` ends options.
SHORT = {"Q": "--global-config", "S": "--scope", "T": "--team", "t": "--token"}
LONG = {"--global-config", "--scope", "--team", "--token", "--cwd"}
# Its other global options, with a value, then without. Any other option before the first word belongs to deploy,
# the default command: `vercel --prod app` deploys app.
VALUED, SHORT_VALUED = LONG | {"--local-config", "--api"}, {*SHORT, "A"}
GLOBAL = VALUED | {"--help", "--version", "--debug", "--no-color", "--non-interactive", "--changelog"}
SHORT_GLOBAL = SHORT_VALUED | {"h", "v", "d"}


def _home(env: dict) -> Path:
    return Path(env.get("HOME") or Path.home())


def _shell(text) -> str:
    text = str(text)
    return text if re.fullmatch(r"[\w@%+=:,./~-]+", text) else shlex.quote(text)


def cli_default(env: dict) -> Path:
    """The folder the CLI uses without --global-config: the first existing of its XDG data folders (on macOS
    ~/Library/Application Support/com.vercel.cli), ~/.now and the older `now` data folders; else the first."""
    data = env.get("XDG_DATA_HOME") or str(_home(env) / "Library" / "Application Support")
    dirs = [data, *(d for d in env.get("XDG_DATA_DIRS", "").split(os.pathsep) if d)]
    candidates = [Path(d) / APP for d in dirs] + [_home(env) / ".now"] + [Path(d) / "now" for d in dirs]
    return next((c for c in candidates if c.is_dir() and not c.is_symlink()), candidates[0])  # lstat, like the CLI


def is_default(config_dir, env: dict) -> bool:
    return canonical(config_dir) == canonical(cli_default(env))


def config_args(conn, env: dict) -> list[str]:
    """`--global-config DIR` for the account's folder, nothing when it is the folder the CLI picks by itself."""
    config_dir = conn.get("config_dir")
    if not config_dir or is_default(config_dir, env):
        return []
    return ["--global-config", str(canonical(config_dir))]


def login_command(config_dir, env: dict) -> str:
    if is_default(config_dir, env):
        return "vercel login"
    return f"vercel login --global-config {_shell(canonical(config_dir))}"


def add_command(conn, config_dir, env: dict) -> str:
    where = "" if is_default(config_dir, env) else f" --config-dir {_shell(tilde(config_dir))}"
    return f"keyfit add vercel --name {conn.get('name', '<name>')}{where}"


def read_login(folder: Path) -> tuple[dict | None, str]:
    """{token, expired, refreshable, current_team} from auth.json and config.json. The token is never printed."""
    try:
        auth = json.loads((folder / "auth.json").read_text())
    except FileNotFoundError:
        return None, "not signed in"
    except (OSError, ValueError):
        return None, "auth.json is unreadable"
    if not isinstance(auth, dict) or not isinstance(auth.get("token"), str) or not auth["token"]:
        return None, "not signed in"
    try:
        config = json.loads((folder / "config.json").read_text())
    except (OSError, ValueError):
        config = {}
    expires = auth.get("expiresAt")  # seconds; logins from before OAuth have none and never expire
    team = config.get("currentTeam") if isinstance(config, dict) else None
    return {"token": auth["token"], "expired": isinstance(expires, (int, float)) and expires < time.time(),
            "refreshable": "refreshToken" in auth, "current_team": team if isinstance(team, str) else None}, ""


def refresh(folder: Path, env: dict, timeout: float = 30) -> str:
    """Let the real CLI renew its own expired access token (every vercel command does that first): `vercel whoami`.
    Returns why that failed, or ""."""
    vercel = find_real("vercel", env, skip_wrappers=True)
    if vercel is None:
        return "vercel not found on PATH"
    clean = {k: v for k, v in env.items() if k not in SCRUB}
    clean["VERCEL_TELEMETRY_DISABLED"] = "1"  # Keyfit's own call; no telemetry
    try:
        subprocess.run([vercel, "whoami", *config_args({"config_dir": str(folder)}, env)], env=clean,
                       capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL, check=False)
    except subprocess.TimeoutExpired:
        return f"vercel whoami did not answer within {timeout:g} s"
    except OSError:
        return "vercel whoami could not run"
    return ""


def options(args: list[str]) -> list[tuple[str, str | None]]:
    """(long name, value) of each --global-config, --scope, --team, --token and --cwd option, read like the CLI."""
    found, i = [], 0
    while i < len(args):
        arg = args[i]
        if arg == "--":
            break
        if arg.startswith("--"):
            name, eq, value = arg.partition("=")
            if name in LONG:
                if not eq:
                    value = args[i + 1] if i + 1 < len(args) else None
                    i += 1
                found.append((name, value))
        elif arg.startswith("-") and len(arg) > 1:
            letters = arg[1:]
            for j, letter in enumerate(letters):
                if letter in SHORT:
                    value = None
                    if j == len(letters) - 1:  # only the last letter of a group takes a value
                        value = args[i + 1] if i + 1 < len(args) else None
                        i += 1
                    found.append((SHORT[letter], value))
        i += 1
    return found


def targets(args: list[str]) -> tuple[str, list[str]]:
    """What the CLI would link and deploy, or run `vercel dev` in, as named on the command line: ("deploy", [path])
    for `vercel deploy path` and `vercel path`, ("dev", [dir]) for `vercel dev dir`. Every word after deploy or dev
    counts (an option Keyfit does not know may take one as its value), and every word when an option of deploy
    comes first. Keyfit keeps no list of the CLI's commands: in `vercel env ls`, env counts as a path too."""
    words, deploy, i = [], False, 0
    while i < len(args):
        arg = args[i]
        if arg == "--":  # the rest are words
            words += args[i + 1:]
            break
        if arg.startswith("--"):
            name, eq, _ = arg.partition("=")
            deploy |= not words and name not in GLOBAL
            i += 2 if name in VALUED and not eq else 1
        elif arg.startswith("-") and len(arg) > 1:
            deploy |= not words and not set(arg[1:]) <= SHORT_GLOBAL
            i += 2 if arg[-1] in SHORT_VALUED else 1  # only the last letter of a group takes a value
        else:
            words.append(arg)
            i += 1
    if words[:1] == ["dev"] and not deploy:
        return "dev", words[1:]
    if words[:1] == ["deploy"]:
        return "deploy", words[1:]
    return "deploy", words if deploy else words[:1]


def _link_orgs(data, where: str) -> list:
    """The teams a link file names for the folder *where* (relative to the folder that holds the link): orgId of
    project.json. Of repo.json (`vercel link --repo`), that of the projects whose directory holds *where*, the
    deepest like the CLI, else of every project; each falls back to the file's top-level orgId (older files)."""
    if not isinstance(data, dict):
        return []
    projects = [p for p in data.get("projects") or [] if isinstance(p, dict)] \
        if isinstance(data.get("projects"), list) else []
    if not projects:
        return [data.get("orgId")]

    def directory(project) -> str:
        value = project.get("directory")
        return value.strip("/") or "." if isinstance(value, str) else "."
    where = "" if where == "." else where
    ordered = sorted(projects, key=lambda p: -len(directory(p).split("/")))  # stable: ties keep the file's order
    matches = [p for p in ordered if directory(p) in (".", where) or where.startswith(directory(p) + "/")]
    if matches:
        matches = [p for p in matches if directory(p) == directory(matches[0])]
    return [p.get("orgId") or data.get("orgId") for p in matches or projects]


def foreign_link(conn, start, env: dict) -> tuple[Path, str] | None:
    """The first Vercel link the CLI could follow from *start* that names another team than the account's:
    .vercel/project.json (or the older .now/project.json) and .vercel/repo.json (`vercel link --repo`), in
    *start* and its parents up to the repository root. None when no team ID is recorded."""
    team_id = conn.get("team_id")
    if not team_id:
        return None
    home = canonical(_home(env))
    start = canonical(start)
    for folder in (start, *start.parents):
        if folder == home:
            break
        for rel in LINKS:
            try:
                data = json.loads((folder / rel).read_text())
            except (OSError, ValueError):
                continue
            for org in _link_orgs(data, start.relative_to(folder).as_posix()):
                if isinstance(org, str) and org and org != team_id:
                    return folder / rel, org
        if (folder / ".git").exists():
            break
    return None


def _fetch(path: str, token: str, timeout: float) -> tuple[int, dict]:
    import urllib.error  # only for online checks: urllib.request and TLS take ~30 ms to import
    import urllib.request

    from .. import http

    req = urllib.request.Request(API + path, headers={"Authorization": f"Bearer {token}", "User-Agent": "keyfit",
                                                      "Accept": "application/json"})
    try:
        with http.urlopen(req, timeout) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as err:
        return err.code, {}


def _busy(status: int) -> bool:
    return status == 429 or status >= 500  # rate limit or server error: worth a retry, like a network blip


class VercelAdapter(Adapter):
    kind = "vercel"

    def __init__(self, fetch=_fetch, timeout: float = 15, cli_timeout: float = 30):
        self.fetch = fetch
        self.timeout = timeout
        self.cli_timeout = cli_timeout

    def label(self, conn) -> str:
        user, team = conn.get("user"), conn.get("team") or conn.get("team_id")
        if not user:
            return conn.get("name", "?")
        return f"{user} → {team}" if team else user

    def inject(self, conn, env: dict) -> None:
        for var in SCRUB:
            env.pop(var, None)

    def fingerprint(self, conn, env: dict) -> str:
        """Changes with the stored token (a new login or a renewed one), so a cached check never vouches for another."""
        login, _ = read_login(canonical(conn.get("config_dir") or cli_default(env)))
        return hashlib.sha256((login or {}).get("token", "").encode()).hexdigest()[:16]

    def local(self, conn, env: dict) -> Check:
        label = self.label(conn)
        config_dir = conn.get("config_dir")
        if not config_dir:
            return Check(self.kind, "fail", label, "no config folder recorded", "keyfit add vercel")
        folder = canonical(config_dir)
        if not folder.is_dir():
            return Check(self.kind, "fail", label, f"config folder {config_dir} does not exist",
                         "vercel login" if is_default(folder, env)
                         else f"keyfit add vercel --name {conn.get('name', '<name>')} --new")
        fix = login_command(folder, env)
        login, reason = read_login(folder)
        if login is None:
            return Check(self.kind, "fail", label, reason, fix)
        if not conn.get("user"):
            return Check(self.kind, "fail", label, "no expected identity recorded",
                         f"{fix}  then: {add_command(conn, folder, env)}")
        link = foreign_link(conn, os.getcwd(), env)
        if link is not None:
            path, org = link
            return Check(self.kind, "fail", label,
                         f"{tilde(path)} links this repository to another Vercel team ({org}), "
                         f"project expects {conn.get('team') or conn['team_id']}",
                         f"rm -r {_shell(tilde(path.parent))}, then link it again: vercel link")
        if login["expired"]:
            if not login["refreshable"]:
                return Check(self.kind, "fail", label, "signed out (the access token expired)", fix)
            return Check(self.kind, "ok", label, "access token due for renewal", fix, pending=True)
        return Check(self.kind, "ok", label, "", pending=True)

    def remote(self, conn, env: dict) -> Check:
        label = self.label(conn)
        folder = canonical(conn["config_dir"])
        fix = login_command(folder, env)
        login, reason, transient = self.current_login(folder, env)
        if login is None:
            return Check(self.kind, "fail", label, reason, fix, transient=transient)
        user, reason, transient = self.whoami(login["token"])
        if user is None:
            return Check(self.kind, "fail", label, reason, "check the network and retry" if transient else fix,
                         transient=transient)
        if user["username"] != conn["user"]:
            return Check(self.kind, "fail", label,
                         f"signed in as {user['username']}, project expects {conn['user']}",
                         f"{fix}   (as {conn['user']})")
        wanted = conn.get("team_id") or conn.get("team")
        if not wanted:
            return Check(self.kind, "ok", label, "login confirmed")
        team, reason, transient = self.team_of(login["token"], wanted)
        if team is None:
            return Check(self.kind, "fail", label, reason,
                         "check the network and retry" if transient else
                         f"keyfit add vercel --name {conn.get('name', '<name>')} --team <a team of {conn['user']}>"
                         f"   (or sign in as a member: {fix})", transient=transient)
        moved = conn.get("team_id") and team["id"] != conn["team_id"]
        if moved or (conn.get("team") and team["slug"] != conn["team"]):
            return Check(self.kind, "fail", label,
                         f"team {wanted} is now {team['slug']} ({team['id']}), project expects "
                         f"{conn.get('team') or '?'} ({conn.get('team_id') or '?'})",
                         f"keyfit add vercel --name {conn.get('name', '<name>')} --team {team['slug']}")
        return Check(self.kind, "ok", label, "login and team confirmed")

    # helpers, shared with `keyfit add vercel` --------------------------------------------------------------

    def current_login(self, folder: Path, env: dict) -> tuple[dict | None, str, bool]:
        """The folder's login with a usable token: an expired one is renewed by the CLI first."""
        login, reason = read_login(folder)
        if login is None:
            return None, reason, False
        if login["expired"] and login["refreshable"]:
            problem = refresh(folder, env, self.cli_timeout)
            login, reason = read_login(folder)
            if login is None:
                return None, reason, False
            if login["expired"]:
                why = f" ({problem})" if problem else ""
                return None, f"the access token expired and Vercel did not renew it{why}", "did not answer" in problem
        if login["expired"]:
            return None, "signed out (the access token expired)", False
        return login, "", False

    def whoami(self, token: str) -> tuple[dict | None, str, bool]:
        """(user, "", False), or (None, why, transient) from GET /v2/user."""
        try:
            status, body = self.fetch("/v2/user", token, self.timeout)
        except (OSError, ValueError) as err:
            return None, f"Vercel did not answer ({getattr(err, 'reason', None) or type(err).__name__})", True
        if status in (401, 403):
            return None, "Vercel rejected the stored login (signed out or revoked?)", False
        user = body.get("user") if isinstance(body.get("user"), dict) else {}
        if status != 200 or not user.get("username"):
            return None, f"Vercel answered {status}", _busy(status)
        return user, "", False

    def team_of(self, token: str, ident: str) -> tuple[dict | None, str, bool]:
        """The team with this ID or slug, if the token may see it: ({id, slug}, "", False) or (None, why, transient)."""
        try:
            status, body = self.fetch("/v2/teams/" + urllib.parse.quote(ident, safe=""), token, self.timeout)
            if status == 404 and not ident.startswith("team_"):  # a slug the path did not take: try the login's teams
                status, listed = self.fetch("/v2/teams?limit=100", token, self.timeout)
                teams = [t for t in listed.get("teams") or [] if isinstance(t, dict) and t.get("slug") == ident]
                if status == 200:
                    status, body = (200, teams[0]) if teams else (404, {})
        except (OSError, ValueError) as err:
            return None, f"Vercel did not answer ({getattr(err, 'reason', None) or type(err).__name__})", True
        if status in (401, 403, 404):
            return None, f"this login has no access to Vercel team {ident}", False
        if status != 200 or not body.get("id") or not body.get("slug"):
            return None, f"Vercel answered {status}", _busy(status)
        return {"id": body["id"], "slug": body["slug"]}, "", False
