"""Google Workspace MCP servers (google-mcp): which servers are Google's, the logins they read, the adapter's checks,
which servers each project's sessions see, and an agent started through its shim. Every login and OAuth client is a
stand-in file in a temporary folder, Google (its token endpoint too) is a stand-in function, Keyfit's HTTPS opener is
refused, and the shims run with HTTPS_PROXY on a closed local port: no real login, agent config, Keychain item or
network is ever touched."""

import base64
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

os.environ["KEYFIT_CONFIG_DIR"] = tempfile.mkdtemp()  # tests never touch the real ~/.config/keyfit

from keyfit import cli, mcp  # noqa: E402
from keyfit.adapters import ADAPTERS, gemini, google_mcp  # noqa: E402
from keyfit.adapters.google_mcp import GMAIL_PROFILE, PRIMARY_CALENDAR, TOKEN, TOKENINFO, GoogleMcpAdapter  # noqa: E402
from keyfit.cli import main  # noqa: E402
from keyfit.registry import Registry  # noqa: E402
from test_vercel import executable  # noqa: E402

# Fake people; never real ones.
ACME, PERSONAL = "me@acme.io", "someone@gmail.com"
GMAIL = {"command": "npx", "args": ["-y", "@gongrzhe/server-gmail-autoauth-mcp"]}
CALENDAR = {"command": "npx", "args": ["@cocal/google-calendar-mcp@latest"]}
DRIVE = {"command": "npx", "args": ["-y", "@piotr-agier/google-drive-mcp"]}
WORKSPACE = {"command": "uvx", "args": ["workspace-mcp", "--single-user"]}
REMOTE = {"type": "http", "url": "https://gmailmcp.googleapis.com/mcp/v1"}
CLIENT = ("fake-client.apps.googleusercontent.com", "fake-client-secret")  # the servers' OAuth client, a stand-in


def expiry(valid: bool) -> int:
    """An expiry_date in milliseconds, as the Node servers write it."""
    return int((time.time() + (3000 if valid else -3000)) * 1000)


def tokens(email: str | None, valid: bool = False, **extra) -> dict:
    """A google-auth-library token record; its access token (ya29.EMAIL) and refresh token (1//EMAIL) name its account
    for the stand-in Google."""
    return {"access_token": f"ya29.{email or 'nobody'}", "refresh_token": f"1//{email or 'nobody'}",
            "token_type": "Bearer", "expiry_date": expiry(valid), **extra}


def oauth_client(path: Path, client=CLIENT, form: str = "installed") -> Path:
    """A gcp-oauth.keys.json as Google Cloud's console downloads it; its token_uri is never followed."""
    data = {"client_id": client[0], "client_secret": client[1], "token_uri": "https://example.com/token",
            "redirect_uris": ["http://localhost"]}
    return write(path, {form: data} if form else data)


def write(path: Path, data) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))
    return path


def workspace(folder: Path, *emails: str, valid: bool = False) -> Path:
    """workspace-mcp's folder: one <email>.json per account (Python google-auth: token, ISO expiry without a zone)."""
    folder.mkdir(parents=True, exist_ok=True)
    when = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(seconds=3000 if valid else -3000)
    for email in emails:
        write(folder / f"{email}.json", {"token": f"ya29.{email}", "refresh_token": f"1//{email}",
                                         "token_uri": "https://example.com/token",
                                         "client_id": CLIENT[0], "client_secret": CLIENT[1],
                                         "scopes": ["https://www.googleapis.com/auth/userinfo.email"],
                                         "expiry": when.isoformat()})
    write(folder / "oauth_states.json", {})
    return folder


def id_token(email: str) -> str:
    part = base64.urlsafe_b64encode(json.dumps({"email": email}).encode()).decode().rstrip("=")
    return f"e30.{part}.sig"


class Google:
    """Stands in for Google: tokeninfo, Gmail's profile and the primary calendar, each naming the token's account, and
    the token endpoint, renewing a refresh token (1//EMAIL) made with CLIENT into a fresh access token."""

    def __init__(self, scoped: bool = True, status: int = 200, token_status: int = 200):
        self.calls, self.scoped, self.status, self.token_status = [], scoped, status, token_status
        self.sent = []  # the access tokens tokeninfo, the profile and the calendar were asked about
        self.issued = {}  # fresh access token → the account it belongs to

    def __call__(self, url, token, timeout):
        self.calls.append(url)
        self.sent.append(token)
        if not token.startswith("ya29.") or token == "ya29.revoked":
            return (400 if url == TOKENINFO else 401), {"error": "invalid_token"}
        if self.status != 200:
            return self.status, None
        email = self.issued.get(token) or token[len("ya29."):]
        field = {TOKENINFO: "email", GMAIL_PROFILE: "emailAddress", PRIMARY_CALENDAR: "id"}[url]
        return 200, ({field: email} if self.scoped else {"scope": "https://www.googleapis.com/auth/drive.file"})

    def renew(self, refresh, client, timeout):
        self.calls.append(TOKEN)
        if self.token_status != 200:
            return self.token_status, None
        if refresh == "1//revoked":
            return 400, {"error": "invalid_grant", "error_description": "Token has been expired or revoked."}
        if client != CLIENT:
            return 401, {"error": "invalid_client", "error_description": "The OAuth client was not found."}
        fresh = f"ya29.fresh-{len(self.issued)}"
        self.issued[fresh] = refresh[len("1//"):]
        return 200, {"access_token": fresh, "expires_in": 3599, "token_type": "Bearer"}


def no_network(test: unittest.TestCase) -> None:
    """Keyfit's own HTTPS requests fail the test: Google is always the stand-in."""
    patcher = mock.patch("keyfit.http.urlopen", side_effect=AssertionError("no network in tests"))
    patcher.start()
    test.addCleanup(patcher.stop)


