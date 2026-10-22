"""Supabase: which Supabase account (access token) a project uses, and the organization its projects live in.

Account:  [accounts.supabase.acme]
          token = "keychain:keyfit/supabase-acme"   (or env:VAR, keychain:service/account)
          org = "<organization slug>"   org_name = "Acme"
Project:  supabase = "acme"

The CLI takes its token from SUPABASE_ACCESS_TOKEN before anything else (the Go CLI and the TypeScript CLI from 2.1xx
alike), then from its own login: the Keychain item "Supabase CLI"/<profile>, the older "Supabase CLI"/access-token,
then ~/.supabase/access-token (or $SUPABASE_HOME/access-token). One login per Mac, so an account here is a token Keyfit
keeps: pasted, or copied once from the CLI's login (`keyfit add supabase --cli`). Newer CLIs store their login through
a native module, and reading it with /usr/bin/security can ask for Keychain access: Keyfit reads it only then.
The CLI works on the project named by --project-ref, else SUPABASE_PROJECT_ID, else supabase/.temp/project-ref
(written by `supabase link`) in its work folder: --workdir, SUPABASE_WORKDIR, else the nearest folder upwards with
supabase/config.toml. The TypeScript CLI's `branches` commands act on the parent project in .temp/linked-project.json,
and its stack mode links through supabase/.link.json instead.

inject  — SUPABASE_ACCESS_TOKEN is the account's token, so every supabase the session starts (`npx supabase` too) and
          Supabase's MCP server use it; SUPABASE_PROFILE=supabase, so neither a --profile-less command nor a profile
          file (supabase/.temp/profile, ~/.supabase/profile) sends it to another API host.
forget  — in a project without a Supabase account, a token Keyfit gave another project's session is removed.
local   — the token is there and shaped like one (sbp_…).
remote  — GET api.supabase.com/v1/organizations must accept the token and list the account's organization; when the
          folder is linked (or SUPABASE_PROJECT_ID is set), GET /v1/projects/<ref> must name the same organization
          for every linked ref (a preview branch is not found there: a warning, not checked).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import urllib.parse
from pathlib import Path

from .. import secrets
from ..checks import Check
from ..registry import canonical, tilde
from .base import Adapter

API = "https://api.supabase.com"
TOKEN = re.compile(r"sbp_(?:oauth_|v0_)?[a-f0-9]{40}")  # the CLI's AccessTokenPattern (use fullmatch)
REF = re.compile(r"[a-z]{20}")  # the CLI's ProjectRefPattern
PROFILE = "supabase"  # the CLI's default profile: api.supabase.com, and the Keychain account of its login
KEYCHAIN = "Supabase CLI"  # Keychain service of the CLI's login
LEGACY = "access-token"  # Keychain account of logins from CLIs before profiles, and the fallback file's name
MARK = "KEYFIT_SUPABASE"  # set next to a token Keyfit injected: the account's name
CONFIG = Path("supabase") / "config.toml"
LINK = Path("supabase") / ".temp" / "project-ref"
PARENT = Path("supabase") / ".temp" / "linked-project.json"  # the linked project's parent: what `branches` acts on
STACK_LINK = Path("supabase") / ".link.json"  # the TypeScript CLI's link in stack mode ([experimental] stack)
OPTIONS = {"--profile", "--project-ref", "--project-id", "--org-id", "--workdir"}
# Options that take a value, of the root command (Go and TypeScript CLI) and of the commands Keyfit looks at; any other
# option is read as a flag without one (--debug, --yes, --linked …), so `supabase --debug login` is still `login`.
VALUED = OPTIONS | {"--output", "-o", "--output-format", "--network-id", "--dns-resolver", "--agent", "--password",
                    "-p", "--db-url", "--token", "--name", "--lang", "--schema", "-s", "--region", "--db-password",
                    "--plan", "--size"}
TOKENS_PAGE = "supabase.com/dashboard/account/tokens"


def _home(env: dict) -> Path:
    return Path(env.get("HOME") or Path.home())


def _token_file(env: dict) -> Path:
    return Path(env["SUPABASE_HOME"]).expanduser() if env.get("SUPABASE_HOME") else _home(env) / ".supabase"


def _decode(value: str) -> str:
    """A value as go-keyring writes it ("go-keyring-base64:…"; older: "go-keyring-encoded:" hex), or plain."""
    import base64
    import binascii

    try:
        if value.startswith("go-keyring-base64:"):
            return base64.b64decode(value[len("go-keyring-base64:"):], validate=True).decode()
        if value.startswith("go-keyring-encoded:"):
            return bytes.fromhex(value[len("go-keyring-encoded:"):]).decode()
    except (binascii.Error, ValueError):
        return ""
    return value


def cli_signed_in(env: dict) -> str:
    """Where the CLI keeps its login ("" when it is not signed in), found without reading the token: a Keychain
    item's attributes need no Keychain access, only its value does."""
    for account in (PROFILE, LEGACY):
        try:
            out = subprocess.run([secrets.SECURITY, "find-generic-password", "-s", KEYCHAIN, "-a", account],
                                 capture_output=True, text=True, timeout=15, check=False)
        except (OSError, subprocess.SubprocessError):
            continue
        if out.returncode == 0:
            return f"Keychain item {KEYCHAIN}/{account}"
    path = _token_file(env) / LEGACY
    return tilde(path) if path.is_file() else ""


