"""Gemini CLI: which Google sign-in (or Gemini API key) a project's Gemini CLI sessions run on.

Account:  [accounts.gemini.work]
          home = "~/.config/keyfit/gemini/work"   (optional: Gemini CLI's home, which holds .gemini/; absent: your own)
          key = "keychain:keyfit/gemini-work"     (optional: a Gemini API key instead of a Google sign-in)
          cloud_project = "acme-ai"               (optional: GOOGLE_CLOUD_PROJECT for Gemini Code Assist)
Project:  gemini = "work"   and   google = "work"   (the project's Google account: its email reaches this connection
          as `account`, see registry.GOOGLE_TOOLS)

Gemini CLI (`gemini`, verified with 0.64.0) keeps everything in ~/.gemini, or in $GEMINI_CLI_HOME/.gemini: settings
(the sign-in method, security.auth.selectedType, and mcpServers), sessions, extensions, and the Google sign-in,
oauth_creds.json (a plain file unless GEMINI_FORCE_ENCRYPTED_FILE_STORAGE moves it to one Keychain item shared by every
home). Google writes the account's email into the sign-in's id_token; Gemini CLI also notes it in google_accounts.json.
A Gemini API key comes from GEMINI_API_KEY before Gemini CLI's own Keychain item (one per Mac). There is no sign-in
command: Gemini CLI signs in inside a session (its first start, or /auth), and `gemini --prompt-interactive /quit`
signs in and quits. So an account is a home: your own for one project, one Keyfit makes for each other.

inject  — GEMINI_CLI_HOME (none for your own home); a key account's key in GEMINI_API_KEY.
scrub   — where Gemini CLI starts (the shim and the remote check): the variables that would pick another sign-in, key,
          Google Cloud project, endpoint or credential store (GEMINI_API_KEY unless it is the account's, GOOGLE_API_KEY,
          GOOGLE_CLOUD_PROJECT, GOOGLE_APPLICATION_CREDENTIALS, GOOGLE_GENAI_USE_VERTEXAI, …). Sessions of other agents
          keep them. GEMINI_DEFAULT_AUTH_TYPE preselects the account's method in Gemini CLI's sign-in dialog.
forget  — in a project without a Gemini CLI account, what Keyfit set for another project goes.
local   — Google: the home's sign-in method is Google's (or not chosen yet), it holds a Google sign-in, and that
          sign-in's email is the project's Google account. Key: the key is there and the home uses the API-key method.
          Only labels are read, never a token.
remote  — Google: when the sign-in's access token is still valid, Google's tokeninfo must name the account; an expired
          one is not renewed (that needs Gemini CLI's own OAuth client), so the local check decides until Gemini CLI
          renews it. Key: Google's Gemini API must accept the key (it lists one model; the key goes in a header).
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shlex
import time
from pathlib import Path

from .. import secrets
from ..checks import Check, printable
from ..registry import canonical, tilde
from .base import Adapter
from .gcloud import TOKENINFO
from .google import EMAIL, same

HOME_VAR = "GEMINI_CLI_HOME"
KEY_VAR = "GEMINI_API_KEY"
MARK = "KEYFIT_GEMINI"  # set next to the GEMINI_CLI_HOME Keyfit injected: the account's name
KEY_MARK = "KEYFIT_GEMINI_KEY"  # set when the GEMINI_API_KEY is one Keyfit injected
GOOGLE, API_KEY = "oauth-personal", "gemini-api-key"  # Gemini CLI's names of the two methods
METHODS = {GOOGLE: "Sign in with Google", API_KEY: "a Gemini API key", "vertex-ai": "Vertex AI",
           "compute-default-credentials": "the metadata server's credentials", "cloud-shell": "Cloud Shell",
           "gateway": "a gateway"}
SIGN_IN = ["--prompt-interactive", "/quit"]  # signs in (Gemini CLI's own dialog) and quits
SYSTEM = Path("/Library/Application Support/GeminiCli")  # macOS: system settings and defaults, root's
# Variables that would make Gemini CLI use another key, method, Google Cloud project, endpoint, credential or credential
# store than the account's (Gemini CLI 0.64.0 and the Google libraries it bundles).
SCRUB = ("GOOGLE_API_KEY", "GOOGLE_APPLICATION_CREDENTIALS", "GOOGLE_CLOUD_ACCESS_TOKEN", "GOOGLE_CLOUD_PROJECT",
         "GOOGLE_CLOUD_PROJECT_ID", "GOOGLE_CLOUD_LOCATION", "GOOGLE_CLOUD_QUOTA_PROJECT", "GOOGLE_CLOUD_ACCOUNT",
         "GOOGLE_CLOUD_UNIVERSE_DOMAIN", "GOOGLE_GENAI_USE_VERTEXAI", "GOOGLE_GENAI_USE_GCA", "GOOGLE_GEMINI_BASE_URL",
         "GOOGLE_VERTEX_BASE_URL", "GOOGLE_CREDENTIALS", "GOOGLE_SERVICE_ACCOUNT_AUTH", "GEMINI_CLI_AUTH_OVERRIDE",
         "GEMINI_DEFAULT_AUTH_TYPE", "GEMINI_CLI_USE_COMPUTE_ADC", "GEMINI_FORCE_ENCRYPTED_FILE_STORAGE")
SCRUB_PREFIXES = ("GOOGLE_EXTERNAL_ACCOUNT_",)
MODELS = "https://generativelanguage.googleapis.com/v1beta/models?pageSize=1"
SHARED = ("GEMINI.md", "commands", "skills", "agents", "policies", "extensions")  # follow you into a new home
EXPIRY_MARGIN = 60  # seconds: a token about to expire is treated as expired


def _home(env: dict) -> Path:
    return Path(env.get("HOME") or Path.home())


def home(conn, env: dict) -> Path:
    """The account's Gemini CLI home (Gemini keeps .gemini/ in it): the recorded one, else your own."""
    return canonical(conn.get("home") or _home(env))