class Recognition(unittest.TestCase):
    def test_the_servers_by_package_and_googles_own_by_host(self):
        cases = [(GMAIL, "gmail-autoauth"), (CALENDAR, "google-calendar-mcp"), (DRIVE, "google-drive-mcp"),
                 (WORKSPACE, "workspace-mcp"), ({"command": "uvx", "args": ["--from", "workspace-mcp==2.1.0",
                                                                            "workspace-mcp"]}, "workspace-mcp"),
                 ({"command": "/opt/homebrew/bin/workspace-mcp"}, "workspace-mcp"),
                 ({"command": "node", "args": ["/x/node_modules/@piotr-agier/google-drive-mcp/dist/index.js"]},
                  "google-drive-mcp"),
                 ({"type": "local", "command": ["npx", "@cocal/google-calendar-mcp"]}, "google-calendar-mcp"),
                 (REMOTE, "remote"), ({"httpUrl": "https://calendarmcp.googleapis.com/mcp/v1"}, "remote"),
                 ({"url": "https://people.googleapis.com/mcp/v1"}, "remote")]
        for spec, kind in cases:
            self.assertEqual(mcp.google_type(spec), kind, spec)
        for spec in ({"command": "npx", "args": ["@shinzolabs/gmail-mcp"]}, {"url": "https://mcp.linear.app/mcp"},
                     {"command": "workspace-mcp-tools"}, {"url": "https://people.googleapis.com/v1/people/me"},
                     {"command": "npx", "args": ["mcp-remote", "https://example.com/@cocal/x"]}, {}):
            self.assertIsNone(mcp.google_type(spec), spec)

    def test_where_each_server_keeps_its_login(self):
        home = Path(tempfile.mkdtemp()).resolve()
        env, cwd = {"HOME": str(home)}, home / "repo"
        self.assertEqual(google_mcp.login_of("gmail-autoauth", GMAIL, env, cwd),
                         home / ".gmail-mcp" / "credentials.json")
        self.assertEqual(google_mcp.login_of("workspace-mcp", WORKSPACE, env, cwd),
                         home / ".google_workspace_mcp" / "credentials")
        self.assertEqual(google_mcp.login_of("google-calendar-mcp", CALENDAR, env, cwd),
                         home / ".config" / "google-calendar-mcp" / "tokens.json")
        xdg = {**env, "XDG_CONFIG_HOME": str(home / "xdg")}
        self.assertEqual(google_mcp.login_of("google-drive-mcp", DRIVE, xdg, cwd),
                         home / "xdg" / "google-drive-mcp" / "tokens.json")
        # the definition's own variable wins, with ${VAR}, $VAR, OpenCode's {env:VAR} and ~ filled in from the session
        for value in ("${HOME}/.gmail-mcp/work.json", "$HOME/.gmail-mcp/work.json", "{env:HOME}/.gmail-mcp/work.json",
                      "~/.gmail-mcp/work.json"):
            spec = {**GMAIL, "env": {"GMAIL_CREDENTIALS_PATH": value}}
            self.assertEqual(google_mcp.login_of("gmail-autoauth", spec, env, cwd), home / ".gmail-mcp" / "work.json")
        spec = {"type": "local", "command": ["uvx", "workspace-mcp"],
                "environment": {"GOOGLE_MCP_CREDENTIALS_DIR": "c"}}  # OpenCode's form
        self.assertEqual(google_mcp.login_of("workspace-mcp", spec, env, cwd), cwd / "c")  # relative: the session's
        spec = {**WORKSPACE, "env": {"WORKSPACE_MCP_CREDENTIALS_DIR": "/w", "GOOGLE_MCP_CREDENTIALS_DIR": "/g"}}
        self.assertEqual(google_mcp.login_of("workspace-mcp", spec, env, cwd), Path("/w"))

    def test_what_each_login_says_about_its_accounts(self):
        tmp = Path(tempfile.mkdtemp()).resolve()
        emails = lambda kind, path: [e["email"] for e in google_mcp.read(kind, path)[0]]  # noqa: E731
        self.assertEqual(emails("workspace-mcp", workspace(tmp / "w", ACME, PERSONAL)), [ACME, PERSONAL])
        self.assertEqual(emails("gmail-autoauth", write(tmp / "g.json", tokens(None))), [None])  # Gmail names no one
        cal = write(tmp / "c.json", {"normal": tokens(None, cached_email=ACME), "work2": tokens(None)})
        self.assertEqual(emails("google-calendar-mcp", cal), [ACME, None])
        self.assertEqual(emails("google-calendar-mcp", write(tmp / "c1.json", tokens(None))), [None])  # before 2.x
        drive = write(tmp / "d.json", {"version": 2, "accounts": {
            "default": {"alias": "default", "email": ACME, "accessToken": "ya29.x", "refreshToken": "1//x",
                        "expiryDate": expiry(True)},
            "old": {"alias": "old", "email": "unknown", "refreshToken": "1//y", "pendingIdentity": True}}})
        self.assertEqual(emails("google-drive-mcp", drive), [ACME, None])
        self.assertEqual(emails("other", write(tmp / "o.json", tokens(None, id_token=id_token(ACME)))), [ACME])
        self.assertEqual(google_mcp.read("gmail-autoauth", tmp / "missing.json"), ([], ""))
        self.assertEqual(google_mcp.read("workspace-mcp", tmp / "missing"), ([], ""))
        (tmp / "bad.json").write_text("{")
        self.assertEqual(google_mcp.read("gmail-autoauth", tmp / "bad.json"),
                         (None, f"{tmp / 'bad.json'} is unreadable"))
        self.assertEqual(google_mcp.read("gmail-autoauth", write(tmp / "empty.json", {"scope": "x"})), ([], ""))


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        self.google = Google()
        self.adapter = GoogleMcpAdapter(fetch=self.google, renew=self.google.renew)
        self.gmail = self.home / ".gmail-mcp" / "work.json"
        self.system = mock.patch.object(gemini, "SYSTEM", self.tmp / "no-system")  # never this Mac's settings
        self.system.start()
        self.addCleanup(self.system.stop)
        no_network(self)

    def conn(self, **servers):
        servers = servers or {"gmail_work": {"type": "gmail-autoauth", "login": str(self.gmail)}}
        return {"name": "work", "account": ACME, "servers": servers}

    def env(self, **extra):
        return {"HOME": str(self.home), **extra}

    def claude(self, **servers):
        write(self.home / ".claude.json", {"mcpServers": servers})

    def verify(self, conn=None, remote=True):
        conn = conn or self.conn()
        env = self.env()
        self.adapter.inject(conn, env)
        local = self.adapter.check_local(conn, env, self.repo)
        return local.merge(self.adapter.check_remote(conn, env, self.repo)) if remote and not local.failed else local

    def test_confirmed_by_google_while_the_saved_token_is_valid(self):
        folder = workspace(self.home / "ws", ACME, valid=True)
        check = self.verify(self.conn(workspace={"type": "workspace-mcp", "login": str(folder)}))
        self.assertEqual((check.status, check.label, check.detail),
                         ("ok", "workspace", f"{ACME} · confirmed by Google"))
        write(self.gmail, tokens(ACME, valid=True))  # Gmail names its account only through its profile
        self.assertEqual(self.verify().detail, "confirmed by Google")
        cal = write(self.tmp / "cal.json", {"normal": tokens(ACME, valid=True, cached_email=ACME)})
        drive = write(self.tmp / "drive.json", {"version": 2, "accounts": {"a": {
            "email": ACME, "accessToken": f"ya29.{ACME}", "refreshToken": "1//x", "expiryDate": expiry(True)}}})
        check = self.verify(self.conn(gcal={"type": "google-calendar-mcp", "login": str(cal)},
                                      gdrive={"type": "google-drive-mcp", "login": str(drive)}))
        self.assertEqual((check.status, check.label, check.detail),
                         ("ok", "gcal, gdrive", f"{ACME} · confirmed by Google"))
        self.assertEqual(self.google.calls, [TOKENINFO, GMAIL_PROFILE, PRIMARY_CALENDAR, TOKENINFO])

    def test_an_expired_token_is_renewed_and_the_fresh_one_asked_once(self):
        """Google Workspace MCP: the client is in the login record. The expired token is never sent, the fresh one only
        to tokeninfo."""
        folder = workspace(self.home / "ws", ACME)
        check = self.verify(self.conn(workspace={"type": "workspace-mcp", "login": str(folder)}))
        self.assertEqual((check.status, check.detail), ("ok", f"{ACME} · confirmed by Google"))
        self.assertEqual((self.google.calls, self.google.sent), ([TOKEN, TOKENINFO], ["ya29.fresh-0"]))

    def test_gmail_autoauth_is_confirmed_hours_after_its_sign_in(self):
        """Its login names no account: renewed with its OAuth client (~/.gmail-mcp/gcp-oauth.keys.json, else
        GMAIL_OAUTH_PATH), Gmail's profile names it, and another account is refused."""
        write(self.gmail, tokens(ACME))
        oauth_client(self.home / ".gmail-mcp" / "gcp-oauth.keys.json")
        check = self.verify()
        self.assertEqual((check.status, check.detail), ("ok", "confirmed by Google"))
        self.assertEqual((self.google.calls, self.google.sent), ([TOKEN, GMAIL_PROFILE], ["ya29.fresh-0"]))
        write(self.gmail, tokens(PERSONAL))
        check = self.verify()
        self.assertEqual((check.status, check.detail),
                         ("fail", f"the token in {self.gmail} belongs to {PERSONAL}, the project's Google account is "
                                  f"{ACME}"))
        self.assertIn("give gmail_work a login of its own", check.fix)
        # the client file its definition names wins, relative to the folder the agent starts in
        (self.home / ".gmail-mcp" / "gcp-oauth.keys.json").unlink()
        oauth_client(self.repo / "keys" / "gmail.json", form="web")
        write(self.gmail, tokens(ACME))
        self.claude(gmail_work={**GMAIL, "env": {"GMAIL_CREDENTIALS_PATH": str(self.gmail),
                                                 "GMAIL_OAUTH_PATH": "keys/gmail.json"}})
        self.assertEqual(self.verify().detail, "confirmed by Google")

    def test_calendar_drive_and_other_servers_are_renewed_with_their_own_clients(self):
        cal = write(self.tmp / "cal.json", {"normal": tokens(ACME), "work": tokens(ACME, cached_email=ACME)})
        oauth_client(self.repo / "gcp-oauth.keys.json", form="")  # the Calendar server takes the top-level form too
        drive = write(self.home / ".config" / "google-drive-mcp" / "tokens.json", {"version": 2, "accounts": {"a": {
            "email": ACME, "accessToken": f"ya29.{ACME}", "refreshToken": f"1//{ACME}", "expiryDate": expiry(False)}}})
        oauth_client(self.home / ".config" / "google-drive-mcp" / "gcp-oauth.keys.json")  # next to its default login
        self.claude(gcal={**CALENDAR, "env": {"GOOGLE_CALENDAR_MCP_TOKEN_PATH": str(cal),
                                              "GOOGLE_OAUTH_CREDENTIALS": "./gcp-oauth.keys.json"}})
        check = self.verify(self.conn(gcal={"type": "google-calendar-mcp", "login": str(cal)},
                                      gdrive={"type": "google-drive-mcp", "login": str(drive)}))
        self.assertEqual((check.status, check.detail), ("ok", "confirmed by Google"))
        self.assertEqual(self.google.calls, [TOKEN, PRIMARY_CALENDAR, TOKEN, PRIMARY_CALENDAR, TOKEN, TOKENINFO])
        self.assertEqual(self.google.sent, ["ya29.fresh-0", "ya29.fresh-1", "ya29.fresh-2"])
        other = write(self.tmp / "other.json", tokens(None, client_id=CLIENT[0], client_secret=CLIENT[1],
                                                       refresh_token=f"1//{ACME}"))
        self.assertEqual(self.verify(self.conn(o={"type": "other", "login": str(other)})).detail, "confirmed by Google")
        self.assertEqual(self.google.calls[-2:], [TOKEN, TOKENINFO])

    def test_without_its_oauth_client_the_local_check_decides(self):
        """No client found, or none in an other server's login: nothing is sent, and a login that names no account is a
        warning, as before renewal."""
        write(self.gmail, tokens(ACME))
        check = self.verify()
        self.assertEqual(check.status, "warn")
        self.assertEqual(check.detail, f"account not confirmed: {self.gmail} names no account, and its saved token "
                                       f"has expired, with no OAuth client found to renew it")
        self.assertEqual(check.fix, "set GMAIL_OAUTH_PATH in gmail_work's env in your agents' MCP configs to its OAuth "
                                    "client file")
        self.assertEqual(self.verify(remote=False).status, "ok")  # locally nothing is wrong; pending
        self.assertTrue(self.adapter.check_local(self.conn(), self.env(), self.repo).pending)
        other = write(self.tmp / "other.json", tokens(None, id_token=id_token(ACME)))
        check = self.verify(self.conn(o={"type": "other", "login": str(other)}))
        self.assertEqual((check.status, check.detail),
                         ("ok", f"{ACME} · not confirmed online (its saved token has expired, with no OAuth client found "
                                f"to renew it)"))
        oauth_client(self.home / ".gmail-mcp" / "gcp-oauth.keys.json", client=("another", "client"))
        check = self.verify()  # not the client the sign-in was made with: Google says so, and the server can't renew it
        self.assertEqual((check.status, check.detail),
                         ("warn", f"account not confirmed: {self.gmail} names no account, and Google did not renew its "
                                  f"token: invalid_client"))
        self.assertIn("npx @gongrzhe/server-gmail-autoauth-mcp auth", check.fix)
        workspace(self.home / "ws", ACME)
        write(self.home / "ws" / f"{ACME}.json", {"token": "ya29.x", "expiry": "2026-01-01T00:00:00"})
        check = self.verify(self.conn(w={"type": "workspace-mcp", "login": str(self.home / "ws")}))
        self.assertEqual(check.detail, f"{ACME} · not confirmed online (its saved token has expired, with no refresh "
                                       f"token to renew it)")
        self.assertEqual(self.google.calls, [TOKEN])  # only the one with a client of its own

    def test_a_revoked_sign_in_asks_for_the_servers_own(self):
        write(self.gmail, tokens(None, refresh_token="1//revoked"))
        oauth_client(self.home / ".gmail-mcp" / "gcp-oauth.keys.json")
        check = self.verify()
        self.assertEqual((check.status, check.detail, check.transient),
                         ("fail", f"Google does not accept the sign-in in {self.gmail} (signed out or revoked)", False))
        self.assertEqual(check.fix, f"GMAIL_CREDENTIALS_PATH={self.gmail} npx @gongrzhe/server-gmail-autoauth-mcp auth"
                                    f"   (as {ACME})")
        self.assertEqual(self.google.calls, [TOKEN])

    def test_no_answer_from_the_token_endpoint_is_a_network_problem(self):
        write(self.gmail, tokens(ACME))
        oauth_client(self.home / ".gmail-mcp" / "gcp-oauth.keys.json")

        def offline(refresh, client, timeout):
            raise OSError("no route")

        adapter = GoogleMcpAdapter(fetch=self.google, renew=offline)
        check = adapter.check_remote(self.conn(), self.env(), self.repo)
        self.assertEqual((check.status, check.detail, check.transient), ("fail", "Google did not answer (OSError)", True))
        busy = GoogleMcpAdapter(fetch=self.google, renew=Google(token_status=503).renew)
        check = busy.check_remote(self.conn(), self.env(), self.repo)
        self.assertEqual((check.detail, check.transient), ("Google answered 503", True))
        self.assertEqual(self.google.calls, [])

    def test_renewal_writes_nothing_and_shows_no_token(self):
        """The login keeps its mtime and content; the refresh token, client secret and fresh token are in no detail."""
        write(self.gmail, tokens(ACME))
        oauth_client(self.home / ".gmail-mcp" / "gcp-oauth.keys.json")
        folder = workspace(self.home / "ws", ACME)
        logins = [self.gmail, folder / f"{ACME}.json", self.home / ".gmail-mcp" / "gcp-oauth.keys.json"]
        before = [(p.stat().st_mtime_ns, p.read_bytes()) for p in logins]
        conn = self.conn(gmail_work={"type": "gmail-autoauth", "login": str(self.gmail)},
                         workspace={"type": "workspace-mcp", "login": str(folder)})
        fingerprint = self.adapter.fingerprint(conn, self.env())
        check = self.verify(conn)
        self.assertEqual((check.status, check.detail), ("ok", "confirmed by Google"))
        self.assertEqual([(p.stat().st_mtime_ns, p.read_bytes()) for p in logins], before)
        self.assertEqual(sorted(p.name for p in (self.home / ".gmail-mcp").iterdir()),
                         ["gcp-oauth.keys.json", "work.json"])
        self.assertEqual(self.adapter.fingerprint(conn, self.env()), fingerprint)
        shown = " ".join(check.lines())
        for secret in (f"1//{ACME}", CLIENT[1], *self.google.issued):
            self.assertNotIn(secret, shown)

    def test_the_renewal_request(self):
        """POST to Google's token endpoint, pinned (a token_uri in a file is never followed), with the refresh token and
        the client in the body, never in the URL; Google's error answer is read."""
        import urllib.error
        import urllib.parse

        seen = []

        class Answer(io.BytesIO):
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def urlopen(req, timeout):
            seen.append(req)
            if len(seen) == 2:
                raise urllib.error.HTTPError(req.full_url, 400, "Bad Request", {},
                                             io.BytesIO(b'{"error": "invalid_grant"}'))
            return Answer(b'{"access_token": "ya29.fresh", "expires_in": 3599}')

        with mock.patch("keyfit.http.urlopen", urlopen):
            self.assertEqual(google_mcp._renew("1//r", CLIENT, 15), (200, {"access_token": "ya29.fresh",
                                                                           "expires_in": 3599}))
            self.assertEqual(google_mcp._renew("1//r", CLIENT, 15), (400, {"error": "invalid_grant"}))
        req = seen[0]
        self.assertEqual((req.full_url, req.get_method()), ("https://oauth2.googleapis.com/token", "POST"))
        self.assertEqual(req.get_header("Content-type"), "application/x-www-form-urlencoded")
        self.assertEqual(urllib.parse.parse_qs(req.data.decode()),
                         {"grant_type": ["refresh_token"], "refresh_token": ["1//r"], "client_id": [CLIENT[0]],
                          "client_secret": [CLIENT[1]]})

    def test_another_account_blocks_with_the_fix(self):
        folder = workspace(self.home / "ws", PERSONAL)
        check = self.verify(self.conn(workspace={"type": "workspace-mcp", "login": str(folder)}))
        self.assertTrue(check.failed)
        self.assertEqual(check.detail, f"{folder} is signed in as {PERSONAL}, the project's Google account is {ACME}")
        self.assertEqual(check.fix, f"give workspace a login of its own for {ACME} (set WORKSPACE_MCP_CREDENTIALS_DIR in "
                                    f"its config to a new folder), sign in there, then: keyfit add google-mcp --server "
                                    f"workspace")
        # a login holding both accounts lets a call act as either: blocked too
        workspace(folder, ACME)
        check = self.verify(self.conn(workspace={"type": "workspace-mcp", "login": str(folder)}))
        self.assertEqual(check.detail, f"{folder} also holds {PERSONAL}, which workspace can act as")
        cal = write(self.tmp / "cal.json", {"normal": tokens(None, cached_email=ACME),
                                            "home": tokens(None, cached_email="Someone@Gmail.com")})
        check = self.verify(self.conn(gcal={"type": "google-calendar-mcp", "login": str(cal)}))
        self.assertEqual(check.detail, f"{cal} also holds {PERSONAL}, which gcal can act as")
        self.assertEqual(self.google.calls, [])

    def test_google_naming_another_account_blocks(self):
        write(self.gmail, tokens(PERSONAL, valid=True))
        check = self.verify()
        self.assertTrue(check.failed)
        self.assertEqual(check.detail, f"the token in {self.gmail} belongs to {PERSONAL}, the project's Google account "
                                       f"is {ACME}")
        self.assertEqual(check.fix, f"give gmail_work a login of its own for {ACME} (set GMAIL_CREDENTIALS_PATH in its "
                                    f"config to a new file), sign in there, then: keyfit add google-mcp --server "
                                    f"gmail_work")

    def test_case_does_not_matter(self):
        folder = workspace(self.home / "ws", "Me@Acme.io", valid=True)
        self.assertEqual(self.verify(self.conn(w={"type": "workspace-mcp", "login": str(folder)})).status, "ok")

    def test_not_signed_in_is_a_warning_with_the_servers_own_sign_in(self):
        check = self.verify()
        self.assertEqual((check.status, check.detail), ("warn", f"not signed in yet ({self.gmail})"))
        self.assertEqual(check.fix, f"GMAIL_CREDENTIALS_PATH={self.gmail} npx @gongrzhe/server-gmail-autoauth-mcp auth"
                                    f"   (as {ACME})")
        default = self.home / ".gmail-mcp" / "credentials.json"
        self.assertEqual(self.verify(self.conn(gmail={"type": "gmail-autoauth", "login": str(default)})).fix,
                         f"npx @gongrzhe/server-gmail-autoauth-mcp auth   (as {ACME})")
        check = self.verify(self.conn(w={"type": "workspace-mcp", "login": str(self.home / "ws")}))
        self.assertEqual(check.fix, f"sign in as {ACME} from an agent session here (ask it to run w's start_google_auth "
                                    f"tool)")

    def test_a_definition_must_read_the_recorded_login(self):
        write(self.gmail, tokens(None))
        self.claude(gmail_work=GMAIL)  # no GMAIL_CREDENTIALS_PATH: the default login, not work.json
        check = self.verify(remote=False)
        self.assertTrue(check.failed)
        default = self.home / ".gmail-mcp" / "credentials.json"
        self.assertEqual(check.detail, f"Claude's gmail_work reads {default}, not {self.gmail}")
        self.assertEqual(check.fix, f"set GMAIL_CREDENTIALS_PATH={self.gmail} in gmail_work's env in Claude's MCP config, "
                                    f"or: keyfit add google-mcp --server gmail_work")
        self.claude(gmail_work={**GMAIL, "env": {"GMAIL_CREDENTIALS_PATH": str(self.gmail)}})
        self.assertFalse(self.verify(remote=False).failed)
        # every agent's definition counts, the folder's too
        write(self.home / ".gemini" / "settings.json", {"mcpServers": {"gmail_work": CALENDAR}})
        check = self.verify(remote=False)
        self.assertEqual(check.detail, "Gemini CLI's gmail_work is Google Calendar MCP, not Gmail AutoAuth MCP")
        (self.home / ".gemini" / "settings.json").unlink()
        write(self.repo / ".mcp.json", {"mcpServers": {"gmail_work": REMOTE}})
        check = self.verify(remote=False)
        self.assertEqual(check.detail, f"Claude's gmail_work is a remote server (its sign-in is Claude's own), not one "
                                       f"reading {self.gmail}")

    def test_workspace_mcps_default_account_must_be_the_projects(self):
        folder = workspace(self.home / ".google_workspace_mcp" / "credentials", ACME)
        self.claude(workspace={**WORKSPACE, "env": {"USER_GOOGLE_EMAIL": PERSONAL}})
        check = self.verify(self.conn(workspace={"type": "workspace-mcp", "login": str(folder)}), remote=False)
        self.assertEqual(check.detail, f"Claude's workspace sets USER_GOOGLE_EMAIL={PERSONAL}, the project's Google "
                                       f"account is {ACME}")

    def test_no_google_account(self):
        check = self.adapter.check_local({"servers": {"g": {"type": "gmail-autoauth", "login": "/x"}}, "account": None},
                                         self.env(), self.repo)
        self.assertEqual((check.status, check.detail, check.fix),
                         ("fail", "the project has no Google account",
                          "keyfit add google --email <the project's Google account>"))

    def test_a_revoked_token_asks_for_a_sign_in(self):
        write(self.gmail, tokens(None, valid=True, access_token="ya29.revoked"))
        check = self.verify()
        self.assertEqual((check.status, check.detail),
                         ("fail", f"Google does not accept the token in {self.gmail} (signed out or revoked)"))
        self.assertIn("npx @gongrzhe/server-gmail-autoauth-mcp auth", check.fix)

    def test_no_answer_from_google_is_a_network_problem(self):
        def offline(url, token, timeout):
            raise OSError("no route")

        write(self.gmail, tokens(ACME, valid=True))
        check = GoogleMcpAdapter(fetch=offline).remote(self.conn(), self.env())
        self.assertEqual((check.status, check.transient), ("fail", True))
        busy = GoogleMcpAdapter(fetch=Google(status=503)).remote(self.conn(), self.env())
        self.assertEqual((busy.detail, busy.transient), ("Google answered 503", True))

    def test_when_google_names_no_account(self):
        other = write(self.tmp / "other.json", tokens(ACME, valid=True))
        check = GoogleMcpAdapter(fetch=Google(scoped=False)).remote(
            self.conn(o={"type": "other", "login": str(other)}), self.env())
        self.assertEqual((check.status, check.detail),
                         ("warn", f"account not confirmed: {other} names no account, and Google named no account for "
                                  f"its token"))
        write(self.gmail, tokens(ACME, valid=True))  # Gmail switched off in the OAuth client's project
        check = GoogleMcpAdapter(fetch=Google(status=403)).remote(self.conn(), self.env())
        self.assertEqual(check.status, "warn")
        self.assertIn("Google answered 403", check.detail)

    def test_several_servers_are_named_in_the_detail(self):
        write(self.gmail, tokens(None))
        cal = write(self.tmp / "cal.json", {"normal": tokens(PERSONAL, cached_email=PERSONAL)})
        conn = self.conn(gmail_work={"type": "gmail-autoauth", "login": str(self.gmail)},
                         gcal={"type": "google-calendar-mcp", "login": str(cal)})
        check = self.verify(conn)
        self.assertEqual((check.label, check.detail),
                         ("gmail_work, gcal", f"gcal: {cal} is signed in as {PERSONAL}, the project's Google account is "
                                              f"{ACME}"))

    def test_inject_removes_variables_that_would_pick_another_login(self):
        env = self.env(GMAIL_CREDENTIALS_PATH="/p", WORKSPACE_MCP_CREDENTIALS_DIR="/p", GOOGLE_MCP_CREDENTIALS_DIR="/p",
                       GOOGLE_CALENDAR_MCP_TOKEN_PATH="/p", GOOGLE_DRIVE_MCP_TOKEN_PATH="/p", USER_GOOGLE_EMAIL=PERSONAL,
                       GOOGLE_OAUTH_CREDENTIALS="/client.json", XDG_CONFIG_HOME="/xdg")
        self.adapter.inject(self.conn(), env)
        self.assertEqual(sorted(env), ["GOOGLE_OAUTH_CREDENTIALS", "HOME", "XDG_CONFIG_HOME"])

    def test_the_fingerprint_follows_the_login(self):
        before = self.adapter.fingerprint(self.conn(), self.env())
        write(self.gmail, tokens(None))
        after = self.adapter.fingerprint(self.conn(), self.env())
        self.assertNotEqual(before, after)
        write(self.gmail, tokens(None, refresh_token="1//another-sign-in"))
        self.assertNotEqual(after, self.adapter.fingerprint(self.conn(), self.env()))


