"""Convex: which Convex login (access token) a project uses, and the team its deployments belong to.

Account:  [accounts.convex.acme]
          token = "keychain:keyfit/convex-acme"   (or env:VAR, keychain:service/account, or "cli")
          team = "<team slug>"   team_name = "Acme"
Project:  convex = "acme"

The CLI (npm package `convex`, checked on 1.46) keeps one login per Mac: the access token in ~/.convex/config.json,
written by `npx convex login` and deleted by `npx convex logout`. It has no account switch, but it takes
CONVEX_OVERRIDE_ACCESS_TOKEN before that file and before deploy keys. So an account here is a token Keyfit keeps
(pasted, or copied once from the CLI's login with `keyfit add convex --copy`), or "cli": the CLI's own login, read
from that plain file whenever Keyfit builds a session.
The deployment the CLI works on: CONVEX_DEPLOY_KEY (or CONVEX_DEPLOYMENT_TOKEN), else CONVEX_SELF_HOSTED_URL with
CONVEX_SELF_HOSTED_ADMIN_KEY, else CONVEX_DEPLOYMENT (`dev:happy-animal-123`, written by `npx convex dev`). Each is
read from the environment, else .env.local, else .env in the folder the CLI runs in (dotenv: a variable that is
already set wins, so an inherited one outranks the folder's own files).

inject  — CONVEX_OVERRIDE_ACCESS_TOKEN is the account's token, so every convex the session starts (`npx convex` too)
          acts as that account, also after a `convex login` elsewhere; CONVEX_PROVISION_HOST is removed, so the token
          only goes to api.convex.dev; inherited CONVEX_DEPLOY_KEY, CONVEX_DEPLOYMENT_TOKEN and CONVEX_DEPLOYMENT that
          disagree with the folder's .env.local/.env are removed: they belong to another project.
forget  — in a project without a Convex account, a token Keyfit gave another project's session is removed.
local   — the token is there and is not a deploy key; a preview deploy key names the account's team.
remote  — GET api.convex.dev/api/teams must accept the token and list the team (what `npx convex login status`
          shows); the folder's cloud deployment must belong to that team
          (GET /api/deployment/<name>/team_and_project). Local and self-hosted deployments need no account.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.parse
from pathlib import Path

from .. import secrets
from ..checks import Check
from ..registry import tilde
from .base import Adapter

API = "https://api.convex.dev"
CLI = "cli"  # token = "cli": the convex CLI's own login (~/.convex/config.json)
OVERRIDE = "CONVEX_OVERRIDE_ACCESS_TOKEN"  # the CLI's own override: before its login file and before deploy keys
MARK = "KEYFIT_CONVEX"  # set next to a token Keyfit injected: the account's name
KEY_VARS = ("CONVEX_DEPLOY_KEY", "CONVEX_DEPLOYMENT_TOKEN")  # the second is an alias; the first wins
DEPLOY_VARS = (*KEY_VARS, "CONVEX_DEPLOYMENT")
SELF_HOSTED = ("CONVEX_SELF_HOSTED_URL", "CONVEX_SELF_HOSTED_ADMIN_KEY")
ENV_FILES = (".env.local", ".env")  # read from the folder the CLI runs in, in this order, none overriding
DEPLOY_KEY = re.compile(r"(?:dev|prod|preview|project):[^|\s]*\|")  # what a deploy key starts with
DEPLOYMENT_NAME = re.compile(r"[a-z]+-[a-z]+-[0-9]+")  # the CLI's own pattern (use fullmatch)
SIGN_IN = "npx convex login --force"  # Convex projects run their CLI through npx; --force: even with a deploy key
# Options that pick a login, a team or a deployment. With the other options that take a value (commander reads
# `--name value` and `--name=value`) they are skipped when reading the command words; any other option is a flag.
OPTIONS = {"--admin-key", "--url", "--deployment", "--deployment-name", "--env-file", "--team"}
VALUED = OPTIONS | {"--preview-name", "--project", "--device-name", "--login-flow", "--override-access-token",
                    "--component", "--cmd", "--cmd-url-env-var-name", "--dev-deployment"}


def _home(env: dict) -> Path:
    return Path(env.get("HOME") or Path.home())


def config_path(env: dict) -> Path:
    return _home(env) / ".convex" / "config.json"


def read_cli_login(env: dict) -> tuple[str | None, str]:
    """(token, where) of the CLI's login, or (None, why). A plain file: reading it asks nothing."""
    path = config_path(env)
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return None, "the convex CLI is not signed in"
    except (OSError, ValueError):
        return None, f"{tilde(path)} is unreadable"
    token = data.get("accessToken") if isinstance(data, dict) else None
    if not isinstance(token, str) or not token.strip():
        return None, "the convex CLI is not signed in"
    return token.strip(), tilde(path)


