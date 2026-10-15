"""Cloudflare: which wrangler sign-in (auth profile) and Cloudflare account a project deploys with.

Account:  [accounts.cloudflare.acme]
          profile = "acme"   (a wrangler auth profile; "default" is the sign-in of `wrangler login`)
          account_id = "<32 hex digits>"   account_name = "Acme"
Project:  cloudflare = "acme"

wrangler keeps named sign-ins since 4.106 (June 2026): `wrangler auth create NAME` signs in, `wrangler auth activate
NAME DIR` binds a profile to a folder and its subfolders. Without --profile, wrangler uses the profile of the nearest
bound folder (the folder it reads its config from), else the default profile. CLOUDFLARE_API_TOKEN (or
CLOUDFLARE_API_KEY with CLOUDFLARE_EMAIL; older names CF_…) beats every profile, also from the folder's .env or
.env.local, which wrangler loads when the variable is not set. The account is account_id in the
wrangler config (wrangler.json, wrangler.jsonc, wrangler.toml, each looked for upwards in that order), else
CLOUDFLARE_ACCOUNT_ID, else the one picked at sign-in; an account the sign-in cannot reach fails, it never falls back.
Profiles live in wrangler's config folder (~/.wrangler when it exists, else ~/Library/Preferences/.wrangler):
config/<profile>.toml, or .enc when wrangler keeps the key in the Keychain, and profiles/directory-bindings.json
(folder → profile, compared as text). Keyfit reads the bindings and the profile names, never a sign-in, and keeps no
Cloudflare token of its own: wrangler switches, Keyfit checks and enforces.

inject  — remove inherited credentials that would beat the profile (CLOUDFLARE_API_TOKEN, CLOUDFLARE_API_KEY,
          CLOUDFLARE_EMAIL and their CF_ names) and set CLOUDFLARE_ACCOUNT_ID to the account, so every wrangler the
          session starts (`npx wrangler` from node_modules too) works on it or fails loudly. The folder's binding
          picks the sign-in.
forget  — in a project without a Cloudflare account, the CLOUDFLARE_ACCOUNT_ID Keyfit set for another project goes.
local   — the profile exists; the folder resolves to it (bound there or above, not shadowed by a subfolder's
          binding); the wrangler config wrangler would read sets no other account_id (top level and env.*); the
          wrangler `npx wrangler` would run here is new enough for profiles.
remote  — `wrangler whoami --json` in the folder (wrangler renews an expired sign-in first, as every command does)
          must list the account.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from ..checks import Check, printable
from ..realbin import find_real
from ..registry import tilde
from .base import Adapter

API = "https://api.cloudflare.com/client/v4"
ACCOUNT = re.compile(r"[0-9a-f]{32}")  # a Cloudflare account ID (use fullmatch)
PROFILE = re.compile(r"[A-Za-z0-9_-]+")  # wrangler's profile names (use fullmatch)
DEFAULT = "default"  # the profile of `wrangler login`; "staging" is reserved too
MINIMUM = (4, 106, 0)  # the first wrangler with auth profiles
MARK = "KEYFIT_CLOUDFLARE"  # set next to the CLOUDFLARE_ACCOUNT_ID Keyfit injected: the account's name
# Credentials wrangler takes before any profile, and the account variable Keyfit sets itself (older CF_ names too).
SCRUB = ("CLOUDFLARE_API_TOKEN", "CF_API_TOKEN", "CLOUDFLARE_API_KEY", "CF_API_KEY", "CLOUDFLARE_EMAIL", "CF_EMAIL",
         "CLOUDFLARE_ACCOUNT_ID", "CF_ACCOUNT_ID")
CONFIGS = ("wrangler.json", "wrangler.jsonc", "wrangler.toml")
BINDINGS = Path("profiles") / "directory-bindings.json"
# Options that take a value (yargs: `--name value`, `--name=value`, `-c value`): wrangler's global ones and those of
# the commands Keyfit looks at (login, auth create, whoami). Every other option is read as a flag, so
# `wrangler --x-provision login` is still `login`.
VALUED = {"-c", "--config", "--cwd", "-e", "--env", "--env-file", "--profile", "--scopes", "--callback-host",
          "--callback-port", "--account"}
OPTIONS = {"-c": "--config", "--config": "--config", "--cwd": "--cwd", "--profile": "--profile"}
UNREACHABLE = ("could not be reached", "fetch failed", "enotfound", "econnrefused", "econnreset", "etimedout",
               "eai_again", "getaddrinfo", "network")
SIGNED_OUT = ("not logged in", "not authenticated", "could not be refreshed", "authentication error",
              "invalid access token", "no credentials were found")


def _home(env: dict) -> Path:
    return Path(env.get("HOME") or Path.home())


def store(env: dict) -> Path:
    """wrangler's config folder: ~/.wrangler when that folder exists, else $XDG_CONFIG_HOME/.wrangler, else
    ~/Library/Preferences/.wrangler (on Linux ~/.config/.wrangler)."""
    legacy = _home(env) / ".wrangler"
    if legacy.is_dir():
        return legacy
    if env.get("XDG_CONFIG_HOME"):
        return Path(env["XDG_CONFIG_HOME"]) / ".wrangler"
    return _home(env) / ("Library/Preferences" if sys.platform == "darwin" else ".config") / ".wrangler"


def profiles(env: dict) -> list[str]:
    """The profiles wrangler has on this Mac (what `wrangler auth list` shows), default first."""
    try:
        files = os.listdir(store(env) / "config")
    except OSError:
        return []
    names = {f.rsplit(".", 1)[0] for f in files if f.endswith((".toml", ".enc"))}
    return sorted((n for n in names if PROFILE.fullmatch(n) and n != "staging"), key=lambda n: (n != DEFAULT, n))


def has_profile(env: dict, profile: str) -> bool:
    folder = store(env) / "config"
    return any((folder / f"{profile}{ext}").is_file() for ext in (".toml", ".enc"))


def bindings(env: dict) -> dict[str, str]:
    """Folder → profile, as `wrangler auth activate` wrote them."""
    try:
        data = json.loads((store(env) / BINDINGS).read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {d: p for d, p in data.items() if isinstance(d, str) and isinstance(p, str)}


def bound(folder, table: dict[str, str]) -> tuple[str, str | None]:
    """(profile, folder it is bound at) that wrangler uses in *folder* without --profile; (default, None) when no
    folder at or above it is bound. Like wrangler: the longest bound folder wins, compared as text."""
    here = os.path.abspath(folder)
    for d in sorted(table, key=len, reverse=True):
        if here == d or (here.startswith(d) and here[len(d):len(d) + 1] == os.sep):
            return table[d], d
    return DEFAULT, None


def find_config(folder) -> Path | None:
    """The config file wrangler reads in *folder*: wrangler.json anywhere upwards, else wrangler.jsonc, else .toml."""
    start = Path(os.path.abspath(folder))
    for name in CONFIGS:
        for d in (start, *start.parents):
            if (d / name).is_file():
                return d / name
    return None


def _jsonc(text: str) -> str:
    """JSON with comments and trailing commas (wrangler.json and .jsonc allow both) as plain JSON."""
    out, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if c == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            out.append(text[i:j + 1])
            i = j + 1
        elif text.startswith("//", i):
            i = text.find("\n", i) if "\n" in text[i:] else n
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end < 0 else end + 2
        elif c == ",":
            j = i + 1
            while j < n and (text[j].isspace() or text.startswith(("//", "/*"), j)):
                if text.startswith("//", j):
                    j = text.find("\n", j) if "\n" in text[j:] else n
                elif text.startswith("/*", j):
                    end = text.find("*/", j + 2)
                    j = n if end < 0 else end + 2
                else:
                    j += 1
            if j >= n or text[j] not in "}]":
                out.append(c)
            i += 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


def account_ids(path: Path) -> list[tuple[str, object]] | None:
    """(where, value) of every account_id in a wrangler config: the top level ("") and each env.NAME. None when the
    file cannot be read (wrangler then stops by itself)."""
    import tomllib

    try:
        text = path.read_text()
        data = tomllib.loads(text) if path.suffix == ".toml" else json.loads(_jsonc(text))
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    found = [("", data["account_id"])] if "account_id" in data else []
    envs = data.get("env")
    if isinstance(envs, dict):
        found += [(f"env.{name}", body["account_id"]) for name, body in envs.items()
                  if isinstance(body, dict) and "account_id" in body]
    return found


def wrangler_bin(env: dict, start) -> str | None:
    """The wrangler `npx wrangler` runs in *start*: the nearest node_modules/.bin/wrangler (up to the home folder),
    else the one on PATH."""
    home = _home(env)
    folder = Path(os.path.abspath(start))
    for d in (folder, *folder.parents):
        candidate = d / "node_modules" / ".bin" / "wrangler"
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
        if d == home:
            break
    return find_real("wrangler", env, skip_wrappers=True)


def version(binary: str) -> tuple[int, ...] | None:
    """wrangler's version from the package.json next to its bin/ (npm and Homebrew installs); None when unknown."""
    try:
        data = json.loads((Path(os.path.realpath(binary)).parent.parent / "package.json").read_text())
    except (OSError, ValueError):
        return None
    match = re.match(r"(\d+)\.(\d+)\.(\d+)", str(data.get("version", ""))) if isinstance(data, dict) else None
    if not match or data.get("name") != "wrangler":
        return None
    return tuple(int(part) for part in match.groups())