class HidingTests(unittest.TestCase):
    """Projects work (me@acme.io) and personal own their Gmail servers; plain has no Google Workspace servers."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.home = self.tmp / "home"
        self.repo = self.tmp / "repo"
        self.repo.mkdir(parents=True)
        self.env = {"HOME": str(self.home)}
        self.servers = {"gmail_work": {**GMAIL, "env": {"GMAIL_CREDENTIALS_PATH": "~/.gmail-mcp/work.json"}},
                        "gmail_personal": GMAIL, "gcal": CALENDAR, "gmail_google": REMOTE,
                        "paper": {"type": "http", "url": "http://127.0.0.1:29979/mcp"}}
        write(self.home / ".claude.json", {"mcpServers": self.servers})
        self.system = mock.patch.object(gemini, "SYSTEM", self.tmp / "no-system")
        self.system.start()
        self.addCleanup(self.system.stop)
        server = lambda name, login: {name: {"type": "gmail-autoauth", "login": login}}  # noqa: E731
        self.registry = Registry(self.tmp / "registry.toml", {
            "projects": {"work": {"roots": [str(self.repo)], "google": "work", "google-mcp": "work"},
                         "personal": {"roots": ["/p"], "google": "personal", "google-mcp": "personal"},
                         "plain": {"roots": ["/q"]}},
            "accounts": {"google": {"work": {"email": ACME}, "personal": {"email": PERSONAL}},
                         "google-mcp": {"work": {"servers": server("gmail_work", "~/.gmail-mcp/work.json")},
                                        "personal": {"servers": server("gmail_personal", "~/.gmail-mcp/credentials.json")}}}})

    def denied(self, project):
        settings = mcp.claude_settings(self.registry.get(project), self.env, self.repo, self.registry.mcp_owners())
        return sorted(s["serverName"] for s in settings.get("deniedMcpServers", []))

    def test_the_servers_a_project_claims_are_its_own(self):
        self.assertEqual(self.registry.mcp_owners(), {"work": ["gmail_work"], "personal": ["gmail_personal"]})
        self.assertEqual(self.registry.get("work").connections["google-mcp"]["account"], ACME)

    def test_a_project_with_google_mcp_sees_only_its_own_google_servers(self):
        self.assertEqual(self.denied("work"), ["gcal", "gmail_google", "gmail_personal"])
        self.assertEqual(self.denied("personal"), ["gcal", "gmail_google", "gmail_work"])

    def test_elsewhere_only_the_servers_other_projects_claim_are_hidden(self):
        self.assertEqual(self.denied("plain"), ["gmail_personal", "gmail_work"])

    def test_a_server_the_project_owns_by_name_stays(self):
        self.registry.add_mcp_servers("work", ["gmail_google"])  # keyfit add mcp gmail_google
        self.assertEqual(self.denied("work"), ["gcal", "gmail_personal"])

    def test_every_agent_hides_them(self):
        project, owners = self.registry.get("work"), self.registry.mcp_owners()
        write(self.home / ".codex" / "config.toml", {})
        (self.home / ".codex" / "config.toml").write_text(
            '[mcp_servers.gmail_personal]\ncommand = "npx"\nargs = ["@gongrzhe/server-gmail-autoauth-mcp"]\n\n'
            '[mcp_servers.gmail_work]\ncommand = "npx"\nargs = ["@gongrzhe/server-gmail-autoauth-mcp"]\n')
        self.assertEqual(mcp.codex_overrides(project, self.env, owners),
                         ["-c", "mcp_servers.gmail_personal.enabled=false"])
        write(self.home / ".config" / "opencode" / "opencode.json", {"mcp": {
            "gcal": {"type": "local", "command": ["npx", "@cocal/google-calendar-mcp"]},
            "gmail_work": {"type": "local", "command": ["npx", "@gongrzhe/server-gmail-autoauth-mcp"]}}})
        env = dict(self.env)
        self.assertEqual(mcp.opencode_environment(project, env, self.repo, owners), ["gcal"])
        write(self.home / ".copilot" / "mcp-config.json",
              {"mcpServers": {"gdrive": DRIVE, "paper": {"url": "http://p"}}})
        self.assertEqual(mcp.copilot_hidden(project, self.env, self.repo, owners), ["gdrive"])
        write(self.home / ".gemini" / "settings.json", {"mcpServers": {"gmail_work": GMAIL, "ws": WORKSPACE}})
        self.assertEqual(mcp.gemini_hidden(project, self.env, self.repo, owners), (["ws"], ["gmail_work"]))

    def test_a_name_with_pattern_characters_is_claimed_as_it_is(self):
        self.registry.data["accounts"]["google-mcp"]["work"]["servers"]["gmail[work]"] = {"type": "gmail-autoauth",
                                                                                          "login": "/x"}
        claims = self.registry.get("work").mcp_claims()
        self.assertIn("gmail[[]work]", claims)
        self.assertTrue(mcp._matches("gmail[work]", claims))
        self.assertFalse(mcp._matches("gmailw", claims))


class CliTests(unittest.TestCase):
    """`keyfit add google-mcp`, init's offer and the doctor's line, in a temporary HOME with project acme (me@acme.io)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.config = self.tmp / "keyfit"
        self.repo, self.other = self.tmp / "acme", self.tmp / "acme-web"
        for folder in (self.repo, self.other):
            folder.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=folder, check=True)
        self.google = Google()
        env = {"PATH": "/usr/bin:/bin", "HOME": str(self.home), "KEYFIT_CONFIG_DIR": str(self.config)}
        for patcher in (mock.patch.dict(os.environ, env, clear=True),
                        mock.patch("urllib.request.urlopen", side_effect=AssertionError("no network in tests")),
                        mock.patch("keyfit.http.urlopen", side_effect=AssertionError("no network in tests")),
                        mock.patch.object(ADAPTERS["google-mcp"], "fetch", self.google),
                        mock.patch.object(ADAPTERS["google-mcp"], "renew", self.google.renew),
                        mock.patch.object(gemini, "SYSTEM", self.tmp / "no-system")):
            patcher.start()
            self.addCleanup(patcher.stop)
        old = os.getcwd()
        os.chdir(self.repo)
        self.addCleanup(os.chdir, old)
        self.gmail = self.home / ".gmail-mcp" / "work.json"
        self.claude(gmail_work={**GMAIL, "env": {"GMAIL_CREDENTIALS_PATH": "~/.gmail-mcp/work.json"}})
        self.keyfit("init", "--yes", "--name", "acme")
        self.keyfit("init", "--yes", "--name", "acme-web", "--root", str(self.other))
        for project in ("acme", "acme-web"):
            self.keyfit("add", "google", "--email", ACME, "--project", project)

    def claude(self, **servers):
        write(self.home / ".claude.json", {"mcpServers": servers})

    def keyfit(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def registry(self) -> dict:
        import tomllib

        return tomllib.loads((self.config / "registry.toml").read_text())

    def test_add_takes_the_login_its_definition_reads(self):
        write(self.gmail, tokens(ACME))  # past its first hour, no OAuth client: recorded, with a note
        code, out, err = self.keyfit("add", "google-mcp", "--server", "gmail_work")
        self.assertEqual(code, 0, err)
        reg = self.registry()
        self.assertEqual(reg["projects"]["acme"]["google-mcp"], "acme")
        self.assertEqual(reg["accounts"]["google-mcp"]["acme"],
                         {"servers": {"gmail_work": {"type": "gmail-autoauth", "login": "~/.gmail-mcp/work.json"}}})
        self.assertIn(f"acme: google-mcp 'acme' → gmail_work (Gmail AutoAuth MCP, ~/.gmail-mcp/work.json), checked "
                      f"against {ACME}; hidden in every other project\n", out)
        self.assertIn("  note: account not confirmed: ~/.gmail-mcp/work.json names no account, and its saved token has "
                      "expired, with no OAuth client found to renew it\n", out)
        self.assertEqual(self.google.calls, [])
        self.assertEqual(Registry.load(self.config / "registry.toml").mcp_owners(), {"acme": ["gmail_work"]})

    def test_a_fresh_login_is_confirmed_and_another_account_refused(self):
        write(self.gmail, tokens(PERSONAL, valid=True))
        code, out, err = self.keyfit("add", "google-mcp", "--server", "gmail_work")
        self.assertEqual(code, 1)
        self.assertIn(f"the token in ~/.gmail-mcp/work.json belongs to {PERSONAL}, the project's Google account is "
                      f"{ACME}\n  → give gmail_work a login of its own", err)
        self.assertNotIn("google-mcp", self.registry()["projects"]["acme"])
        write(self.gmail, tokens(ACME, valid=True))
        code, out, err = self.keyfit("add", "google-mcp", "--server", "gmail_work")
        self.assertEqual((code, "note:" in out), (0, False), err)
        self.assertEqual(self.google.calls, [GMAIL_PROFILE, GMAIL_PROFILE])

    def test_a_renewed_token_reaches_no_file_and_no_output(self):
        """keyfit add and keyfit status renew Gmail AutoAuth's token: the refresh token, the client secret and the fresh
        token are in neither the registry, the check cache nor the output, and the login is unchanged."""
        write(self.gmail, tokens(ACME))
        oauth_client(self.home / ".gmail-mcp" / "gcp-oauth.keys.json")
        before = (self.gmail.stat().st_mtime_ns, self.gmail.read_bytes())
        add = self.keyfit("add", "google-mcp", "--server", "gmail_work")
        self.assertEqual(add[0], 0, add[2])
        self.assertNotIn("note:", add[1])
        status = self.keyfit("status")
        self.assertIn("✔ google-mcp gmail_work   confirmed by Google", status[1])
        self.assertEqual(self.google.calls, [TOKEN, GMAIL_PROFILE, TOKEN, GMAIL_PROFILE])
        cached = (self.config / "cache" / "checks.json").read_text()
        self.assertIn("confirmed by Google", cached)
        written = (self.config / "registry.toml").read_text() + cached + "".join(add[1:] + status[1:])
        for secret in (f"1//{ACME}", CLIENT[1], *self.google.issued):
            self.assertNotIn(secret, written)
        self.assertEqual((self.gmail.stat().st_mtime_ns, self.gmail.read_bytes()), before)

    def test_servers_of_one_account_are_shared_by_its_projects(self):
        workspace(self.home / ".google_workspace_mcp" / "credentials", ACME)
        self.claude(gmail_work={**GMAIL, "env": {"GMAIL_CREDENTIALS_PATH": "~/.gmail-mcp/work.json"}}, ws=WORKSPACE,
                    gcal=CALENDAR)
        self.assertEqual(self.keyfit("add", "google-mcp", "--server", "gmail_work")[0], 0)
        code, out, _ = self.keyfit("add", "google-mcp", "--server", "ws")
        self.assertEqual(code, 0)
        self.assertIn("acme's sessions no longer see the other Google Workspace servers: gcal\n", out)
        code, out, _ = self.keyfit("add", "google-mcp", "--server", "ws", "--project", "acme-web")
        self.assertIn("  account 'acme' is shared: acme takes ws too\n", out)
        self.assertEqual(sorted(self.registry()["accounts"]["google-mcp"]["acme"]["servers"]), ["gmail_work", "ws"])
        self.assertEqual(Registry.load(self.config / "registry.toml").mcp_owners(),
                         {"acme": ["gmail_work", "ws"], "acme-web": ["gmail_work", "ws"]})

    def test_any_other_server_by_its_login_file(self):
        login = write(self.home / "tokens" / "x.json", tokens(None, id_token=id_token(ACME)))
        self.claude(x={"command": "python3", "args": ["-m", "my_gmail_server"]})
        code, out, err = self.keyfit("add", "google-mcp", "--server", "x")
        self.assertEqual(code, 1)
        self.assertIn("x is not a Google Workspace server Keyfit knows in your agents' MCP configs (workspace-mcp, "
                      "gmail-autoauth, google-calendar-mcp, google-drive-mcp).\n  Name its login file: keyfit add "
                      "google-mcp --server x --login PATH\n", err)
        code, out, err = self.keyfit("add", "google-mcp", "--server", "x", "--login", str(login))
        self.assertEqual(code, 0, err)
        self.assertEqual(self.registry()["accounts"]["google-mcp"]["acme"]["servers"]["x"],
                         {"type": "other", "login": "~/tokens/x.json"})

    def test_what_add_refuses(self):
        self.claude(gmail=REMOTE, **{"gmail.work": GMAIL})
        code, _, err = self.keyfit("add", "google-mcp", "--server", "gmail")
        self.assertEqual(code, 1)
        self.assertIn("gmail is one of Google's own remote servers: its sign-in is the agent's, out of Keyfit's reach.\n"
                      "  To give it to acme only (hidden in every other project): keyfit add mcp gmail\n", err)
        code, _, err = self.keyfit("add", "google-mcp", "--server", "gmail.work")
        self.assertIn("gmail.work: Keyfit takes servers named with letters, digits, '_' and '-'", err)
        code, _, err = self.keyfit("add", "google-mcp")  # no terminal: nothing to pick from
        self.assertEqual(code, 1)
        self.assertIn("name the server: keyfit add google-mcp --server <its name in your agents' MCP config> (your "
                      "agents have gmail.work)\n", err)
        (self.tmp / "solo").mkdir()
        self.keyfit("init", "--yes", "--name", "solo", "--root", str(self.tmp / "solo"))
        code, _, err = self.keyfit("add", "google-mcp", "--server", "gmail.work", "--project", "solo")
        self.assertIn("solo has no Google account yet, which its Google Workspace servers are checked against.", err)
        self.assertEqual(self.registry()["accounts"].get("google-mcp"), None)

    def test_init_offers_the_servers_signed_in_as_the_projects_account(self):
        write(self.gmail, tokens(ACME))
        cal = write(self.home / ".config" / "google-calendar-mcp" / "tokens.json",
                    {"normal": tokens(None, cached_email=PERSONAL)})
        self.claude(gmail_work={**GMAIL, "env": {"GMAIL_CREDENTIALS_PATH": "~/.gmail-mcp/work.json"}}, gcal=CALENDAR)
        asked = []
        with mock.patch.object(cli, "_ask", lambda prompt: asked.append(prompt) or "y"):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                cli._offer_google_mcp("acme", {"registry": None, "project": "acme"})
        self.assertIn(f"  gcal (Google Calendar MCP, {cal.relative_to(self.home).as_posix().join(['~/', ''])}: "
                      f"{PERSONAL}) — not offered\n", out.getvalue())
        self.assertEqual(asked, ["  Use gmail_work (Gmail AutoAuth MCP, ~/.gmail-mcp/work.json: names no account) for "
                                 "acme? [y/N] "])
        self.assertEqual(list(self.registry()["accounts"]["google-mcp"]["acme"]["servers"]), ["gmail_work"])

    def test_doctor_names_server_login_and_account(self):
        workspace(self.home / ".google_workspace_mcp" / "credentials", ACME)
        self.claude(ws=WORKSPACE)
        self.keyfit("add", "google-mcp", "--server", "ws")
        project = Registry.load(self.config / "registry.toml").get("acme")
        check = cli._doctor_google_mcp(project, project.connections["google-mcp"], dict(os.environ))
        self.assertEqual(check.lines(), [f"  ✔ google-mcp ws   ws: ~/.google_workspace_mcp/credentials (workspace-mcp, "
                                         f"{ACME})"])


FAKE_CLAUDE = """#!/usr/bin/env python3
import json, os, sys
print(json.dumps({"argv": sys.argv[1:], "env": {k: os.environ.get(k) for k in (
    "GMAIL_CREDENTIALS_PATH", "USER_GOOGLE_EMAIL", "KEYFIT_PROJECT")}}))
"""


class ShimTests(unittest.TestCase):
    """An agent started through the shim written by `keyfit setup`, as a real process in temporary folders: project demo
    (Google account me@acme.io) owns gmail_work; globex owns gmail_personal."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        executable(self.bin / "claude", FAKE_CLAUDE)
        self.config = self.tmp / "keyfit"
        self.gmail = self.home / ".gmail-mcp" / "work.json"
        write(self.home / ".claude.json", {"mcpServers": {
            "gmail_work": {**GMAIL, "env": {"GMAIL_CREDENTIALS_PATH": str(self.gmail)}},
            "gmail_personal": GMAIL, "gcal": CALENDAR, "paper": {"type": "http", "url": "http://p"}}})
        (self.home / ".claude").mkdir()
        write(self.home / ".claude" / "settings.json", {"statusLine": {"type": "command", "command": "true"}})
        self.repo, self.other, self.outside = self.tmp / "repo", self.tmp / "other", self.tmp / "outside"
        for folder in (self.repo, self.other, self.outside):
            folder.mkdir()
        for folder in (self.repo, self.other):
            subprocess.run(["git", "init", "-q"], cwd=folder, check=True)
        with mock.patch.dict(os.environ, {"KEYFIT_CONFIG_DIR": str(self.config)}):
            with contextlib.redirect_stdout(io.StringIO()):
                main(["init", "--yes", "--root", str(self.repo), "--name", "demo"])
                main(["init", "--yes", "--root", str(self.other), "--name", "globex"])
                with mock.patch.object(sys, "platform", "darwin"):
                    main(["setup"])
        reg = self.config / "registry.toml"
        text = reg.read_text().replace(
            '[projects.demo]\nroots = ["' + str(self.repo) + '"]\n',
            '[projects.demo]\nroots = ["' + str(self.repo) + '"]\ngoogle = "work"\ngoogle-mcp = "work"\n')
        text = text.replace('[projects.globex]\nroots = ["' + str(self.other) + '"]\n',
                            '[projects.globex]\nroots = ["' + str(self.other) + '"]\ngoogle = "personal"\n'
                            'google-mcp = "personal"\n')
        reg.write_text(text + f'\n[accounts.google.work]\nemail = "{ACME}"\n\n[accounts.google.personal]\nemail = '
                              f'"{PERSONAL}"\n\n[accounts.google-mcp.work.servers.gmail_work]\ntype = "gmail-autoauth"\n'
                              f'login = "{self.gmail}"\n\n[accounts.google-mcp.personal.servers.gmail_personal]\n'
                              f'type = "gmail-autoauth"\nlogin = "{self.home / ".gmail-mcp" / "credentials.json"}"\n')

    def run_tool(self, *args, cwd=None, **extra):
        env = {"PATH": f"{self.config / 'bin'}{os.pathsep}{self.bin}{os.pathsep}/usr/bin:/bin", "HOME": str(self.home),
               "KEYFIT_CONFIG_DIR": str(self.config), "GEMINI_CLI_SYSTEM_SETTINGS_PATH": str(self.tmp / "none.json"),
               "GEMINI_CLI_SYSTEM_DEFAULTS_PATH": str(self.tmp / "none.json"),
               "HTTPS_PROXY": "http://127.0.0.1:9", **extra}  # should a check ever go online: a closed port
        return subprocess.run(["claude", *args], cwd=cwd or self.repo, env=env, capture_output=True, text=True,
                              timeout=60)

    def started(self, out):
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout.splitlines()[-1])

    def denied(self, seen):
        settings = json.loads(seen["argv"][seen["argv"].index("--settings") + 1])
        return sorted(s["serverName"] for s in settings["deniedMcpServers"])

    def test_the_session_sees_only_the_projects_google_server_and_warns_what_keyfit_cannot_confirm(self):
        write(self.gmail, tokens(ACME))  # past its first hour and no OAuth client: no online question at all
        out = self.run_tool("-p", "hi", GMAIL_CREDENTIALS_PATH="/elsewhere.json", USER_GOOGLE_EMAIL=PERSONAL)
        seen = self.started(out)
        self.assertEqual(self.denied(seen), ["gcal", "gmail_personal"])
        self.assertEqual(seen["env"],
                         {"GMAIL_CREDENTIALS_PATH": None, "USER_GOOGLE_EMAIL": None, "KEYFIT_PROJECT": "demo"})
        self.assertIn("keyfit: ! google-mcp gmail_work   account not confirmed: ~/.gmail-mcp/work.json names no "
                      "account, and its saved token has expired, with no OAuth client found to renew it\n", out.stderr)
        seen = self.started(self.run_tool("-p", "hi", cwd=self.other))  # globex: not signed in yet, a warning too
        self.assertEqual(self.denied(seen), ["gcal", "gmail_work"])

    def test_another_account_blocks_with_the_fix(self):
        write(self.gmail, tokens(None, id_token=id_token(PERSONAL)))
        out = self.run_tool("-p", "hi")
        self.assertEqual((out.returncode, out.stdout), (1, ""))
        self.assertIn(f"keyfit: claude not started, demo needs attention\n"
                      f"  ✘ google-mcp gmail_work   ~/.gmail-mcp/work.json is signed in as {PERSONAL}, the project's "
                      f"Google account is {ACME}\n    → give gmail_work a login of its own for {ACME} (set "
                      f"GMAIL_CREDENTIALS_PATH in its config to a new file), sign in there, then: keyfit add google-mcp "
                      f"--server gmail_work\n",
                      out.stderr)

    def test_unregistered_folder_runs_unchanged(self):
        seen = self.started(self.run_tool("-p", "hi", cwd=self.outside, GMAIL_CREDENTIALS_PATH="/mine.json"))
        self.assertEqual(seen["argv"], ["-p", "hi"])
        self.assertEqual(seen["env"]["GMAIL_CREDENTIALS_PATH"], "/mine.json")


if __name__ == "__main__":
    unittest.main()