def where(conn) -> str:
    """Where the account's token comes from, for messages."""
    ref = conn.get("token", "")
    return "the convex CLI's login (~/.convex/config.json)" if ref == CLI else secrets.describe(ref)


def add_command(conn) -> str:
    return f"keyfit add convex --name {conn.get('name', '<name>')}"


def fix_for(conn) -> str:
    """How to give the account a working token."""
    ref = conn.get("token", "")
    if ref == CLI:
        return SIGN_IN
    if ref and not ref.startswith("keychain:"):
        return f"set {secrets.describe(ref)} to a Convex access token"
    return f"{add_command(conn)} --copy   (after {SIGN_IN} as that account; or paste a token: {add_command(conn)})"


def shape_problem(token: str) -> str:
    """Why a value cannot be a Convex access token ("" when it may be one). Nothing else is ever sent to Convex."""
    half = len(token) // 2
    if len(token) % 2 == 0 and token[:half] == token[half:]:
        return "looks pasted twice"
    if DEPLOY_KEY.match(token):
        return "holds a deploy key (…|…), not an access token"
    if len(token) < 20 or not re.fullmatch(r"[\x21-\x7e]+", token):
        return "does not hold a Convex access token"
    return ""


def read_env_file(path: Path) -> dict:
    """KEY=VALUE lines like dotenv reads them: `export` allowed, quotes kept apart, ` # comment` after a value."""
    try:
        text = path.read_text()
    except (OSError, UnicodeDecodeError):
        return {}
    out = {}
    for line in text.splitlines():
        m = re.match(r"\s*(?:export\s+)?([\w.-]+)\s*=\s*(.*)$", line)
        if not m:
            continue
        name, value = m.groups()
        quoted = re.match(r"""(['"`])(.*?)\1""", value)
        out[name] = quoted.group(2) if quoted else re.split(r"\s+#", value, maxsplit=1)[0].strip()
    return out


def folder_values(start) -> dict:
    """What the folder's .env.local and .env say, .env.local first (the values the CLI falls back to)."""
    found = {}
    for name in ENV_FILES:
        for key, value in read_env_file(Path(start) / name).items():
            found.setdefault(key, (value, name))
    return found


def scrub(env: dict, start) -> None:
    """Remove inherited deployment variables the folder's own files disagree with: an exported CONVEX_DEPLOYMENT
    outranks .env.local, so another project's would send this folder's commands to that project's deployment."""
    files = folder_values(start)
    for var in DEPLOY_VARS:
        if var in env and env[var] != files.get(var, (None,))[0]:
            del env[var]


def deploy_key_target(key: str, source: str) -> dict:
    """What a deploy key points at: a deployment (`dev:NAME|…`, `prod:NAME|…`), or a team and project
    (`preview:TEAM:PROJECT|…`); a project key is checked by Convex, not here."""
    prefix = key.split("|", 1)[0]
    parts = prefix.split(":")
    if "|" not in key:
        return {"kind": "unusable", "source": source}
    if parts[0] == "preview" and len(parts) == 3:
        return {"kind": "preview", "team": parts[1], "project": parts[2], "source": source}
    if parts[0] == "project":
        return {"kind": "project_key", "source": source}
    return {"kind": "deployment", "name": parts[-1], "type": parts[0] if len(parts) > 1 else "prod", "source": source}