def shell_path(path) -> str:
    """A folder as a shell argument: ~/… for the home folder, with special characters escaped (GTM\\ Engine)."""
    return re.sub(r"[^\w@%+=:,./~-]", lambda m: "\\" + m.group(), tilde(path))


def superset_parent(folder, env: dict) -> str | None:
    """The folder that holds *folder* when it is a Superset worktree (~/.superset/worktrees/<repository>/<name>, or
    ~/.superset-*/… of another Superset build): binding it once covers every worktree of the repository."""
    parent = os.path.dirname(os.path.abspath(folder))
    worktrees = Path(parent).parent
    if worktrees.name != "worktrees" or not re.fullmatch(r"\.superset(-.+)?", worktrees.parent.name):
        return None
    return parent if os.path.realpath(worktrees.parent.parent) == os.path.realpath(_home(env)) else None


def sign_in(profile: str) -> str:
    return "wrangler login" if profile == DEFAULT else f"wrangler auth create {profile}"


def add_command(conn) -> str:
    return f"keyfit add cloudflare --name {conn.get('name', '<name>')}"


def _top(folder: str) -> str:
    """The folder to bind: the top of the git checkout (or worktree) *folder* is in, else *folder* itself."""
    try:
        out = subprocess.run(["git", "-C", folder, "rev-parse", "--show-toplevel"], capture_output=True, text=True,
                             timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return folder
    return out.stdout.strip() if out.returncode == 0 and out.stdout.strip() else folder


def mismatch(conn, folder, env: dict, asked: str | None = None, config: Path | None = None) -> tuple[str, str] | None:
    """Why wrangler started in *folder* would not use the account's profile or account: (detail, fix), else None.
    *asked*: a --profile given on the command line; *config*: a --config file (else the one wrangler finds)."""
    profile, expected = conn["profile"], conn["account_id"]
    title = conn.get("account_name") or expected
    folder = os.path.abspath(folder)
    if asked is None:
        used, where = bound(folder, bindings(env))
        if used != profile:
            top = _top(folder)
            home = str(_home(env).resolve())
            if top == home or home.startswith(top.rstrip(os.sep) + os.sep):  # never bind everything under it
                return (f"wrangler in {tilde(folder)} would use profile {used}, not {profile}",
                        "run wrangler inside the project's folder")
            if where is None:
                return (f"{tilde(folder)} is not bound to wrangler profile {profile} (wrangler would use its default "
                        f"login)", f"wrangler auth activate {profile} {shell_path(top)}")
            if profile == DEFAULT:
                return (f"{tilde(folder)} uses wrangler profile {used} (bound at {tilde(where)}), project expects "
                        f"wrangler's default login", f"wrangler auth deactivate {shell_path(where)}")
            inside = where == top or where.startswith(top.rstrip(os.sep) + os.sep)
            return (f"{tilde(folder)} uses wrangler profile {used} (bound at {tilde(where)}), project expects {profile}",
                    f"wrangler auth activate {profile} {shell_path(where if inside else top)}")
    path = config or find_config(folder)
    for where, value in (account_ids(path) or []) if path else []:
        if value != expected:
            shown = printable(value) + (f" in [{where}]" if where else "")
            return (f"{tilde(path)} sets account_id {shown}, project expects {title} ({expected})",
                    f"set account_id = \"{expected}\" there; if the project moved: keyfit add cloudflare --profile "
                    f"{profile} --account {printable(value)}")
    return None


def options(args: list[str]) -> list[tuple[str, str | None]]:
    """(long name, value) of each --profile, -c/--config and --cwd option, read like wrangler's parser (yargs):
    `--name value`, `--name=value`, `-c value`, anywhere before `--`."""
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
            found.append((OPTIONS[name], value))
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


def _json(text: str):
    """The first JSON object in a command's output (wrangler may print a line or two around it)."""
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", text):
        try:
            return decoder.raw_decode(text, match.start())[0]
        except ValueError:
            continue
    return None


def _plain(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text)


def wrangler_env(env: dict) -> dict:
    """The environment Keyfit runs wrangler with for a check: no credentials that would beat the profile, no metrics,
    no colours."""
    run = {k: v for k, v in env.items() if k not in SCRUB or k == "CLOUDFLARE_ACCOUNT_ID"}
    run.update(WRANGLER_SEND_METRICS="false", NO_COLOR="1", FORCE_COLOR="0")
    return run


def _run(argv: list[str], cwd: str, env: dict, timeout: float) -> tuple[int, str, str]:
    out = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout,
                         stdin=subprocess.DEVNULL, check=False)
    return out.returncode, out.stdout, out.stderr


