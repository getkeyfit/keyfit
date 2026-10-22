"""Firebase: which firebase CLI sign-in and Firebase project a project works with.

Account:  [accounts.firebase.acme-prod]
          project = "acme-prod"   (the Firebase project ID it works on)
Project:  firebase = "acme-prod"   and   google = "work"   (the project's Google account: its email reaches this
          connection as `account`, see registry.GOOGLE_TOOLS)

The firebase CLI (verified with firebase-tools 15.26.0) keeps its sign-ins in one file, configstore/firebase-tools.json
under $XDG_CONFIG_HOME, else ~/.config: `user` and `tokens` of the default login (`firebase login`), more in
`additionalAccounts` (`firebase login:add`), and `activeAccounts`, folder → email (`firebase login:use EMAIL`). The
folder is the CLI's project folder: the nearest folder upwards with a firebase.json (or the folder of the one --config
names), matched exactly; without a binding there, and outside any project folder, the default login applies. --account
beats the binding, and FIREBASE_TOKEN (or --token) beats every sign-in. The Firebase project is --project, else the one
`firebase use` picked for the folder (`activeProjects`, the nearest folder upwards), else firebase.json's deprecated
"firebase" key, else in .firebaserc the only alias or `default`; an alias name is looked up in .firebaserc (the project
folder's, else the current folder's). Keyfit reads this file, firebase.json and .firebaserc and keeps no Google token:
the firebase CLI switches, Keyfit checks and enforces.

inject  — remove FIREBASE_TOKEN and GOOGLE_APPLICATION_CREDENTIALS (a token or key file that would act instead of the
          sign-in: Keyfit cannot tell whose), FIREBASE_…_URL (FIREBASE_TOKEN_URL and FIREBASE_GOOGLE_URL get the
          refresh token, the others the requests) and FIREBASE_CLIENT_ID/_SECRET (another OAuth client). Nothing is
          set: the folder's binding picks the sign-in for every firebase the session starts (`npx firebase-tools`
          too).
local   — the firebase CLI is signed in as the project's Google account; in the CLI's project folder that account is
          the one it uses (bound there, or the default login) and the Firebase project it picks is the expected one.
          Outside a project folder (no firebase.json here or above) the project a .firebaserc here picks must be the
          expected one too (commands that need no firebase.json use it), and another default login is only a warning:
          Keyfit's own `firebase` then adds --account.
remote  — Google's tokeninfo must name the account for the access token the CLI keeps for it, and the Firebase
          Management API must show the project to it. An access token about to expire is renewed by the real CLI
          first (`firebase projects:list`, as every firebase command does), then read again. The token is held in
          memory for those requests only.
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
import tempfile
import time
from pathlib import Path

from ..checks import Check, printable
from ..realbin import find_real
from ..registry import tilde
from .base import Adapter
from .cloudflare import _jsonc
from .gcloud import PROJECT, TOKENINFO, _fetch
from .google import EMAIL, same

PROJECTS = "https://firebase.googleapis.com/v1beta1/projects/"
STORE = Path("configstore") / "firebase-tools.json"
SCRUB = ("FIREBASE_TOKEN", "GOOGLE_APPLICATION_CREDENTIALS", "FIREBASE_CLIENT_ID", "FIREBASE_CLIENT_SECRET")
FRESH = 5 * 60  # seconds an access token must still be valid for a check; else the CLI renews it first
# Global options that take a value (commander: `--name value`, `--name=value`, `-P value`, `-Pvalue`), and those of the
# commands Keyfit looks at (login, use).
VALUED = {"-P", "--project", "--account", "--token", "-c", "--config", "--alias", "--unalias"}
OPTIONS = {"-P": "--project", "--project": "--project", "--account": "--account", "--token": "--token",
           "-c": "--config", "--config": "--config"}
FLAGS = {"-j", "--json", "--non-interactive", "-i", "--interactive", "--debug", "-h", "--help", "-V", "--version",
         "--reauth", "--no-localhost", "--add", "--clear"}


def _home(env: dict) -> Path:
    return Path(env.get("HOME") or Path.home())


def scrubbed(var: str) -> bool:
    """Variables the CLI would take instead of the sign-in, or that send the sign-in or its requests elsewhere than to
    Google: SCRUB and every FIREBASE_…_URL (the CLI's endpoints)."""
    return var in SCRUB or (var.startswith("FIREBASE_") and var.endswith("_URL"))


def store_path(env: dict) -> Path:
    """The firebase CLI's sign-ins: configstore/firebase-tools.json under $XDG_CONFIG_HOME, else ~/.config."""
    base = Path(env["XDG_CONFIG_HOME"]) if env.get("XDG_CONFIG_HOME") else _home(env) / ".config"
    return base / STORE


def read_store(env: dict) -> dict | None:
    """The CLI's settings and sign-ins ({} when it has none yet); None when the file cannot be read as JSON."""
    try:
        data = json.loads(store_path(env).read_text())
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def accounts(store: dict) -> list[dict]:
    """Every sign-in, the default login first (`firebase login:list`): {"user": {"email", …}, "tokens": {…}}."""
    found = []
    if isinstance(store.get("user"), dict) and isinstance(store.get("tokens"), dict):
        found.append({"user": store["user"], "tokens": store["tokens"]})
    extra = store.get("additionalAccounts")
    if isinstance(extra, list):
        found += [a for a in extra if isinstance(a, dict) and isinstance(a.get("user"), dict)]
    return [a for a in found if isinstance(a["user"].get("email"), str)]


def emails(store: dict) -> list[str]:
    return [a["user"]["email"] for a in accounts(store)]


def account_of(store: dict, email: str) -> dict | None:
    return next((a for a in accounts(store) if same(a["user"]["email"], email)), None)


def default_login(store: dict) -> str | None:
    """The email of `firebase login`'s sign-in: the one used where no folder is bound."""
    user = store.get("user")
    email = user.get("email") if isinstance(user, dict) and isinstance(store.get("tokens"), dict) else None
    return email if isinstance(email, str) else None


def project_root(folder, config: str | None = None) -> str | None:
    """The CLI's project folder: the folder of the --config file, else the nearest folder upwards with a
    firebase.json; None outside every project folder. Compared as text, like the CLI."""
    here = os.path.abspath(folder)
    if config:
        return os.path.dirname(os.path.normpath(os.path.join(here, config)))
    while not os.path.isfile(os.path.join(here, "firebase.json")):
        parent = os.path.dirname(here)
        if parent == here:
            return None
        here = parent
    return here


def bound(store: dict, root: str | None) -> tuple[str | None, bool]:
    """(email, bound) the CLI signs in as in *root*: the folder's `firebase login:use` (True), else the default
    login."""
    table = store.get("activeAccounts")
    email = table.get(root) if root and isinstance(table, dict) else None
    if isinstance(email, str):
        return email, True
    return default_login(store), False


def aliases(folder: str) -> dict[str, str]:
    """.firebaserc's project aliases in *folder* (alias → project ID); {} when there is none or it does not parse."""
    try:
        data = json.loads(_jsonc((Path(folder) / ".firebaserc").read_text()))
    except (OSError, UnicodeDecodeError, ValueError):
        return {}
    projects = data.get("projects") if isinstance(data, dict) else None
    return {k: v for k, v in projects.items() if isinstance(v, str)} if isinstance(projects, dict) else {}


def selected(store: dict, root: str | None, folder, config: str | None = None) -> tuple[str | None, str]:
    """(project ID, where from) the CLI works on without --project: the one `firebase use` picked for the folder or a
    folder above, else the deprecated "firebase" key of the project folder's firebase.json (or the --config file),
    else .firebaserc's only alias or its default; an alias is looked up in .firebaserc. Without a project folder the
    CLI reads the .firebaserc of *folder*."""
    base = root or os.path.abspath(folder)
    names = aliases(base)
    table = store.get("activeProjects") if isinstance(store.get("activeProjects"), dict) else {}
    here = base
    while True:
        picked = table.get(here)
        if isinstance(picked, str) and picked:
            return names.get(picked, picked), "picked with `firebase use`"
        parent = os.path.dirname(here)
        if parent == here:
            break
        here = parent
    name = os.path.basename(config) if config else "firebase.json"
    try:  # the CLI reads it from the project folder, under the --config file's name
        data = json.loads(_jsonc((Path(root) / name).read_text())) if root else None
    except (OSError, UnicodeDecodeError, ValueError):
        data = None
    legacy = data.get("firebase") if isinstance(data, dict) else None
    if isinstance(legacy, str) and legacy:
        return names.get(legacy, legacy), f'the "firebase" key in {name}'
    if len(names) == 1:
        return next(iter(names.values())), "the only project in .firebaserc"
    return names.get("default"), "the default in .firebaserc"


def fresh(tokens) -> bool:
    """True when the access token is valid for FRESH seconds more (expires_at is in milliseconds)."""
    if not isinstance(tokens, dict) or not isinstance(tokens.get("access_token"), str):
        return False
    expires = tokens.get("expires_at")
    return isinstance(expires, (int, float)) and expires > (time.time() + FRESH) * 1000


def _short(arg: str) -> tuple[list[tuple[str, str | None]], bool]:
    """A group of short options as commander reads it (-jP → -j -P; -Pfoo → -P with the value foo): the OPTIONS
    among them, and whether the last one takes the next argument as its value."""
    rest = arg[1:]
    while rest:
        letter, rest = rest[0], rest[1:]
        if f"-{letter}" in OPTIONS:  # takes a value: the rest of the group, else the next argument
            return [(OPTIONS[f"-{letter}"], rest or None)], not rest
    return [], False


def options(args: list[str]) -> list[tuple[str, str | None]]:
    """(long name, value) of each --project/-P, --account, --token and --config/-c, read like the CLI's parser
    (commander): `--name value`, `--name=value`, `-P value`, `-Pvalue`, `-jP value`, anywhere before `--`."""
    found, i = [], 0
    while i < len(args):
        arg = args[i]
        if arg == "--":
            break
        nxt = args[i + 1] if i + 1 < len(args) else None
        if arg.startswith("--"):
            name, eq, value = arg.partition("=")
            if name in OPTIONS:
                if not eq:
                    value, i = nxt, i + 1
                found.append((OPTIONS[name], value))
        elif arg.startswith("-") and len(arg) > 1:
            short, takes = _short(arg)
            if takes:
                short, i = [(short[0][0], nxt)], i + 1
            found += short
        i += 1
    return found


def words(args: list[str]) -> list[str]:
    """The command and its arguments, without options and the values of VALUED options; everything after `--` is an
    argument. An option Keyfit does not know may take the next word as its value, so the words stop there: an unclear
    command line is never taken for a sign-in command, which would skip every check."""
    out, i = [], 0
    while i < len(args):
        arg = args[i]
        if arg == "--":
            out += args[i + 1:]
            break
        if arg.startswith("-") and len(arg) > 1:
            short, takes = _short(arg) if not arg.startswith("--") else ([], False)
            if arg in VALUED or takes:
                i += 2
                continue
            known = short or "=" in arg or arg in FLAGS or \
                (not arg.startswith("--") and all(f"-{c}" in FLAGS for c in arg[1:]))
            if not known and i + 1 < len(args) and not args[i + 1].startswith("-"):
                break
            i += 1
            continue
        out.append(arg)
        i += 1
    return out


def firebase_bin(env: dict, start) -> str | None:
    """The firebase CLI `npx firebase-tools` runs in *start*: the nearest node_modules/.bin/firebase (up to the home
    folder), else the one on PATH."""
    home = _home(env)
    folder = Path(os.path.abspath(start))
    for d in (folder, *folder.parents):
        candidate = d / "node_modules" / ".bin" / "firebase"
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
        if d == home:
            break
    return find_real("firebase", env, skip_wrappers=True)


def sign_in(store: dict, email: str) -> str:
    """Renew a sign-in the CLI already holds: `firebase login --reauth` replaces the default login, so it fits only when
    that is the account; `firebase login:add` (pick the account in the browser) renews any other."""
    return "firebase login --reauth" if same(default_login(store), email) else "firebase login:add"


def _run(argv: list[str], cwd: str, env: dict, timeout: float) -> tuple[int, str, str]:
    out = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout,
                         stdin=subprocess.DEVNULL, check=False)
    return out.returncode, out.stdout, out.stderr