def read_cli_login(env: dict, timeout: float = 120) -> tuple[str | None, str]:
    """(token, where) of the CLI's login, or (None, why). macOS may ask to allow Keychain access first (CLIs that
    store through a native module), hence the long timeout: someone is answering a dialog."""
    where = cli_signed_in(env)
    if not where:
        return None, "the supabase CLI is not signed in"
    if not where.startswith("Keychain item "):
        try:
            return _token_file(env).joinpath(LEGACY).read_text().strip() or None, where
        except (OSError, UnicodeDecodeError):
            return None, f"{where} is unreadable"
    account = where.rsplit("/", 1)[1]
    try:
        out = subprocess.run([secrets.SECURITY, "find-generic-password", "-s", KEYCHAIN, "-a", account, "-w"],
                             capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return None, f"no answer to the Keychain's question about {where} within {timeout:g} s"
    except (OSError, subprocess.SubprocessError):
        return None, f"could not run {secrets.SECURITY}"
    if out.returncode != 0:
        return None, f"the Keychain did not give out {where} (access denied?)"
    return _decode(out.stdout.strip()).strip() or None, where


def add_command(conn) -> str:
    return f"keyfit add supabase --name {conn.get('name', '<name>')}"


def fix_for(conn) -> str:
    """How to give the account a working token."""
    ref = conn.get("token", "")
    if ref and not ref.startswith("keychain:"):
        return f"set {secrets.describe(ref)} to a token from {TOKENS_PAGE}"
    return f"{add_command(conn)}   (paste a token from {TOKENS_PAGE}, or add --cli for the supabase CLI's login)"


def workdir(start: Path, env: dict) -> Path:
    """The folder the CLI works in: SUPABASE_WORKDIR, else the nearest folder upwards with supabase/config.toml,
    else *start* itself (the CLI's getProjectRoot)."""
    if env.get("SUPABASE_WORKDIR"):
        return (Path(start) / Path(env["SUPABASE_WORKDIR"]).expanduser()).resolve()
    start = canonical(start)
    for folder in (start, *start.parents):
        if (folder / CONFIG).is_file():
            return folder
    return start


def _json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
    except (OSError, UnicodeDecodeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def linked_refs(start, env: dict) -> list[tuple[str, str]]:
    """Every project the CLI could use from *start* without --project-ref, and where each comes from:
    SUPABASE_PROJECT_ID, else the work folder's link. That is supabase/.temp/project-ref (`supabase link`) with the
    parent project in .temp/linked-project.json, which the TypeScript CLI's `branches` commands act on, or in that
    CLI's stack mode supabase/.link.json (the active branch and its project). Keyfit reads them all, whatever the
    mode. [] when not linked."""
    if env.get("SUPABASE_PROJECT_ID", "").strip():
        return [(env["SUPABASE_PROJECT_ID"].strip(), "$SUPABASE_PROJECT_ID")]
    folder = workdir(Path(start), env)
    try:
        found = [((folder / LINK).read_text(), LINK)]
    except (OSError, UnicodeDecodeError):
        found = []
    if found and found[0][0].strip():  # the CLI reads the parent only next to a linked ref
        found.append((_json(folder / PARENT).get("ref"), PARENT))
    stack = _json(folder / STACK_LINK)
    for part in (stack.get("active_branch"), stack.get("project")):
        found.append((part.get("ref") if isinstance(part, dict) else None, STACK_LINK))
    refs = []
    for ref, rel in found:
        if isinstance(ref, str) and ref.strip() and ref.strip() not in (seen for seen, _ in refs):
            refs.append((ref.strip(), tilde(folder / rel)))
    return refs


def linked_ref(start, env: dict) -> tuple[str | None, str]:
    """The first of linked_refs: the project the CLI would use, and where that comes from. (None, "") when not
    linked."""
    refs = linked_refs(start, env)
    return refs[0] if refs else (None, "")


def options(args: list[str]) -> list[tuple[str, str | None]]:
    """(name, value) of each option that picks a login, an organization, a project or the work folder, read like the
    CLI's parser (cobra): `--name value` and `--name=value`, anywhere before `--`."""
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
    return f"Supabase did not answer ({getattr(err, 'reason', None) or type(err).__name__})"


class SupabaseAdapter(Adapter):
    kind = "supabase"

    def __init__(self, fetch=_fetch, timeout: float = 15):
        self.fetch = fetch
        self.timeout = timeout

    def label(self, conn) -> str:
        return conn.get("org_name") or conn.get("org") or conn.get("name", "?")

    def inject(self, conn, env: dict) -> None:
        if env.get(MARK) != conn.get("name"):  # what a session of this same account set stays: it is this token
            self.forget(env)
        token = secrets.resolve(conn.get("token", ""), env)  # before the removal: env:SUPABASE_ACCESS_TOKEN works too
        env.pop("SUPABASE_ACCESS_TOKEN", None)
        if token:
            env["SUPABASE_ACCESS_TOKEN"] = token
        env["SUPABASE_PROFILE"] = PROFILE
        env[MARK] = conn.get("name", "")

    def forget(self, env: dict) -> None:
        if MARK in env:  # Keyfit put these there for another project
            for var in ("SUPABASE_ACCESS_TOKEN", "SUPABASE_PROFILE", MARK):
                env.pop(var, None)

    def fingerprint(self, conn, env: dict) -> str:
        """Changes with the token and with the project the folder is linked to, so a cached check never vouches
        for another token or another linked project."""
        refs = "\0".join(ref for ref, _ in linked_refs(os.getcwd(), env))
        seen = f"{env.get('SUPABASE_ACCESS_TOKEN', '')}\0{refs}"
        return hashlib.sha256(seen.encode()).hexdigest()[:16]

    def local(self, conn, env: dict) -> Check:
        label = self.label(conn)
        ref = conn.get("token")
        if not ref:
            return Check(self.kind, "fail", label, "no token recorded", "keyfit add supabase")
        if not conn.get("org"):
            return Check(self.kind, "fail", label, "no expected organization recorded", add_command(conn))
        where, fix = secrets.describe(ref), fix_for(conn)
        token = env.get("SUPABASE_ACCESS_TOKEN")
        if not token:
            return Check(self.kind, "fail", label, f"token not found ({where})", fix)
        half = len(token) // 2
        if len(token) % 2 == 0 and token[:half] == token[half:]:
            return Check(self.kind, "fail", label, f"the token in {where} looks pasted twice", fix)
        if not TOKEN.fullmatch(token):
            return Check(self.kind, "fail", label, f"{where} does not hold a Supabase access token (sbp_…)", fix)
        return Check(self.kind, "ok", label, "", pending=True)

    def remote(self, conn, env: dict) -> Check:
        label = self.label(conn)
        token = env["SUPABASE_ACCESS_TOKEN"]
        orgs, reason, transient = self.organizations(token)
        if orgs is None:
            return Check(self.kind, "fail", label, reason, "check the network and retry" if transient else fix_for(conn),
                         transient=transient)
        expected = conn["org"]
        title = conn.get("org_name") or expected
        if expected not in orgs:
            return Check(self.kind, "fail", label, f"this token has no access to organization {title} ({expected})",
                         f"{add_command(conn)}   (a token of a member of {title})")
        refs = [(ref, source) for ref, source in linked_refs(os.getcwd(), env) if REF.fullmatch(ref)]
        if not refs:  # not linked, or a ref the CLI itself refuses
            return Check(self.kind, "ok", label, "token and organization confirmed")
        relink = f"supabase link   (a project of {title})"
        unchecked = []
        for ref, source in refs:
            project, reason, transient = self.project(token, ref)
            if project is None and reason == "not found":
                unchecked.append((ref, source))
                continue
            if project is None:
                return Check(self.kind, "fail", label, f"{source} links this folder to project {ref}: {reason}",
                             "check the network and retry" if transient else relink, transient=transient)
            if project["org"] != expected:
                return Check(self.kind, "fail", label,
                             f"{source} links this folder to project {ref} of another organization "
                             f"({orgs.get(project['org']) or project['org']}), project expects {title}", relink)
        if unchecked:
            ref, source = unchecked[0]
            return Check(self.kind, "warn", label, f"token and organization confirmed · {source} names {ref}, which "
                         f"this token does not find as a project (a preview branch?): not checked")
        return Check(self.kind, "ok", label,
                     f"token, organization and linked project {', '.join(ref for ref, _ in refs)} confirmed")

    # helpers, shared with `keyfit add supabase` and the shim -------------------------------------------------

    def organizations(self, token: str) -> tuple[dict | None, str, bool]:
        """{slug: name} of the organizations the token can see, or (None, why, transient)."""
        try:
            status, body = self.fetch("/v1/organizations", token, self.timeout)
        except (OSError, ValueError) as err:
            return None, _why(err), True
        if status in (401, 403):
            return None, "Supabase rejected the token (deleted or expired?)", False
        if status != 200 or not isinstance(body, list):
            return None, f"Supabase answered {status}", _busy(status)
        orgs = {}
        for org in body:
            slug = isinstance(org, dict) and (org.get("slug") or org.get("id"))  # id: older name of the slug
            if isinstance(slug, str) and slug:
                orgs[slug] = org["name"] if isinstance(org.get("name"), str) and org["name"] else slug
        return orgs, "", False

    def project(self, token: str, ref: str) -> tuple[dict | None, str, bool]:
        """{ref, org, name} of a project the token can reach, or (None, why, transient); why is "not found" for a
        ref Supabase does not know as a project for this token (a preview branch, or someone else's project)."""
        try:
            status, body = self.fetch("/v1/projects/" + urllib.parse.quote(ref, safe=""), token, self.timeout)
        except (OSError, ValueError) as err:
            return None, _why(err), True
        if status == 404:
            return None, "not found", False
        if status in (401, 403):
            return None, "this token cannot reach that project", False
        org = isinstance(body, dict) and (body.get("organization_slug") or body.get("organization_id"))
        if status != 200 or not isinstance(org, str) or not org:
            return None, f"Supabase answered {status}", _busy(status)
        return {"ref": ref, "org": org, "name": body.get("name") or ref}, "", False