def _fetch(path: str, token: str, timeout: float) -> tuple[int, object]:
    import urllib.error  # only for `keyfit add cloudflare`: urllib.request and TLS take ~30 ms to import
    import urllib.request

    from .. import http

    req = urllib.request.Request(API + path, headers={"Authorization": f"Bearer {token}", "User-Agent": "keyfit",
                                                      "Accept": "application/json"})
    try:
        with http.urlopen(req, timeout) as resp:
            return resp.status, json.loads(resp.read() or b"null")
    except urllib.error.HTTPError as err:
        return err.code, None


class CloudflareAdapter(Adapter):
    kind = "cloudflare"

    def __init__(self, run=_run, fetch=_fetch, timeout: float = 15, cli_timeout: float = 30):
        self.run = run
        self.fetch = fetch
        self.timeout = timeout
        self.cli_timeout = cli_timeout

    def label(self, conn) -> str:
        profile = conn.get("profile") or "?"
        account = conn.get("account_name") or conn.get("account_id")
        return f"{profile} → {account}" if account else profile

    def inject(self, conn, env: dict) -> None:
        for var in SCRUB:
            env.pop(var, None)
        account = conn.get("account_id")
        if isinstance(account, str) and ACCOUNT.fullmatch(account):
            env["CLOUDFLARE_ACCOUNT_ID"] = account
        env[MARK] = conn.get("name", "")

    def forget(self, env: dict) -> None:
        if MARK in env:  # Keyfit set the account for another project
            for var in ("CLOUDFLARE_ACCOUNT_ID", MARK):
                env.pop(var, None)

    def fingerprint(self, conn, env: dict) -> str:
        """Changes with the profile's sign-in file (a new sign-in, or one wrangler renewed), so a cached check never
        vouches for another sign-in, and with the wrangler that checks it (one installed since)."""
        seen = [wrangler_bin(env, os.getcwd()) or ""]
        for ext in (".toml", ".enc"):
            try:
                st = (store(env) / "config" / f"{conn.get('profile')}{ext}").stat()
            except OSError:
                continue
            seen.append(f"{ext}:{st.st_mtime_ns}:{st.st_size}")
        return hashlib.sha256("|".join(seen).encode()).hexdigest()[:16]

    def local(self, conn, env: dict) -> Check:
        label = self.label(conn)
        profile, expected = conn.get("profile"), conn.get("account_id")
        if not isinstance(profile, str) or not PROFILE.fullmatch(profile):
            return Check(self.kind, "fail", label, "no wrangler profile recorded", "keyfit add cloudflare")
        if not isinstance(expected, str) or not ACCOUNT.fullmatch(expected):
            return Check(self.kind, "fail", label, "the expected account must be a 32-character Cloudflare account ID",
                         add_command(conn))
        if not has_profile(env, profile):
            what = "wrangler is not signed in (no default profile)" if profile == DEFAULT else \
                f"wrangler has no profile {profile} on this Mac"
            return Check(self.kind, "fail", label, what, sign_in(profile))
        folder = os.getcwd()
        problem = mismatch(conn, folder, env)
        if problem:
            return Check(self.kind, "fail", label, *problem)
        binary = wrangler_bin(env, folder)
        found = version(binary) if binary else None
        if found is not None and found < MINIMUM:
            local = f"{os.sep}node_modules{os.sep}" in binary
            return Check(self.kind, "fail", label,
                         f"{tilde(binary)} is wrangler {'.'.join(map(str, found))}, which ignores profiles "
                         f"(4.106 or newer needed)",
                         "npm install --save-dev wrangler@latest" if local else "npm install -g wrangler@latest")
        _, where = bound(folder, bindings(env))
        return Check(self.kind, "ok", label, f"bound at {tilde(where)}" if where else "default login", pending=True)

    def remote(self, conn, env: dict) -> Check:
        label = self.label(conn)
        profile, expected = conn["profile"], conn["account_id"]
        title = conn.get("account_name") or expected
        folder = os.getcwd()
        binary = wrangler_bin(env, folder)
        if binary is None:
            return Check(self.kind, "warn", label, "not checked online: no wrangler here (npm install -g wrangler, "
                                                   "or npm install in the project)")
        accounts, reason, state = self.whoami(binary, folder, env)
        if accounts is None:
            if state == "transient":
                return Check(self.kind, "fail", label, reason, "check the network and retry", transient=True)
            return Check(self.kind, "fail", label, f"wrangler profile {profile}: {reason}", sign_in(profile))
        if expected not in accounts:
            reach = ", ".join(f"{name} ({ident})" for ident, name in accounts.items()) or "none"
            return Check(self.kind, "fail", label,
                         f"wrangler profile {profile} has no access to account {title} ({expected}); it reaches {reach}",
                         f"{sign_in(profile)}   (as a member of {title})")
        return Check(self.kind, "ok", label, "profile and account confirmed")

    # helpers, shared with `keyfit add cloudflare` --------------------------------------------------------------

    def activate(self, binary: str, profile: str, folder: str, env: dict) -> str:
        """Bind *folder* and everything under it to *profile* (`wrangler auth activate`): "" when done, else why not.
        It runs away from the folder, so no .env there hands wrangler a CLOUDFLARE_API_TOKEN (with one set, wrangler
        refuses to change profiles); wrangler neither prompts nor reads a sign-in for it."""
        import tempfile

        try:
            code, out, err = self.run([binary, "auth", "activate", profile, folder], tempfile.gettempdir(),
                                      wrangler_env(env), self.cli_timeout)
        except subprocess.TimeoutExpired:
            return f"wrangler did not answer within {self.cli_timeout:g} s"
        except OSError:
            return f"{tilde(binary)} could not run"
        if code == 0:
            return ""
        lines = [line.strip() for line in _plain(f"{out}\n{err}").splitlines() if line.strip()]
        first = next((line for line in lines if "[ERROR]" in line), lines[-1] if lines else f"exit {code}")
        return printable(re.sub(r"^.*?\[ERROR\]\s*", "", first))[:160]

    def whoami(self, binary: str, folder: str, env: dict) -> tuple[dict | None, str, str]:
        """({account id: name}, "", "ok") of the sign-in wrangler uses in *folder*, or (None, why, state) with state
        "signin" (signed out, or the sign-in expired), "transient" (no answer) or "error"."""
        try:
            code, out, err = self.run([binary, "whoami", "--json"], folder, wrangler_env(env), self.cli_timeout)
        except subprocess.TimeoutExpired:
            return None, (f"wrangler did not answer within {self.cli_timeout:g} s (a Keychain prompt may be waiting — "
                          f"choose Always Allow)"), "transient"
        except OSError:
            return None, f"{tilde(binary)} could not run", "error"
        data = _json(out)
        if code == 0 and isinstance(data, dict) and data.get("loggedIn") and isinstance(data.get("accounts"), list):
            return {a["id"]: str(a.get("name") or a["id"]) for a in data["accounts"]
                    if isinstance(a, dict) and isinstance(a.get("id"), str)}, "", "ok"
        text = _plain(f"{out}\n{err}")
        low = text.lower()
        if any(word in low for word in UNREACHABLE):
            return None, "Cloudflare did not answer (network?)", "transient"
        if "could not be refreshed" in low:
            return None, "the sign-in expired and wrangler could not renew it", "signin"
        answer = data if isinstance(data, dict) else _json(err)  # {"loggedIn": false} when signed out
        if (isinstance(answer, dict) and answer.get("loggedIn") is False) or any(word in low for word in SIGNED_OUT):
            return None, "not signed in", "signin"
        lines = [line.strip(" ✘▲[]") for line in text.splitlines() if line.strip()]
        first = next((line for line in lines if "error" in line.lower()), lines[0] if lines else f"exit {code}")
        return None, f"wrangler whoami failed: {printable(first)[:160]}", "error"

    def accounts_of(self, binary: str, profile: str, env: dict) -> tuple[dict | None, str]:
        """{account id: name} a profile's sign-in reaches, or (None, why): its token from `wrangler auth token`
        (renewed by wrangler when due), then GET /accounts. The token is never stored or shown."""
        import tempfile

        try:
            code, out, _ = self.run([binary, "auth", "token", "--json", "--profile", profile], tempfile.gettempdir(),
                                    wrangler_env(env), self.cli_timeout)  # elsewhere: no .env of the folder
        except subprocess.TimeoutExpired:
            return None, f"wrangler auth token did not answer within {self.cli_timeout:g} s"
        except OSError:
            return None, f"{tilde(binary)} could not run"
        data = _json(out)
        token = data.get("token") if isinstance(data, dict) else None
        if code != 0 or not isinstance(token, str) or not token:
            return None, f"wrangler profile {profile} is not signed in (or its sign-in expired)"
        try:
            status, body = self.fetch("/accounts?per_page=50", token, self.timeout)
        except (OSError, ValueError) as err:
            return None, f"Cloudflare did not answer ({getattr(err, 'reason', None) or type(err).__name__})"
        if status in (401, 403):
            return None, f"Cloudflare rejected the sign-in of profile {profile}"
        result = body.get("result") if isinstance(body, dict) else None
        if status != 200 or not isinstance(result, list):
            return None, f"Cloudflare answered {status}"
        return {a["id"]: str(a.get("name") or a["id"]) for a in result
                if isinstance(a, dict) and isinstance(a.get("id"), str)}, ""
