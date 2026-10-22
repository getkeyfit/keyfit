"""Google Workspace MCP servers: the Gmail, Drive and Calendar servers that run on the Mac with a login of their own,
each signed in as one Google account and owned by the projects of that account.

Account:  [accounts.google-mcp.work.servers.gmail_work]   (one table per server, by its name in the agents' configs)
          type = "gmail-autoauth"                          (one of TYPES)
          login = "~/.gmail-mcp/work.json"                 (the login file, or folder, the server reads)
Project:  google-mcp = "work"   and   google = "work"   (the project's Google account: its email reaches this connection
          as `account`, see registry.GOOGLE_TOOLS)

The servers (their packages were read at the versions named; none was run):
  workspace-mcp        PyPI workspace-mcp 2.1.0: a folder, WORKSPACE_MCP_CREDENTIALS_DIR (else
                       GOOGLE_MCP_CREDENTIALS_DIR, else ~/.google_workspace_mcp/credentials), with one <email>.json per
                       account, named after the email Google gave at sign-in. A call may name any of them
                       (user_google_email), so the folder must hold the project's account only. Its tokens carry
                       userinfo.email: tokeninfo names the account. Each file holds its OAuth client too.
                       GOOGLE_SERVICE_ACCOUNT_KEY_FILE or _JSON make it a service account acting as any user of its
                       domain.
  gmail-autoauth       npm @gongrzhe/server-gmail-autoauth-mcp 1.1.11: GMAIL_CREDENTIALS_PATH (else
                       ~/.gmail-mcp/credentials.json): tokens with Gmail scopes only and no email. Gmail's profile names
                       the account while the saved token is valid, and the server never saves a renewed one: that is
                       the hour after its sign-in. Its OAuth client: GMAIL_OAUTH_PATH, else
                       ~/.gmail-mcp/gcp-oauth.keys.json ("installed" or "web").
  google-calendar-mcp  npm @cocal/google-calendar-mcp 2.7.0: GOOGLE_CALENDAR_MCP_TOKEN_PATH (else
                       $XDG_CONFIG_HOME/google-calendar-mcp/tokens.json): accounts by nickname, all usable by a call,
                       each with the email the server noted once it used it (cached_email). The primary calendar's ID
                       names the account. Its OAuth client: GOOGLE_OAUTH_CREDENTIALS (else a file inside the
                       package, which Keyfit does not look for). GOOGLE_SERVICE_ACCOUNT_KEY (+ _SUBJECT), and
                       GOOGLE_APPLICATION_CREDENTIALS when it has no client file, make it a service account.
  google-drive-mcp     npm @piotr-agier/google-drive-mcp 2.13.0: GOOGLE_DRIVE_MCP_TOKEN_PATH (else
                       $XDG_CONFIG_HOME/google-drive-mcp/tokens.json): {version: 2, accounts: {alias: {email, …}}}, all
                       usable by a call. Its tokens carry userinfo.email: tokeninfo names the account. Its OAuth
                       client: GOOGLE_DRIVE_OAUTH_CREDENTIALS, else gcp-oauth.keys.json in
                       $XDG_CONFIG_HOME/google-drive-mcp. Ahead of its login: GOOGLE_APPLICATION_CREDENTIALS
                       (+ GOOGLE_DRIVE_MCP_SUBJECT), a service account, then GOOGLE_DRIVE_MCP_ACCESS_TOKEN or
                       _REFRESH_TOKEN (+ _CLIENT_ID, _CLIENT_SECRET). Into a missing login it moves an old token file:
                       GOOGLE_TOKEN_PATH, else google-tokens.json or .gcp-saved-tokens.json in the folder it starts in.
  other                any other server: one JSON token file, given with --login; the email from its id_token (or an
                       email field), and online from tokeninfo when its scopes include the email. Its OAuth client
                       only if the file holds one (client_id and client_secret).

inject  — removes inherited variables that would point a server at another login (TYPES' path variables and
          USER_GOOGLE_EMAIL) or sign it in some other way (the sources of the types the connection's servers run), so
          each server reads what its definition says, which is what Keyfit checked.
local   — every definition of a claimed server in the five agents' configs (yours and the folder's) runs its type,
          reads the recorded login (a default one under the HOME and XDG_CONFIG_HOME it sets) and sets none of its
          type's sources, and every account in that login is the project's Google account. Only labels are read (file
          names and emails), never a token. Not signed in yet is a warning, not a block: no account can be wrong (unless
          Google Drive MCP would take an old token file from the folder instead).
remote  — Google (tokeninfo with the token in the request body, else Gmail's profile or the primary calendar with it in a
          header) must name the project's account: asked with the saved access token while it is valid, else with a
          fresh one renewed as the server itself would (TOKEN, pinned: a token_uri in a file is never followed; the
          refresh token and the server's own OAuth client in the request body: named in your own configs, never the
          folder's, and read only from a regular file of at most CLIENT_MAX bytes). The fresh token is used for that
          one question and dropped; nothing is written to the login, and the refresh token, client secret and fresh
          token are never kept, shown or cached. The only place Keyfit uses a refresh token. Without a refresh token or
          client, the local check decides, and a login that names no account is reported as not confirmed — a
          warning, shown when an agent starts.
mcp     — the claimed servers belong to the project (Project.mcp_claims), so every other project's sessions do not see
          them; in a project with this connection every other Google Workspace server is hidden (mcp.hidden).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import stat
import time
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .. import mcp
from ..checks import Check, printable
from ..registry import canonical, tilde
from .base import Adapter
from .gcloud import TOKENINFO
from .gemini import _claims
from .google import EMAIL, same

KIND = "google-mcp"
TOKEN = "https://oauth2.googleapis.com/token"  # where every one of the servers renews its token (google-auth)
GMAIL_PROFILE = "https://gmail.googleapis.com/gmail/v1/users/me/profile"
PRIMARY_CALENDAR = "https://www.googleapis.com/calendar/v3/calendars/primary"
NAME = re.compile(r"[A-Za-z0-9_-]{1,64}")  # every agent can hide it (Codex: no dot or "="; Gemini CLI: no comma)
EXPIRY_MARGIN = 60  # seconds: a token about to expire is treated as expired
NO_REFRESH = "its saved token has expired, with no refresh token to renew it"
NO_CLIENT = "its saved token has expired, with no OAuth client found to renew it"
ERROR = re.compile(r"[a-z_]{1,40}")  # an OAuth error code, safe to show
CLIENT_MAX = 64 * 1024  # bytes: an OAuth client file (gcp-oauth.keys.json) holds under 1 KB
DRIVE_LEGACY = ("google-tokens.json", ".gcp-saved-tokens.json")  # Google Drive MCP's old token files, in its folder
VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}"  # ${VAR}, ${VAR:-default}, $VAR, {env:VAR}
                 r"|\$([A-Za-z_][A-Za-z0-9_]*)|\{env:([A-Za-z_][A-Za-z0-9_]*)\}")


@dataclass(frozen=True)
class Type:
    title: str
    variables: tuple[str, ...] = ()  # where the server takes its login from, strongest first
    default: tuple[str, ...] = ()  # the login's place without them: under the home folder, or XDG_CONFIG_HOME (config)
    config: bool = False
    folder: bool = False  # a folder of <email>.json files rather than one file
    whoami: str = TOKENINFO  # what names the account of a valid token, and in which field
    field: str = "email"
    sign_in: str = ""  # its own sign-in command
    client: tuple[str, ...] = ()  # the variables naming its OAuth client file; without them, the login holds the client
    client_default: tuple[str, ...] = ()  # the client file's place without them (under home, or XDG_CONFIG_HOME: config)
    # variables that make it sign in some other way than with its login (a service account, a token of its own):
    # removed from sessions, refused in a definition
    sources: tuple[str, ...] = ()


TYPES = {
    "workspace-mcp": Type("Google Workspace MCP", ("WORKSPACE_MCP_CREDENTIALS_DIR", "GOOGLE_MCP_CREDENTIALS_DIR"),
                          (".google_workspace_mcp", "credentials"), folder=True,
                          sources=("GOOGLE_SERVICE_ACCOUNT_KEY_FILE", "GOOGLE_SERVICE_ACCOUNT_KEY_JSON")),
    "gmail-autoauth": Type("Gmail AutoAuth MCP", ("GMAIL_CREDENTIALS_PATH",), (".gmail-mcp", "credentials.json"),
                           whoami=GMAIL_PROFILE, field="emailAddress",
                           sign_in="npx @gongrzhe/server-gmail-autoauth-mcp auth", client=("GMAIL_OAUTH_PATH",),
                           client_default=(".gmail-mcp", "gcp-oauth.keys.json")),
    "google-calendar-mcp": Type("Google Calendar MCP", ("GOOGLE_CALENDAR_MCP_TOKEN_PATH",),
                                ("google-calendar-mcp", "tokens.json"), config=True, whoami=PRIMARY_CALENDAR,
                                field="id", sign_in="npx @cocal/google-calendar-mcp auth",
                                client=("GOOGLE_OAUTH_CREDENTIALS",),
                                sources=("GOOGLE_SERVICE_ACCOUNT_KEY", "GOOGLE_SERVICE_ACCOUNT_SUBJECT",
                                         "GOOGLE_APPLICATION_CREDENTIALS")),
    "google-drive-mcp": Type("Google Drive MCP", ("GOOGLE_DRIVE_MCP_TOKEN_PATH",), ("google-drive-mcp", "tokens.json"),
                             config=True, sign_in="npx @piotr-agier/google-drive-mcp auth",
                             client=("GOOGLE_DRIVE_OAUTH_CREDENTIALS",),
                             client_default=("google-drive-mcp", "gcp-oauth.keys.json"),
                             sources=("GOOGLE_APPLICATION_CREDENTIALS", "GOOGLE_DRIVE_MCP_SUBJECT",
                                      "GOOGLE_DRIVE_MCP_ACCESS_TOKEN", "GOOGLE_DRIVE_MCP_REFRESH_TOKEN",
                                      "GOOGLE_DRIVE_MCP_CLIENT_ID", "GOOGLE_DRIVE_MCP_CLIENT_SECRET",
                                      "GOOGLE_TOKEN_PATH")),
    "other": Type("a Google server"),
}
SCRUB = ("USER_GOOGLE_EMAIL", *(var for kind in TYPES.values() for var in kind.variables))


def servers(conn) -> list[tuple[str, dict]]:
    """(name, record) of the servers a connection claims."""
    table = conn.get("servers") if isinstance(conn, dict) else None
    return [(n, s) for n, s in table.items() if isinstance(s, dict)] if isinstance(table, dict) else []


def sources(conn) -> tuple[str, ...]:
    """The sources (Type.sources) of the types a connection's servers run; every type's when one is "other" or unknown,
    since its definitions may run any of them."""
    kinds = {server.get("type") for _, server in servers(conn)}
    if not kinds <= TYPES.keys() - {"other"}:
        kinds = set(TYPES)
    return tuple(dict.fromkeys(var for kind in TYPES if kind in kinds for var in TYPES[kind].sources))


def _path(value: str, env: dict, cwd: Path) -> Path:
    """A path from an MCP definition: its variables (${VAR}, $VAR, OpenCode's {env:VAR}) and ~ filled in from *env*."""
    value = VAR.sub(lambda m: env.get(m.group(1) or m.group(3) or m.group(4)) or (m.group(2) or ""), value)
    if value == "~" or value.startswith("~/"):
        value = str(Path(env.get("HOME") or Path.home())) + value[1:]
    path = Path(value)
    return canonical(path if path.is_absolute() else cwd / path)


def _default(kind: str, parts: tuple[str, ...], env: dict) -> Path | None:
    if not parts:
        return None
    home = Path(env.get("HOME") or Path.home())
    base = Path(env.get("XDG_CONFIG_HOME") or home / ".config") if TYPES[kind].config else home
    return canonical(base.joinpath(*parts))


def default_login(kind: str, env: dict) -> Path | None:
    """Where a server of *kind* keeps its login when its definition names none."""
    return _default(kind, TYPES[kind].default, env)


def _server_env(spec: dict, env: dict, cwd: Path) -> dict:
    """The session's environment as a server of this definition finds its default places: with the HOME and
    XDG_CONFIG_HOME its definition sets."""
    own = mcp.spec_env(spec)
    return {**env, **{var: str(_path(own[var], env, cwd)) for var in ("HOME", "XDG_CONFIG_HOME") if own.get(var)}}


def login_of(kind: str, spec: dict, env: dict, cwd: Path) -> Path | None:
    """The login a definition of a *kind* server reads: the variable its definition sets, else the type's default."""
    own = mcp.spec_env(spec)
    for var in TYPES[kind].variables:
        if own.get(var):
            return _path(own[var], env, cwd)
    return default_login(kind, _server_env(spec, env, cwd))


def recorded(server: dict) -> Path | None:
    login = server.get("login")
    return canonical(login) if isinstance(login, str) and login else None


def found(env: dict, cwd: Path) -> dict[str, list[tuple[str, str, Path | None]]]:
    """The local Google Workspace servers of the agents' configs, by name: [(agent, type, login it reads)]."""
    out: dict[str, list] = {}
    for agent, name, spec in mcp.definitions(env, cwd):
        kind = mcp.google_type(spec)
        if kind in TYPES:
            out.setdefault(name, []).append((agent, kind, login_of(kind, spec, env, cwd)))
    return out


def _when(value) -> float | None:
    """An expiry in seconds since 1970: milliseconds (the Node servers) or an ISO time (Python's, UTC when no zone)."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value / 1000
    if isinstance(value, str):
        try:
            moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return (moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)).timestamp()
    return None


def _first(data: dict, *keys) -> str | None:
    return next((data[k] for k in keys if isinstance(data.get(k), str) and data[k]), None)


def _record(data: dict, email: str | None = None) -> dict | None:
    """One sign-in, from a token record in any of the shapes these servers write: {"email" (None when the record names
    no account), "token" (the access token), "expiry", "refresh" (the refresh token) and "client" (the OAuth client
    the record holds: workspace-mcp's, (ID, secret) or None)}; None when it holds no token. The tokens and the client
    are for the remote check only, held in memory while it runs."""
    token, refresh = _first(data, "access_token", "token", "accessToken"), _first(data, "refresh_token", "refreshToken")
    if token is None and refresh is None:
        return None
    if email is None:
        named = (data.get("email"), data.get("cached_email"), _claims(data.get("id_token")).get("email"))
        email = next((e for e in named if isinstance(e, str) and EMAIL.fullmatch(e)), None)
    expiry = next((data[k] for k in ("expiry_date", "expiryDate", "expiry") if data.get(k) is not None), None)
    client = _first(data, "client_id"), _first(data, "client_secret")
    return {"email": email if email and EMAIL.fullmatch(email) else None, "token": token, "expiry": _when(expiry),
            "refresh": refresh, "client": client if all(client) else None}


def read(kind: str, login: Path) -> tuple[list[dict] | None, str]:
    """The sign-ins a login holds ([] when there is none yet), or None and why it cannot be read."""
    if TYPES[kind].folder:
        if not login.is_dir():
            return [], ""
        entries = []
        for path in sorted(login.glob("*.json")):
            email = urllib.parse.unquote(path.stem)
            if "@" not in email:  # oauth_states.json and the like
                continue
            try:
                data = json.loads(path.read_text())
            except (OSError, UnicodeDecodeError, ValueError):
                return None, f"{tilde(path)} is unreadable"
            entry = _record(data, email) if isinstance(data, dict) else None
            if entry:
                entries.append(entry)
        return entries, ""
    try:
        data = json.loads(login.read_text())
    except FileNotFoundError:
        return [], ""
    except (OSError, UnicodeDecodeError, ValueError):
        return None, f"{tilde(login)} is unreadable"
    if not isinstance(data, dict):
        return None, f"{tilde(login)} is unreadable"
    records = [data]
    if kind == "google-calendar-mcp" and _record(data) is None:  # accounts by nickname, unless an old single one
        records = [r for r in data.values() if isinstance(r, dict)]
    elif kind == "google-drive-mcp" and data.get("version") == 2 and isinstance(data.get("accounts"), dict):
        records = [r for r in data["accounts"].values() if isinstance(r, dict)]
    return [entry for entry in map(_record, records) if entry], ""


def who(kind: str, login: Path | None) -> str:
    """What a login says about its accounts, for messages: their emails, that it names none, or no sign-in yet."""
    entries, reason = read(kind, login) if login else (None, "no login")
    if entries is None:
        return reason
    named = sorted({e["email"].lower() for e in entries if e["email"]})
    if not entries:
        return "not signed in yet"
    if not named:
        return "names no account"
    return ", ".join(named) + (" and an account it does not name" if any(not e["email"] for e in entries) else "")


def _valid(entry: dict) -> str | None:
    """The sign-in's access token while it is still valid (never stored or shown)."""
    expiry = entry.get("expiry")
    return entry["token"] if entry.get("token") and expiry and expiry > time.time() + EXPIRY_MARGIN else None


def sign_in(name: str, kind: str, login: Path, account: str, env: dict) -> str:
    """How *name* signs in, as the project's account, into its recorded login."""
    t = TYPES[kind]
    if kind == "workspace-mcp":
        return f"sign in as {account} from an agent session here (ask it to run {name}'s start_google_auth tool)"
    if not t.sign_in:
        return f"sign {name} in as {account} with its own sign-in"
    var = "" if login == default_login(kind, env) else f"{t.variables[0]}={shlex.quote(str(login))} "
    extra = "   (with GOOGLE_OAUTH_CREDENTIALS as in its config)" if kind == "google-calendar-mcp" else ""
    return f"{var}{t.sign_in}{extra}   (as {account})"


def own_login(name: str, kind: str, account: str) -> str:
    """The fix when a login holds another account: the server gets a login of its own (the other one may be another
    project's, so it is never removed)."""
    t = TYPES[kind]
    where = f"{t.variables[0]} in its config to a new {'folder' if t.folder else 'file'}" if t.variables else \
        "a new login"
    add = f"keyfit add google-mcp --server {name}" + ("" if t.variables else " --login <it>")
    return f"give {name} a login of its own for {account} (set {where}), sign in there, then: {add}"


def _small_file(path: Path, limit: int) -> bytes | None:
    """A regular file's bytes when it holds at most *limit*, else None: a FIFO, a device or a huge file never holds up
    a check (it is opened without waiting, and checked again once open)."""
    try:
        if not stat.S_ISREG(os.stat(path).st_mode):
            return None
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    except (OSError, ValueError):
        return None
    try:
        with os.fdopen(fd, "rb") as file:
            info = os.fstat(file.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
                return None
            data = file.read(limit + 1)
    except OSError:
        return None
    return data if len(data) <= limit else None


def client_file(path: Path) -> tuple[str, str] | None:
    """The OAuth client (ID, secret) in a gcp-oauth.keys.json, as the servers read it: under "installed", else "web",
    else at the top. Only a regular file of at most CLIENT_MAX bytes is read."""
    raw = _small_file(path, CLIENT_MAX)
    try:
        data = json.loads(raw.decode()) if raw is not None else None
    except ValueError:  # also UnicodeDecodeError
        return None
    if not isinstance(data, dict):
        return None
    part = next((data[k] for k in ("installed", "web") if k in data), data)
    client = (_first(part, "client_id"), _first(part, "client_secret")) if isinstance(part, dict) else (None, None)
    return client if all(client) else None


def client_of(name: str, kind: str, env: dict, cwd: Path) -> tuple[str, str] | None:
    """The OAuth client a server of *kind* renews its token with: the file its variable names (in your own definition of
    *name*, never one a repository brings, else in the session's environment), else the server's default file."""
    t = TYPES[kind]
    mine = [spec for _, defined, spec in mcp.definitions(env, cwd, mine=True) if defined == name]
    for spec in mine:
        own = mcp.spec_env(spec)
        var = next((v for v in t.client if own.get(v)), None)
        if var:
            return client_file(_path(own[var], env, cwd))
    var = next((v for v in t.client if env.get(v)), None)
    path = _path(env[var], env, cwd) if var else \
        _default(kind, t.client_default, _server_env(mine[0] if mine else {}, env, cwd))
    return client_file(path) if path else None


def _fetch(url: str, token: str, timeout: float) -> tuple[int, object]:
    from .gcloud import _fetch as fetch  # tokeninfo gets the token in the body, the others in a header

    return fetch(url, token, timeout)


def _renew(refresh: str, client: tuple[str, str], timeout: float) -> tuple[int, object]:
    """Google's answer to the request each of the servers makes to renew its token (google-auth's refresh grant): the
    refresh token and the client in the body of a POST to TOKEN, which is pinned. Its error answers are read too
    ({"error": "invalid_grant"} when the sign-in was signed out or revoked)."""
    import urllib.error  # only when a check goes online
    import urllib.request

    from .. import http

    body = urllib.parse.urlencode({"grant_type": "refresh_token", "refresh_token": refresh, "client_id": client[0],
                                   "client_secret": client[1]}).encode()
    req = urllib.request.Request(TOKEN, data=body, method="POST", headers={
        "User-Agent": "keyfit", "Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"})
    try:
        with http.urlopen(req, timeout) as resp:
            return resp.status, json.loads(resp.read() or b"null")
    except urllib.error.HTTPError as err:
        try:
            return err.code, json.loads(err.read() or b"null")
        except (OSError, ValueError):
            return err.code, None


class GoogleMcpAdapter(Adapter):
    kind = KIND

    def __init__(self, fetch=_fetch, renew=_renew, timeout: float = 15):
        self.fetch = fetch
        self.renew = renew
        self.timeout = timeout

    def label(self, conn) -> str:
        return ", ".join(name for name, _ in servers(conn)) or "no servers"

    def inject(self, conn, env: dict) -> None:
        for var in (*SCRUB, *sources(conn)):
            env.pop(var, None)

    def fingerprint(self, conn, env: dict) -> str:
        """Changes with every login file (a new sign-in, a renewed token): a cached check never vouches for another."""
        seen = []
        for name, server in servers(conn):
            login = recorded(server)
            if login is None:
                continue
            for path in sorted(login.glob("*.json")) if login.is_dir() else [login]:
                try:
                    st = path.stat()
                except OSError:
                    continue
                seen.append(f"{name}:{path.name}:{st.st_mtime_ns}:{st.st_size}")
        return hashlib.sha256("|".join(seen).encode()).hexdigest()[:16]

    def local(self, conn, env: dict) -> Check:
        return self.check_local(conn, env, Path.cwd())

    def check_local(self, conn, env: dict, cwd: Path) -> Check:
        label = self.label(conn)
        account = conn.get("account")
        if not isinstance(account, str) or not EMAIL.fullmatch(account):
            return Check(KIND, "fail", label, "the project has no Google account",
                         "keyfit add google --email <the project's Google account>")
        table = servers(conn)
        if not table:
            return Check(KIND, "fail", label, "no servers recorded", "keyfit add google-mcp --server <name>")
        definitions = mcp.definitions(env, cwd)
        results = [self._one(name, server, account, env, cwd, definitions) for name, server in table]
        several = len(results) > 1
        for name, result in zip((n for n, _ in table), results):
            if result.failed:
                result.label = label
                result.detail = f"{name}: {result.detail}" if several else result.detail
                return result
        warned = [(n, r) for n, r in zip((n for n, _ in table), results) if r.status == "warn"]
        if warned:
            detail = "; ".join(f"{n}: {r.detail}" if several else r.detail for n, r in warned)
            return Check(KIND, "warn", label, detail, warned[0][1].fix, pending=any(r.pending for r in results))
        named = all(r.detail == account for r in results)
        return Check(KIND, "ok", label, account if named else "", pending=True)

    def _one(self, name: str, server: dict, account: str, env: dict, cwd: Path, definitions) -> Check:
        """The local check of one server: its definitions, then its login."""
        kind, login = server.get("type"), recorded(server)

        def fail(detail: str, fix: str) -> Check:
            return Check(KIND, "fail", name, detail, fix)

        if kind not in TYPES:
            return fail(f"unknown server type {printable(kind)}", f"keyfit add google-mcp --server {name}")
        if login is None:
            return fail("no login recorded", f"keyfit add google-mcp --server {name}")
        for agent, defined, spec in definitions:
            if defined == name:
                problem = self._definition(name, kind, login, spec, account, env, cwd, agent)
                if problem:
                    return fail(*problem)
        entries, reason = read(kind, login)
        if entries is None:
            return fail(reason, sign_in(name, kind, login, account, env))
        if not entries:
            old = next((cwd / n for n in DRIVE_LEGACY if (cwd / n).exists()), None) \
                if kind == "google-drive-mcp" and not login.exists() else None
            if old:  # Google Drive MCP would move it into the missing login and act as its account
                return fail(f"not signed in yet ({tilde(login)}), and {name} would take the sign-in in {tilde(old)}",
                            f"move {tilde(old)} out of this folder, then: {sign_in(name, kind, login, account, env)}")
            return Check(KIND, "warn", name, f"not signed in yet ({tilde(login)})",
                         sign_in(name, kind, login, account, env))
        others = sorted({e["email"].lower() for e in entries if e["email"] and not same(e["email"], account)})
        if others:
            mine = any(same(e["email"], account) for e in entries)
            said = f"{tilde(login)} also holds {', '.join(others)}, which {name} can act as" if mine else \
                f"{tilde(login)} is signed in as {', '.join(others)}, the project's Google account is {account}"
            return fail(said, own_login(name, kind, account))
        return Check(KIND, "ok", name, account if all(e["email"] for e in entries) else "", pending=True)

    def _definition(self, name: str, kind: str, login: Path, spec: dict, account: str, env: dict, cwd: Path,
                    agent: str) -> tuple[str, str] | None:
        """What is wrong with one definition of a claimed server, as (detail, fix), or None: it must run the recorded
        type, read the recorded login and set none of its type's sources, so the login Keyfit checks is the one the
        server uses."""
        again = f"keyfit add google-mcp --server {name}"
        where = f"in {agent}'s MCP config"
        runs = mcp.google_type(spec)
        if runs == mcp.GOOGLE_REMOTE or (spec.get("url") or spec.get("httpUrl")) and runs is None:
            return (f"{agent}'s {name} is a remote server (its sign-in is {agent}'s own), not one reading "
                    f"{tilde(login)}", f"give one of them another name {where}: {name} must be one server everywhere")
        if runs in TYPES and kind != "other" and runs != kind:
            return (f"{agent}'s {name} is {TYPES[runs].title}, not {TYPES[kind].title}",
                    f"{again}   (records what it is)")
        effective = runs if runs in TYPES else kind
        if effective == "other":
            return None  # Keyfit knows nothing about where it reads its login
        reads = login_of(effective, spec, env, cwd)
        if reads != login:
            var = TYPES[effective].variables[0]
            return (f"{agent}'s {name} reads {tilde(reads)}, not {tilde(login)}",
                    f"set {var}={tilde(login)} in {name}'s env {where}, or: {again}")
        own = mcp.spec_env(spec)
        source = next((var for var in TYPES[effective].sources if own.get(var)), None)
        if source:
            return (f"{agent}'s {name} sets {source}: {TYPES[effective].title} would sign in with it, not with "
                    f"{tilde(login)}", f"remove {source} from {name}'s env {where}")
        user = mcp.spec_env(spec).get("USER_GOOGLE_EMAIL") if effective == "workspace-mcp" else None
        if user and not same(user, account):
            return (f"{agent}'s {name} sets USER_GOOGLE_EMAIL={printable(user)}, the project's Google account is "
                    f"{account}", f"set USER_GOOGLE_EMAIL={account} in {name}'s env {where}, or remove it")
        return None

    def remote(self, conn, env: dict) -> Check:
        return self.check_remote(conn, env, Path.cwd())

    def check_remote(self, conn, env: dict, cwd: Path) -> Check:
        label, account = self.label(conn), conn["account"]
        table = servers(conn)
        notes, fixes, status = [], [], "ok"
        for name, server in table:
            kind, login = server["type"], recorded(server)
            entries, _ = read(kind, login)
            if not entries:
                continue  # the local check said why
            result = self._confirm(name, kind, login, entries, account, env, cwd)
            if result.failed:
                result.label = label
                result.detail = f"{name}: {result.detail}" if len(table) > 1 else result.detail
                return result
            if result.status == "warn":
                status = "warn"
                fixes.append(result.fix)
            notes.append((name, result.detail))
        if all(note == "confirmed by Google" for _, note in notes):
            return Check(KIND, status, label, "confirmed by Google" if notes else "")
        detail = "; ".join(f"{n}: {note}" if len(notes) > 1 else note for n, note in notes)
        return Check(KIND, status, label, detail, fixes[0] if fixes else "")

    def _confirm(self, name: str, kind: str, login: Path, entries: list[dict], account: str, env: dict,
                 cwd: Path) -> Check:
        """Ask Google about every sign-in of one login: with its saved token while that is valid, else with a fresh
        one renewed from its refresh token (used for this one question, then dropped)."""
        t = TYPES[kind]
        unconfirmed, unknown = "", ""  # why Google named no account: for a sign-in the login names, for one it does not
        client, looked = None, not t.client  # the server's OAuth client file, read once and only when one is needed
        for entry in entries:
            token = _valid(entry)
            if token is None:
                if not looked:
                    client, looked = client_of(name, kind, env, cwd), True
                token, why, failed = self._fresh(name, kind, login, entry, client if t.client else entry["client"],
                                                 account, env)
                if failed:
                    return failed
            if token is not None:
                try:
                    code, body = self.fetch(t.whoami, token, self.timeout)
                except (OSError, ValueError) as err:
                    return _offline(name, err)
                if code in (400, 401):
                    return Check(KIND, "fail", name, f"Google does not accept the token in {tilde(login)} (signed out "
                                                     f"or revoked)", sign_in(name, kind, login, account, env))
                email = body.get(t.field) if isinstance(body, dict) else None
                if code not in (200, 403):
                    return Check(KIND, "fail", name, f"Google answered {code}", "check the network and retry",
                                 transient=code >= 500 or code == 429)
                named = email if code == 200 and isinstance(email, str) and EMAIL.fullmatch(email) else None
                if named and not same(named, account):
                    return Check(KIND, "fail", name, f"the token in {tilde(login)} belongs to {printable(named)}, the "
                                                     f"project's Google account is {account}",
                                 own_login(name, kind, account))
                if named:
                    continue
                # 403: the token may not read that (an API switched off); 200 without an email: not in its scopes
                why = "Google answered 403" if code == 403 else "Google named no account for its token"
            if entry["email"]:
                unconfirmed = unconfirmed or why
            else:
                unknown = unknown or why
        if unknown:
            fix = sign_in(name, kind, login, account, env)
            if unknown == NO_CLIENT and t.client:
                fix = f"set {t.client[0]} in {name}'s env in your agents' MCP configs to its OAuth client file"
            elif unknown in (NO_CLIENT, NO_REFRESH):
                fix = f"sign in again, then start the agent within the hour: {fix}"
            return Check(KIND, "warn", name, f"account not confirmed: {tilde(login)} names no account, and {unknown}",
                         fix)
        if unconfirmed:
            return Check(KIND, "ok", name, f"not confirmed online ({unconfirmed})")
        return Check(KIND, "ok", name, "confirmed by Google")

    def _fresh(self, name: str, kind: str, login: Path, entry: dict, client: tuple[str, str] | None, account: str,
               env: dict) -> tuple[str | None, str, Check | None]:
        """A fresh access token for a sign-in whose saved one has expired: (token, "", None); (None, why not, None) when
        it can't be renewed and the local check decides; or (None, "", the failed check)."""
        if not entry["refresh"]:
            return None, NO_REFRESH, None
        if client is None:
            return None, NO_CLIENT, None
        try:
            code, body = self.renew(entry["refresh"], client, self.timeout)
        except (OSError, ValueError) as err:
            return None, "", _offline(name, err)
        body = body if isinstance(body, dict) else {}
        token, error = body.get("access_token"), body.get("error")
        if code == 200 and isinstance(token, str) and token:
            return token, "", None
        if error == "invalid_grant":  # signed out, revoked, or a password change: the server can't renew it either
            return None, "", Check(KIND, "fail", name, f"Google does not accept the sign-in in {tilde(login)} (signed "
                                                       f"out or revoked)", sign_in(name, kind, login, account, env))
        if code >= 500 or code == 429:
            return None, "", Check(KIND, "fail", name, f"Google answered {code}", "check the network and retry",
                                   transient=True)
        if code in (200, 400, 401):  # e.g. invalid_client: not the client this sign-in was made with
            said = error if isinstance(error, str) and ERROR.fullmatch(error) else str(code)
            return None, f"Google did not renew its token: {said}", None
        return None, "", Check(KIND, "fail", name, f"Google answered {code}", "check the network and retry")


def _offline(name: str, err: Exception) -> Check:
    return Check(KIND, "fail", name, f"Google did not answer ({getattr(err, 'reason', None) or type(err).__name__})",
                 "check the network and retry", transient=True)
