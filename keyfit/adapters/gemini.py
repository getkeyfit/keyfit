"""Gemini CLI: which Google sign-in (or Gemini API key) a project's Gemini CLI sessions run on.

Account:  [accounts.gemini.work]
          home = "~/.config/keyfit/gemini/work"   (Gemini CLI's home, which holds .gemini/; "~" is your own)
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
          keep them. GEMINI_DEFAULT_AUTH_TYPE preselects the account's method in Gemini CLI's sign-in dialog. Gemini CLI
          loads one .env file (the nearest, from its folder up to /, else its home's) for each variable not set:
          the local check fails when that file would bring one of them back. In the project's own .env (in one of
          its folders) a key, Google Cloud project or method its app uses (APP_SETTINGS) only warns.
forget  — in a project without a Gemini CLI account, what Keyfit set for another project goes.
local   — no .env file Gemini CLI may load sets a variable Keyfit removed (only the names are read); the project's
          own .env setting its app's key, project or method gets a warning. Google: the
          home's sign-in method is Google's (or not chosen yet), it holds a Google sign-in, and that sign-in's email
          is the project's Google account. Key: the key is there and the home uses the API-key method. Only labels
          are read, never a token.
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
         "GEMINI_DEFAULT_AUTH_TYPE", "GEMINI_CLI_USE_COMPUTE_ADC", "GEMINI_FORCE_ENCRYPTED_FILE_STORAGE",
         "CODE_ASSIST_ENDPOINT")
SCRUB_PREFIXES = ("GOOGLE_EXTERNAL_ACCOUNT_",)
# Of those, what a project's own .env sets for its app (a key or credential, its Google Cloud project and location,
# google-genai's method switches): there only a warning. Every other name (an endpoint, Gemini CLI's home, the
# credential store, an external account) fails the check wherever it is set: a repository must never send the sign-in's
# token to another host or move the sign-in.
APP_SETTINGS = ("GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_APPLICATION_CREDENTIALS", "GOOGLE_CREDENTIALS",
                "GOOGLE_CLOUD_ACCESS_TOKEN", "GOOGLE_CLOUD_PROJECT", "GOOGLE_CLOUD_PROJECT_ID", "GOOGLE_CLOUD_LOCATION",
                "GOOGLE_CLOUD_QUOTA_PROJECT", "GOOGLE_GENAI_USE_VERTEXAI", "GOOGLE_GENAI_USE_GCA")
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
        return json.loads(strip(path.read_text(encoding="utf-8-sig")))
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


def sign_in_again(conn, path: Path, env: dict) -> str:
    """The fix when the home holds another account's sign-in: Gemini CLI keeps using a sign-in that still works, so it
    must go first; in your own home, Keyfit never suggests removing yours, but a home of the project's own."""
    if is_default(path, env):
        return f"keyfit add gemini --name {conn.get('name', '<name>')} --new   (a Gemini CLI folder of the project's own; " \
               f"sign in there)"
    return f"rm {tilde(folder(path) / 'oauth_creds.json')}, then: {sign_in(path, env)}"


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


def _env_file(start: Path, path: Path, trusted: bool, ignore: bool) -> Path | None:
    """The .env file Gemini CLI loads when it starts in *start* with the home *path* (its findEnvFile, 0.64.0): the
    first from the folder up to /, .gemini/.env (in a trusted folder) before .env (with --ignore-env, only the home's),
    else the home's .gemini/.env (trusted) or .env."""
    for here in (start, *start.parents):
        if trusted and (folder(here) / ".env").exists():
            return folder(here) / ".env"
        if (here / ".env").exists() and (not ignore or here == path):
            return here / ".env"
    last = ([folder(path) / ".env"] if trusted else []) + [path / ".env"]
    return next((file for file in last if file.exists()), None)


def dotenv_files(start: Path, path: Path) -> list[Path]:
    """The .env files Gemini CLI may load in *start*: whether the folder is trusted, and --ignore-env, are not known
    here, so the one each of those starts would load counts (a false alarm, never a miss)."""
    found: list[Path] = []
    for trusted in (True, False):
        for ignore in (False, True):
            file = _env_file(canonical(start), canonical(path), trusted, ignore)
            if file is not None and file not in found:
                found.append(file)
    return found


def _excluded(path: Path, env: dict) -> set:
    """advanced.excludedEnvVars of your settings and the system's (Gemini CLI merges them): names it never loads from a
    .env outside a .gemini folder. The folder's own settings do not count: an untrusted folder's are ignored."""
    names: set = set()
    for _, file in settings_files(path, env):
        data = _jsonc(file)
        advanced = data.get("advanced") if isinstance(data, dict) else None
        listed = advanced.get("excludedEnvVars") if isinstance(advanced, dict) else None
        if isinstance(listed, list):
            names.update(name for name in listed if isinstance(name, str))
    return names


def reloaded(conn, env: dict, start: Path) -> list[tuple[Path, list[str]]]:
    """(file, names) for each .env file Gemini CLI may load in *start* that sets a variable Keyfit removed where Gemini
    CLI starts and left unset: Gemini CLI would load it, and so use another key, sign-in method, Google Cloud project,
    endpoint or home. Only names are read: a value is never kept or shown."""
    from .cloudflare import DOTENV_LINE

    started = dict(env)
    scrub(conn, started)
    path = home(conn, env)
    excluded = None
    found = []
    for file in dotenv_files(start, path):
        try:
            if not file.is_file():  # a folder: Gemini CLI cannot read it either
                continue
            text = file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        seen = {m.group(1) for m in map(DOTENV_LINE.match, text.splitlines()) if m}
        names = sorted(name for name in seen if name not in started and (
            name in SCRUB or name.startswith(SCRUB_PREFIXES) or name in (KEY_VAR, HOME_VAR)))
        if names and ".gemini" not in str(file):
            excluded = _excluded(path, env) if excluded is None else excluded
            names = [name for name in names if name not in excluded]
        if names:
            found.append((file, names))
    return found