def is_default(path: str | Path, env: dict) -> bool:
    return canonical(path) == canonical(_home(env))


def folder(path: Path) -> Path:
    return Path(path) / ".gemini"


def is_key(conn) -> bool:
    return bool(conn.get("key"))


def sign_in(path: Path, env: dict) -> str:
    """The sign-in for this home: Gemini CLI's own dialog, then it quits."""
    prefix = "" if is_default(path, env) else f"{HOME_VAR}={shlex.quote(str(canonical(path)))} "
    return f"{prefix}gemini {shlex.join(SIGN_IN)}"


def _jsonc(path: Path):
    """A settings file as Gemini CLI reads it (JSON with comments), or None."""
    from .cloudflare import _jsonc as strip

    try:
        return json.loads(strip(path.read_text()))
    except (OSError, UnicodeDecodeError, ValueError):
        return None


def settings_files(path: Path, env: dict, cwd: Path | None = None) -> list[tuple[str, Path]]:
    """(scope, file) of Gemini CLI's settings, weakest first: the system defaults, yours (in the home), the folder's,
    the system's. The folder's counts only for the folder Gemini CLI starts in, and not when it is the home."""
    system = Path(env["GEMINI_CLI_SYSTEM_SETTINGS_PATH"]) if env.get("GEMINI_CLI_SYSTEM_SETTINGS_PATH") \
        else SYSTEM / "settings.json"
    defaults = Path(env["GEMINI_CLI_SYSTEM_DEFAULTS_PATH"]) if env.get("GEMINI_CLI_SYSTEM_DEFAULTS_PATH") \
        else SYSTEM / "system-defaults.json"
    user = folder(path) / "settings.json"
    out = [("defaults", defaults), ("user", user)]
    if cwd is not None and canonical(folder(cwd) / "settings.json") != canonical(user):
        out.append(("workspace", folder(cwd) / "settings.json"))
    return out + [("system", system)]