class FirebaseAdapter(Adapter):
    kind = "firebase"

    def __init__(self, run=_run, fetch=_fetch, timeout: float = 15, cli_timeout: float = 30):
        self.run = run
        self.fetch = fetch
        self.timeout = timeout
        self.cli_timeout = cli_timeout

    def label(self, conn) -> str:
        return str(conn.get("project") or "?")

    def inject(self, conn, env: dict) -> None:
        for var in [v for v in env if scrubbed(v)]:
            env.pop(var)

    def fingerprint(self, conn, env: dict) -> str:
        """Changes with a new sign-in of the account (Google's ID token of it: subject and time), so a cached check
        never vouches for another sign-in. A renewed access token keeps it."""
        account = account_of(read_store(env) or {}, str(conn.get("account")))
        user = account["user"] if account else {}
        return hashlib.sha256(f"{user.get('sub')}:{user.get('iat')}".encode()).hexdigest()[:16]

    def local(self, conn, env: dict) -> Check:
        return self.at(conn, env, os.getcwd())

    def at(self, conn, env: dict, folder, config: str | None = None) -> Check:
        """The local check for the firebase CLI started in *folder* (with --config *config*)."""
        label = self.label(conn)
        email, expected = conn.get("account"), conn.get("project")

        def fail(detail: str, fix: str) -> Check:
            return Check(self.kind, "fail", label, detail, fix)

        if not isinstance(email, str) or not EMAIL.fullmatch(email):
            return fail("the project has no Google account", "keyfit add google --email <the project's Google account>")
        if not isinstance(expected, str) or not PROJECT.fullmatch(expected):
            return fail("the expected project must be a Firebase project ID", "keyfit add firebase --project-id <ID>")
        store = read_store(env)
        if store is None:
            return fail(f"{tilde(store_path(env))} is not JSON (the firebase CLI empties it at its next start)",
                        "firebase login")
        signed = emails(store)
        if not signed:
            return fail("the firebase CLI is not signed in", "firebase login")
        if not any(same(e, email) for e in signed):
            return fail(f"the firebase CLI is not signed in as {email} (it has {printable(', '.join(signed))})",
                        f"firebase login:add {email}")
        root = project_root(folder, config)
        used, binding = bound(store, root)
        here = f" --config {shlex.quote(config)}" if config else ""
        if root is None:  # commands that need no firebase.json (firestore:delete, auth:export) still pick a project
            project, how = selected(store, None, folder)
            if project is not None and project != expected:
                rc = Path(os.path.abspath(folder)) / ".firebaserc"
                return fail(f"no firebase.json here, and the firebase CLI works on {printable(project)} here ({how}), "
                            f"the project expects {expected}",
                            "run firebase in the project's Firebase folder (where firebase.json is)"
                            if how.startswith("picked") else f"make {expected} the default in {tilde(rc)}")
        if not same(used, email):
            if root is None:
                return Check(self.kind, "warn", label, f"no firebase.json here, so the firebase CLI uses its default "
                             f"login {printable(used)}; Keyfit's firebase adds --account {email}", pending=True)
            what = f"is bound to {printable(used)}" if binding else f"uses the default login {printable(used)}"
            return fail(f"the firebase CLI's folder {tilde(root)} {what}, the project's Google account is {email}",
                        f"firebase login:use {email}{here}")
        if root is None:
            return Check(self.kind, "ok", label, f"{email} (default login, no firebase.json here)", pending=True)
        project, how = selected(store, root, folder, config)
        if project is None:
            return fail(f"no Firebase project selected in {tilde(root)} (.firebaserc has no default)",
                        f"firebase use {expected}{here}")
        if project != expected:
            return fail(f"the firebase CLI works on {printable(project)} in {tilde(root)} ({how}), the project "
                        f"expects {expected}", f"firebase use {expected}{here}")
        return Check(self.kind, "ok", label, f"{email}, " + (f"bound at {tilde(root)}" if binding else "default login"),
                     pending=True)

    def remote(self, conn, env: dict) -> Check:
        label = self.label(conn)
        email, expected = conn["account"], conn["project"]
        store = read_store(env) or {}
        token, reason, state = self.access_token(email, env)
        if token is None:
            if state == "missing":
                return Check(self.kind, "warn", label, reason)
            fix = {"transient": "check the network and retry", "signin": sign_in(store, email)}.get(
                state, f"KEYFIT_DISABLE=1 firebase projects:list --account {email}   (shows the full error)")
            return Check(self.kind, "fail", label, reason, fix, transient=state == "transient")
        try:
            status, body = self.fetch(TOKENINFO, token, self.timeout)
        except (OSError, ValueError) as err:
            return self._unreachable(label, err)
        who = body.get("email") if isinstance(body, dict) else None
        if status in (400, 401):
            return Check(self.kind, "fail", label, f"Google does not accept the firebase CLI's sign-in for {email}",
                         sign_in(store, email))
        if status != 200 or not isinstance(who, str):
            return Check(self.kind, "fail", label, f"Google's tokeninfo answered {status}",
                         "check the network and retry", transient=status >= 500 or status == 429)
        if not same(who, email):
            return Check(self.kind, "fail", label,
                         f"the firebase CLI's sign-in for {email} belongs to {printable(who)}", sign_in(store, email))
        from urllib.parse import quote

        try:
            status, body = self.fetch(PROJECTS + quote(expected, safe=":"), token, self.timeout)
        except (OSError, ValueError) as err:
            return self._unreachable(label, err)
        if status == 401:
            return Check(self.kind, "fail", label, f"Google does not accept the firebase CLI's sign-in for {email}",
                         sign_in(store, email))
        if status in (403, 404):
            return Check(self.kind, "fail", label, f"{email} cannot reach Firebase project {expected} (it does not "
                                                   f"exist, has no Firebase, or the account has no access)",
                         f"keyfit add firebase --project-id <a project {email} can reach>")
        if status != 200 or not isinstance(body, dict):
            return Check(self.kind, "fail", label, f"the Firebase Management API answered {status}",
                         "check the network and retry", transient=status >= 500 or status == 429)
        if body.get("state") not in (None, "ACTIVE"):
            return Check(self.kind, "fail", label,
                         f"Firebase project {expected} is not active ({printable(body.get('state'))})",
                         "keyfit add firebase --project-id …")
        return Check(self.kind, "ok", label, "account and project confirmed")

    def _unreachable(self, label: str, err: Exception) -> Check:
        why = getattr(err, "reason", None) or type(err).__name__
        return Check(self.kind, "fail", label, f"Google did not answer ({why})", "check the network and retry",
                     transient=True)

    def access_token(self, email: str, env: dict) -> tuple[str | None, str, str]:
        """(token, "", "ok"): the access token the CLI keeps for *email*, renewed by the CLI first when it is due.
        Else (None, why, state): "signin" (signed out, expired or revoked), "transient" (no answer), "missing" (no
        firebase CLI to renew it with) or "error"."""
        account = account_of(read_store(env) or {}, email)
        tokens = account.get("tokens") if account else None
        if not isinstance(tokens, dict):
            return None, f"the firebase CLI is not signed in as {email}", "signin"
        if fresh(tokens):
            return tokens["access_token"], "", "ok"
        if not isinstance(tokens.get("refresh_token"), str):
            return None, f"the firebase CLI's sign-in for {email} expired and cannot be renewed", "signin"
        binary = firebase_bin(env, os.getcwd())
        if binary is None:
            return None, "not checked online: no firebase CLI here to renew its sign-in", "missing"
        run_env = {k: v for k, v in env.items() if not scrubbed(k)}
        run_env.update(NO_UPDATE_NOTIFIER="1", NO_COLOR="1")
        with tempfile.TemporaryDirectory() as away:  # no firebase.json: --account picks the sign-in; a log stays there
            try:
                code, out, err = self.run([binary, "--account", account["user"]["email"], "projects:list", "--json",
                                           "--non-interactive"], away, run_env, self.cli_timeout)
            except subprocess.TimeoutExpired:
                return None, f"the firebase CLI did not answer within {self.cli_timeout:g} s", "transient"
            except OSError:
                return None, f"{tilde(binary)} could not run", "error"
        account = account_of(read_store(env) or {}, email)
        tokens = account.get("tokens") if account else None
        if isinstance(tokens, dict) and fresh(tokens):
            return tokens["access_token"], "", "ok"
        if '"status"' not in out:  # not the CLI's JSON answer: it did not get as far as Google
            lines = [line.strip() for line in f"{err}\n{out}".splitlines() if line.strip()]
            first = lines[0] if lines else f"exit {code}"
            return None, f"firebase projects:list failed: {printable(first)[:160]}", "error"
        try:  # the CLI reports a network failure as expired credentials: ask Google whether it answers at all
            self.fetch(TOKENINFO, str(tokens.get("access_token") if isinstance(tokens, dict) else "") or "-",
                       self.timeout)
        except (OSError, ValueError):
            return None, "Google did not answer (network?)", "transient"
        return None, f"the firebase CLI could not renew its sign-in for {email} (expired or revoked): sign in again " \
                     f"as {email}", "signin"