def target(start, env: dict, files: dict | None = None) -> dict | None:
    """The deployment the CLI would act on from *start* without options, and where it comes from (None: none set,
    the CLI would ask). *files*: the values of an --env-file, used instead of the environment and the folder."""
    if files is None:
        found = folder_values(start)

        def get(var):
            if var in env:  # set, even to "": dotenv does not override it
                return env[var] or None, f"${var}"
            value, name = found.get(var, (None, ""))
            return value or None, f"{var} in {name}"
    else:
        def get(var):
            return files.get(var) or None, f"{var} in the --env-file"
    for var in KEY_VARS:
        key, source = get(var)
        if key and key != "<ignore_deploy_key>":
            return deploy_key_target(key, source)
    url, source = get(SELF_HOSTED[0])
    if url and get(SELF_HOSTED[1])[0]:
        return {"kind": "self_hosted", "url": url, "source": source}
    deployment, source = get("CONVEX_DEPLOYMENT")
    if not deployment:
        return None
    kind, name = deployment.split(":")[0], deployment.split(":")[-1]
    if kind == "local" or name.startswith("anonymous-"):
        return {"kind": "local", "name": name, "source": source}
    return {"kind": "deployment", "name": name, "type": kind if ":" in deployment else "", "source": source}


def options(args: list[str]) -> list[tuple[str, str | None]]:
    """(name, value) of each option in OPTIONS, `--name value` and `--name=value`, anywhere before `--`."""
    found, i = [], 0
    while i < len(args):
        arg = args[i]
        if arg == "--":
            break
        name, eq, value = arg.partition("=")
        if name in OPTIONS:
            if not eq:
                value = args[i + 1] if i + 1 < len(args) else None
                i += 1
            found.append((name, value))
        i += 1
    return found


def words(args: list[str]) -> list[str]:
    """The command words and arguments, without options and the values of VALUED options."""
    out, i = [], 0
    while i < len(args):
        arg = args[i]
        if arg == "--":
            break
        if arg.startswith("-") and len(arg) > 1:
            i += 2 if arg in VALUED else 1
            continue
        out.append(arg)
        i += 1
    return out


def _fetch(path: str, token: str, timeout: float) -> tuple[int, object]:
    import urllib.error  # only for online checks: urllib.request and TLS take ~30 ms to import
    import urllib.request

    from .. import http

    req = urllib.request.Request(API + path, headers={"Authorization": f"Bearer {token}", "User-Agent": "keyfit",
                                                      "Accept": "application/json"})
    try:
        with http.urlopen(req, timeout) as resp:
            return resp.status, json.loads(resp.read() or b"null")
    except urllib.error.HTTPError as err:
        return err.code, None


def _busy(status: int) -> bool:
    return status == 429 or status >= 500  # rate limit or server error: worth a retry, like a network blip


def _why(err: Exception) -> str:
    return f"Convex did not answer ({getattr(err, 'reason', None) or type(err).__name__})"