def method(path: Path, env: dict, cwd: Path | None = None) -> tuple[str | None, Path | None]:
    """The sign-in method Gemini CLI uses in this home (security.auth.selectedType, the strongest settings file that
    names one; older settings call it selectedAuthType), and the file that says so. (None, None): not chosen yet."""
    found: tuple[str | None, Path | None] = (None, None)
    for _, path_ in settings_files(path, env, cwd):
        data = _jsonc(path_)
        if not isinstance(data, dict):
            continue
        auth = (data.get("security") or {}).get("auth") if isinstance(data.get("security"), dict) else None
        value = auth.get("selectedType") if isinstance(auth, dict) else None
        value = value if isinstance(value, str) else data.get("selectedAuthType")
        if isinstance(value, str) and value:
            found = (value, path_)
    return found


def _claims(id_token) -> dict:
    if not isinstance(id_token, str) or id_token.count(".") != 2:
        return {}
    part = id_token.split(".")[1]
    try:
        claims = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    except ValueError:
        return {}
    return claims if isinstance(claims, dict) else {}


def read_login(path: Path) -> tuple[dict | None, str]:
    """The Google sign-in in a home: {"email", "expiry", "kind"} (labels only, no token), or None and why not. The email
    is the id_token's (Google's own words), else the account Gemini CLI noted when it signed in."""
    creds = folder(path) / "oauth_creds.json"
    try:
        data = json.loads(creds.read_text())
    except FileNotFoundError:
        return None, f"not signed in with Google in {tilde(folder(path))}"
    except (OSError, UnicodeDecodeError, ValueError):
        return None, f"{tilde(creds)} is unreadable"
    if not isinstance(data, dict):
        return None, f"{tilde(creds)} is unreadable"
    kind = data.get("type") or "authorized_user"
    email = _claims(data.get("id_token")).get("email")
    if not isinstance(email, str):
        try:
            noted = json.loads((folder(path) / "google_accounts.json").read_text())
            email = noted.get("active") if isinstance(noted, dict) else None
        except (OSError, UnicodeDecodeError, ValueError):
            email = None
    expiry = data.get("expiry_date")
    return {"email": email if isinstance(email, str) and EMAIL.fullmatch(email) else None, "kind": kind,
            "expiry": expiry / 1000 if isinstance(expiry, (int, float)) else None}, ""


def access_token(path: Path) -> str | None:
    """The sign-in's access token while it is valid (for the remote check only; never stored or shown)."""
    try:
        data = json.loads((folder(path) / "oauth_creds.json").read_text())
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    token, expiry = (data.get("access_token"), data.get("expiry_date")) if isinstance(data, dict) else (None, None)
    if not isinstance(token, str) or not token or not isinstance(expiry, (int, float)):
        return None
    return token if expiry / 1000 > time.time() + EXPIRY_MARGIN else None


def scrub(conn, env: dict) -> None:
    """Where Gemini CLI starts: remove what would pick another sign-in, then preselect the account's method in Gemini
    CLI's dialog and hand it the account's Google Cloud project."""
    for var in [v for v in env if v in SCRUB or v.startswith(SCRUB_PREFIXES)]:
        env.pop(var)
    if not is_key(conn):
        env.pop(KEY_VAR, None)
    env["GEMINI_DEFAULT_AUTH_TYPE"] = API_KEY if is_key(conn) else GOOGLE
    if isinstance(conn.get("cloud_project"), str) and conn["cloud_project"]:
        env["GOOGLE_CLOUD_PROJECT"] = conn["cloud_project"]


def seed(path: Path, env: dict, key: bool = False) -> list[str]:
    """Create a home for an account: Gemini CLI's folder, owner-only (it keeps the sign-in and the sessions), with your
    context file, commands, skills, agents, policies and extensions symlinked from your own (never the sign-in or your
    settings, which name your sign-in method and servers). A key account's settings pick the API-key method, so Gemini
    CLI never asks for the key and never stores it in its own Keychain item."""
    from ..registry import write_private

    target = folder(path)
    target.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path, 0o700)
    made = []
    source = folder(_home(env))
    for item in SHARED:
        link = target / item
        if (source / item).exists() and not link.exists() and not link.is_symlink():
            link.symlink_to(source / item)
            made.append(item)
    settings = target / "settings.json"
    if key and not settings.exists():
        write_private(settings, json.dumps({"security": {"auth": {"selectedType": API_KEY}}}, indent=2) + "\n")
        made.append("settings.json (API key)")
    return made