def projects_own(file: Path, env: dict) -> bool:
    """Whether *file* lies in a folder of the session's project (KEYFIT_PROJECT): under one of its roots, or in a folder
    that resolves to it (a worktree). Such a .env holds the project's own settings, not another client's."""
    from ..registry import Registry
    from ..resolve import GitError, resolve

    name = env.get("KEYFIT_PROJECT")
    if not name:
        return False
    here = canonical(file.parent)
    try:
        registry = Registry.load()
        project = registry.projects().get(name)
        if project is None:
            return False
        if any(here == root or root in here.parents for root in project.canonical_roots()):
            return True
        found = resolve(here, registry)  # git, only for a .env outside the roots that sets such a name
    except (GitError, OSError, ValueError):
        return False
    return found.project is not None and found.project.name == name


def reloaded_problem(found: list[tuple[Path, list[str]]], path: Path, own: bool = False) -> tuple[str, str]:
    """(detail, fix) for .env files that bring back what Keyfit removed: the files and the names, never a value. *own*:
    the project's own files, which get a warning."""
    files = "; ".join(f"{printable(tilde(file))} sets {', '.join(names)}" for file, names in found)
    every = sorted({name for _, listed in found for name in listed})
    where = printable(tilde(found[0][0])) if len(found) == 1 else "those files"
    fix = f"remove {', '.join(every)} from {where}"
    if not any(".gemini" in str(file) for file, _ in found):  # Gemini CLI skips the names listed there in such a file
        settings = tilde(folder(path) / "settings.json")
        fix += f', or add "advanced": {{"excludedEnvVars": {json.dumps(every)}}} to {settings}'
    it = "it" if len(every) == 1 else "them"
    if own:
        return f"{files}: Gemini CLI uses {it} instead of the project's Gemini CLI account", fix
    return f"{files}, which Gemini CLI would load although Keyfit removed {it} for the project's account", fix


def seed(path: Path, env: dict, key: bool = False) -> list[str]:
    """Create a home for an account: Gemini CLI's folder, owner-only (it keeps the sign-in and the sessions), with your
    context file, commands, skills, agents, policies and extensions symlinked from your own (never the sign-in or your
    settings, which name your sign-in method and servers); returns what it linked. A key account's settings pick the
    API-key method, so Gemini CLI never asks for the key and never stores it in its own Keychain item."""
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
        own, others = [], []
        for file, names in reloaded(conn, env, Path.cwd()):  # first: Gemini CLI's sign-in would load the file too
            app = [name for name in names if name in APP_SETTINGS]
            if app and not projects_own(file, env):  # outside the project's folders: another account's key, say
                app = []
            if app:
                own.append((file, app))
            if len(app) < len(names):
                others.append((file, [name for name in names if name not in app]))
        if others:
            return fail(*reloaded_problem(others, path))

        def ok(detail: str) -> Check:
            if not own:
                return Check(self.kind, "ok", label, detail, pending=True)
            warning, fix = reloaded_problem(own, path, own=True)
            return Check(self.kind, "warn", label, " · ".join(d for d in (detail, warning) if d), fix, pending=True)

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
            return ok("")
        account = conn.get("account")
        if not isinstance(account, str) or not EMAIL.fullmatch(account):
            return fail("the project has no Google account", "keyfit add google --email <the project's Google account>")
        login, reason = read_login(path)
        if login is None:
            return fail(reason, sign_in(path, env))
        creds = folder(path) / "oauth_creds.json"
        again = sign_in_again(conn, path, env)
        if login["kind"] != "authorized_user":
            return fail(f"{tilde(creds)} holds {printable(login['kind'])} credentials, not a Google sign-in", again)
        if login["email"] is None:
            return fail(f"cannot tell which Google account {tilde(creds)} belongs to", again)
        if not same(login["email"], account):
            return fail(f"Gemini CLI is signed in as {printable(login['email'])}, the project's Google account is "
                        f"{account}", again)
        return ok("Google sign-in" + ("" if is_default(path, env) else f" in {tilde(path)}"))

    def remote(self, conn, env: dict) -> Check:
        label = self.label(conn)
        if is_key(conn):
            return self._key(label, env.get(KEY_VAR, ""))
        path = home(conn, env)
        token = access_token(path)
        if token is None:  # renewing it takes Gemini CLI's own OAuth client: Gemini CLI does when it starts
            return Check(self.kind, "ok", label, "not confirmed online (the token has expired; Gemini CLI renews it)")
        account = conn["account"]
        try:
            status, body = self.fetch(TOKENINFO, token, self.timeout)
        except (OSError, ValueError) as err:
            return Check(self.kind, "fail", label,
                         f"Google did not answer ({getattr(err, 'reason', None) or type(err).__name__})",
                         "check the network and retry", transient=True)
        if status in (400, 401):  # Gemini CLI signs in afresh when its sign-in no longer works
            return Check(self.kind, "fail", label, "Google does not accept the sign-in's token (signed out or revoked)",
                         sign_in(path, env))
        email = body.get("email") if isinstance(body, dict) else None
        if status != 200 or not isinstance(email, str):
            return Check(self.kind, "fail", label, f"Google's tokeninfo answered {status}", "check the network and retry",
                         transient=status >= 500 or status == 429)
        if not same(email, account):
            return Check(self.kind, "fail", label, f"the sign-in's token belongs to {printable(email)}, the project's "
                                                   f"Google account is {account}", sign_in_again(conn, path, env))
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