class ConvexAdapter(Adapter):
    kind = "convex"

    def __init__(self, fetch=_fetch, timeout: float = 15):
        self.fetch = fetch
        self.timeout = timeout

    def label(self, conn) -> str:
        return conn.get("team_name") or conn.get("team") or conn.get("name", "?")

    def inject(self, conn, env: dict) -> None:
        if env.get(MARK) != conn.get("name"):  # what a session of this same account set stays: it is this token
            self.forget(env)
        ref = conn.get("token", "")
        token = read_cli_login(env)[0] if ref == CLI else secrets.resolve(ref, env)  # before the removal below
        env.pop(OVERRIDE, None)
        if token:
            env[OVERRIDE] = token
        env.pop("CONVEX_PROVISION_HOST", None)  # would send the token to another API host
        scrub(env, os.getcwd())
        env[MARK] = conn.get("name", "")

    def forget(self, env: dict) -> None:
        if MARK in env:  # Keyfit put these there for another project
            for var in (OVERRIDE, MARK):
                env.pop(var, None)

    def fingerprint(self, conn, env: dict) -> str:
        """Changes with the token and with the deployment the folder points at, so a cached check never vouches
        for another login or another deployment."""
        seen = f"{env.get(OVERRIDE, '')}\0{json.dumps(target(os.getcwd(), env), sort_keys=True)}"
        return hashlib.sha256(seen.encode()).hexdigest()[:16]

    def local(self, conn, env: dict) -> Check:
        label = self.label(conn)
        ref = conn.get("token")
        if not ref:
            return Check(self.kind, "fail", label, "no token recorded", "keyfit add convex")
        if not conn.get("team"):
            return Check(self.kind, "fail", label, "no expected team recorded", add_command(conn))
        token, fix = env.get(OVERRIDE), fix_for(conn)
        if not token:
            reason = read_cli_login(env)[1] if ref == CLI else f"token not found ({where(conn)})"
            return Check(self.kind, "fail", label, reason, fix)
        problem = shape_problem(token)
        if problem:
            return Check(self.kind, "fail", label, f"{where(conn)} {problem}", fix)
        aim = target(os.getcwd(), env)
        if aim and aim["kind"] == "preview" and aim["team"] != conn["team"]:
            return Check(self.kind, "fail", label, f"{aim['source']} is a preview deploy key of another team "
                         f"({aim['team']}), project expects {label}", f"a preview deploy key of {label}")
        return Check(self.kind, "ok", label, "", pending=True)

    def remote(self, conn, env: dict) -> Check:
        label = self.label(conn)
        token, fix = env[OVERRIDE], fix_for(conn)
        teams, reason, transient = self.teams(token)
        if teams is None:
            return Check(self.kind, "fail", label, reason, "check the network and retry" if transient else fix,
                         transient=transient)
        expected = conn["team"]
        if expected not in teams:
            seen = ", ".join(teams) or "none"
            return Check(self.kind, "fail", label, f"this login has no access to team {label} ({expected}; it sees "
                         f"{seen})", fix if conn.get("token") == CLI else f"{add_command(conn)}   (a token of a member "
                         f"of {label})")
        confirmed = "login and team confirmed"
        aim = target(os.getcwd(), env)
        if aim is None:
            return Check(self.kind, "ok", label, confirmed)
        if aim["kind"] == "local":
            return Check(self.kind, "ok", label, f"{confirmed} · {aim['source']} names local deployment {aim['name']}")
        if aim["kind"] == "self_hosted":
            return Check(self.kind, "ok", label, f"{confirmed} · {aim['source']} names a self-hosted deployment "
                         f"(no Convex team to check)")
        if aim["kind"] == "preview":
            return Check(self.kind, "ok", label, f"{confirmed} · {aim['source']} is a preview deploy key of "
                         f"{aim['team']}/{aim['project']}")
        if aim["kind"] in ("project_key", "unusable"):
            return Check(self.kind, "warn", label, f"{confirmed} · {aim['source']} holds a "
                         f"{'project deploy key' if aim['kind'] == 'project_key' else 'deploy key Convex refuses'}: "
                         f"not checked")
        name, relink = aim["name"], f"npx convex dev --configure existing --team {expected}"
        found, reason, transient = self.deployment(token, name)
        if found is None:
            return Check(self.kind, "fail", label, f"{aim['source']} points this folder at deployment {name}: {reason}",
                         "check the network and retry" if transient else relink, transient=transient)
        if found["team"] != expected:
            return Check(self.kind, "fail", label,
                         f"{aim['source']} points this folder at deployment {name} of another team "
                         f"({teams.get(found['team']) or found['team']}), project expects {label}", relink)
        return Check(self.kind, "ok", label, f"login, team and deployment {name} ({found['project']}) confirmed")

    # helpers, shared with `keyfit add convex` and the shim -------------------------------------------------

    def teams(self, token: str) -> tuple[dict | None, str, bool]:
        """{slug: name} of the teams the token can see, or (None, why, transient)."""
        try:
            status, body = self.fetch("/api/teams", token, self.timeout)
        except (OSError, ValueError) as err:
            return None, _why(err), True
        if status in (401, 403):
            return None, "Convex rejected the token (signed out or revoked?)", False
        if status != 200 or not isinstance(body, list):
            return None, f"Convex answered {status}", _busy(status)
        teams = {}
        for team in body:
            slug = isinstance(team, dict) and team.get("slug")
            if isinstance(slug, str) and slug:
                teams[slug] = team["name"] if isinstance(team.get("name"), str) and team["name"] else slug
        return teams, "", False

    def deployment(self, token: str, name: str) -> tuple[dict | None, str, bool]:
        """{name, team, project} of a cloud deployment the token can reach, or (None, why, transient)."""
        try:
            status, body = self.fetch(f"/api/deployment/{urllib.parse.quote(name, safe='')}/team_and_project", token,
                                      self.timeout)
        except (OSError, ValueError) as err:
            return None, _why(err), True
        if status in (400, 401, 403, 404):  # Convex says DeploymentNotFound for one the token cannot see
            return None, "this login cannot reach it (another team's, or deleted?)", False
        team = isinstance(body, dict) and body.get("team")
        if status != 200 or not isinstance(team, str) or not team:
            return None, f"Convex answered {status}", _busy(status)
        return {"name": name, "team": team, "project": body.get("project") or "?"}, "", False