def _models(key: str, timeout: float) -> tuple[int, object]:
    import urllib.error  # only when a check goes online: urllib.request and TLS take ~30 ms to import
    import urllib.request

    from .. import http

    req = urllib.request.Request(MODELS, headers={"x-goog-api-key": key, "User-Agent": "keyfit",
                                                  "Accept": "application/json"})
    try:
        with http.urlopen(req, timeout) as resp:
            return resp.status, json.loads(resp.read() or b"null")
    except urllib.error.HTTPError as err:
        try:
            return err.code, json.loads(err.read() or b"null")
        except ValueError:
            return err.code, None


def _tokeninfo(url: str, token: str, timeout: float) -> tuple[int, object]:
    from .gcloud import _fetch

    return _fetch(url, token, timeout)


class GeminiAdapter(Adapter):
    kind = "gemini"

    def __init__(self, fetch=_tokeninfo, models=_models, timeout: float = 15):
        self.fetch = fetch
        self.models = models
        self.timeout = timeout

    def label(self, conn) -> str:
        if is_key(conn):
            return f"{conn.get('name', '?')} · API key"
        return str(conn.get("account") or conn.get("name") or "?")

    def inject(self, conn, env: dict) -> None:
        value = secrets.resolve(conn["key"], env) if is_key(conn) else None  # before an env: key's variable goes
        if KEY_MARK in env:  # a key Keyfit gave another project's session
            env.pop(KEY_VAR, None)
            env.pop(KEY_MARK)
        path = home(conn, env)
        if is_default(path, env):
            env.pop(HOME_VAR, None)
        else:
            env[HOME_VAR] = str(path)
        if is_key(conn):
            env.pop(KEY_VAR, None)
            if value:
                env[KEY_VAR] = value
                env[KEY_MARK] = "1"
        env[MARK] = conn.get("name", "")

    def forget(self, env: dict) -> None:
        if MARK in env:  # Keyfit pointed Gemini CLI at another project's home
            for var in (HOME_VAR, MARK):
                env.pop(var, None)
        if KEY_MARK in env:
            for var in (KEY_VAR, KEY_MARK):
                env.pop(var, None)

    def fingerprint(self, conn, env: dict) -> str:
        """Changes with the sign-in (a new one, a renewed token) and the settings that pick the method, or with the key,
        so a cached check never vouches for another sign-in or key."""
        path = home(conn, env)
        seen = [hashlib.sha256(env.get(KEY_VAR, "").encode()).hexdigest()[:16] if is_key(conn) else ""]
        for item in ("oauth_creds.json", "google_accounts.json", "settings.json"):
            try:
                st = (folder(path) / item).stat()
            except OSError:
                continue
            seen.append(f"{item}:{st.st_mtime_ns}:{st.st_size}")
        return hashlib.sha256("|".join(seen).encode()).hexdigest()[:16]

    def local(self, conn, env: dict) -> Check:
        label = self.label(conn)
        name = conn.get("name", "<name>")
        path = home(conn, env)

        def fail(detail: str, fix: str) -> Check:
            return Check(self.kind, "fail", label, detail, fix)

        if not path.is_dir():
            return fail(f"Gemini CLI folder {tilde(path)} does not exist",
                        f"keyfit add gemini --name {name} " + ("--key" if is_key(conn) else "--new"))
        chosen, where = method(path, env, Path.cwd())
        wanted = API_KEY if is_key(conn) else GOOGLE
        if (chosen or wanted) != wanted or (is_key(conn) and chosen is None):
            said = f"is set to {METHODS.get(chosen, printable(chosen))} in {tilde(where)}" if chosen else \
                f"has no sign-in method chosen in {tilde(folder(path))}"
            return fail(f"Gemini CLI {said}, not {METHODS[wanted]}",
                        f'set "security": {{"auth": {{"selectedType": "{wanted}"}}}} in '
                        f"{tilde(where or folder(path) / 'settings.json')}")
        if is_key(conn):
            if not env.get(KEY_VAR):
                return fail(f"the API key ({secrets.describe(conn['key'])}) is missing", "keyfit add gemini --key")
            return Check(self.kind, "ok", label, "", pending=True)
        account = conn.get("account")
        if not isinstance(account, str) or not EMAIL.fullmatch(account):
            return fail("the project has no Google account", "keyfit add google --email <the project's Google account>")
        login, reason = read_login(path)
        if login is None:
            return fail(reason, sign_in(path, env))
        creds = folder(path) / "oauth_creds.json"
        again = f"keyfit add gemini --name {name} --new   (a Gemini CLI folder of the project's own; sign in there)" \
            if is_default(path, env) else f"rm {tilde(creds)}, then: {sign_in(path, env)}"
        if login["kind"] != "authorized_user":
            return fail(f"{tilde(creds)} holds {printable(login['kind'])} credentials, not a Google sign-in", again)
        if login["email"] is None:
            return fail(f"cannot tell which Google account {tilde(creds)} belongs to", again)
        if not same(login["email"], account):
            return fail(f"Gemini CLI is signed in as {printable(login['email'])}, the project's Google account is "
                        f"{account}", again)
        return Check(self.kind, "ok", label, "Google sign-in" + ("" if is_default(path, env) else f" in {tilde(path)}"),
                     pending=True)

    def remote(self, conn, env: dict) -> Check:
        label = self.label(conn)
        if is_key(conn):
            return self._key(label, env.get(KEY_VAR, ""))
        path = home(conn, env)
        token = access_token(path)
        if token is None:  # renewing it takes Gemini CLI's own OAuth client: Gemini CLI does when it starts
            return Check(self.kind, "ok", label, "not confirmed online (the token has expired; Gemini CLI renews it)")
        account = conn["account"]
        creds = folder(path) / "oauth_creds.json"
        again = f"rm {tilde(creds)}, then: {sign_in(path, env)}"
        try:
            status, body = self.fetch(TOKENINFO, token, self.timeout)
        except (OSError, ValueError) as err:
            return Check(self.kind, "fail", label,
                         f"Google did not answer ({getattr(err, 'reason', None) or type(err).__name__})",
                         "check the network and retry", transient=True)
        if status in (400, 401):
            return Check(self.kind, "fail", label, "Google does not accept the sign-in's token (signed out or revoked)",
                         again)
        email = body.get("email") if isinstance(body, dict) else None
        if status != 200 or not isinstance(email, str):
            return Check(self.kind, "fail", label, f"Google's tokeninfo answered {status}", "check the network and retry",
                         transient=status >= 500 or status == 429)
        if not same(email, account):
            return Check(self.kind, "fail", label, f"the sign-in's token belongs to {printable(email)}, the project's "
                                                   f"Google account is {account}", again)
        return Check(self.kind, "ok", label, "confirmed by Google")

    def _key(self, label: str, key: str) -> Check:
        try:
            status, body = self.models(key, self.timeout)
        except (OSError, ValueError) as err:
            return Check(self.kind, "fail", label,
                         f"Google did not answer ({getattr(err, 'reason', None) or type(err).__name__})",
                         "check the network and retry", transient=True)
        if status == 200:
            return Check(self.kind, "ok", label, "key accepted by Google")
        if status in (400, 401):
            return Check(self.kind, "fail", label, "Google does not accept the API key (invalid, expired or deleted)",
                         "keyfit add gemini --key   (paste a key from aistudio.google.com/apikey)")
        if status == 403:
            return Check(self.kind, "fail", label, "the API key may not use the Gemini API (restricted, or the API is "
                                                   "off in its Google Cloud project)",
                         "allow the Generative Language API for the key, or: keyfit add gemini --key")
        return Check(self.kind, "fail", label, f"Google's Gemini API answered {status}", "check the network and retry",
                     transient=status >= 500 or status == 429)
