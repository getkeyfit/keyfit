"""Google Cloud: which gcloud configuration, Google account and Google Cloud project a project works with.

Account:  [accounts.gcloud.acme]
          configuration = "acme"   (a gcloud configuration: gcloud config configurations create acme)
          project = "acme-prod"    (the Google Cloud project ID it works on)
          config_dir = "~/…"       (optional: gcloud's folder, when CLOUDSDK_CONFIG moved it at `keyfit add gcloud`)
Project:  gcloud = "acme"   and   google = "work"   (the project's Google account: its email reaches this connection
          as `account`, see registry.GOOGLE_TOOLS)

gcloud (verified with 580.0.0) keeps named configurations in its folder (~/.config/gcloud, or CLOUDSDK_CONFIG):
configurations/config_NAME, an INI file whose [core] holds the account and the project. CLOUDSDK_ACTIVE_CONFIG_NAME
picks one for a single process without touching the one active everywhere else (active_config); a name without a
file gives empty settings, silently. CLOUDSDK_CORE_ACCOUNT and CLOUDSDK_CORE_PROJECT override single settings, and
--configuration, --account and --project beat all of them. Sign-ins live in credentials.db, one row per account
(`gcloud auth list`). Keyfit reads the configuration files and the account names of the sign-ins, never a credential,
and keeps no Google token: gcloud switches, Keyfit checks and enforces.

inject  — remove inherited CLOUDSDK_* variables that could pick another configuration, account, project, credentials
          or endpoint (those that only say how gcloud runs — its Python, a proxy, prompts — stay) and
          GOOGLE_APPLICATION_CREDENTIALS (a credentials file the client libraries take before any login); then set
          CLOUDSDK_ACTIVE_CONFIG_NAME, CLOUDSDK_CORE_ACCOUNT and CLOUDSDK_CORE_PROJECT, so every gcloud the session
          starts (bq and gsutil too) works as the project's account on its project.
forget  — in a project without a Google Cloud account, the variables Keyfit set for another project go.
local   — the configuration exists, its account is the project's Google account and its project the expected one, it
          takes no credentials from a file, and gcloud holds a sign-in for the account.
remote  — `gcloud auth print-access-token` with that configuration (gcloud renews the token first, as every gcloud
          command does): Google's tokeninfo must name the account, and the Cloud Resource Manager must show the
          project to it. The token is held in memory for those two requests only.
"""

from __future__ import annotations

import configparser
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

from ..checks import Check, printable
from ..realbin import find_real
from ..registry import tilde
from .base import Adapter
from .google import EMAIL, same

TOKENINFO = "https://oauth2.googleapis.com/tokeninfo"
PROJECTS = "https://cloudresourcemanager.googleapis.com/v1/projects/"
CONFIGURATION = re.compile(r"[a-z][-a-z0-9]*")  # gcloud's rule for configuration names (use fullmatch)
# A project ID (6 to 30 characters), also in the older domain-scoped form (example.com:project).
PROJECT = re.compile(r"(?:[a-z0-9][-a-z0-9.]*[a-z0-9]:)?[a-z][-a-z0-9]{4,28}[a-z0-9]")
ACTIVE, ACCOUNT, PROJECT_VAR, FOLDER = ("CLOUDSDK_ACTIVE_CONFIG_NAME", "CLOUDSDK_CORE_ACCOUNT", "CLOUDSDK_CORE_PROJECT",
                                        "CLOUDSDK_CONFIG")
ADC = "GOOGLE_APPLICATION_CREDENTIALS"
MARK = "KEYFIT_GCLOUD"  # set next to the variables Keyfit injected: the account's name
# CLOUDSDK_* variables that only say how gcloud runs, never as whom or on what (prefixes): they stay.
KEEP = ("CLOUDSDK_PYTHON", "CLOUDSDK_GSUTIL_PYTHON", "CLOUDSDK_BQ_PYTHON", "CLOUDSDK_PROXY_",
        "CLOUDSDK_COMPONENT_MANAGER_", "CLOUDSDK_CORE_DISABLE_PROMPTS", "CLOUDSDK_CORE_DISABLE_USAGE_REPORTING",
        "CLOUDSDK_CORE_DISABLE_COLOR", "CLOUDSDK_CORE_DISABLE_FILE_LOGGING", "CLOUDSDK_CORE_VERBOSITY",
        "CLOUDSDK_CORE_CUSTOM_CA_CERTS_FILE", "CLOUDSDK_CORE_USER_OUTPUT_ENABLED", "CLOUDSDK_CORE_MAX_LOG_DAYS")
# Settings in a configuration's [auth] that take credentials from somewhere else than the account's sign-in.
ELSEWHERE = ("access_token_file", "credential_file_override", "disable_credentials")
# gcloud's options that take a value (`--name value` or `--name=value`; gcloud takes no abbreviations): the global
# ones, those of list commands, and those of the commands Keyfit looks at.
VALUED = {"--account", "--billing-project", "--configuration", "--flags-file", "--flatten", "--format", "--project",
          "--verbosity", "--impersonate-service-account", "--access-token-file", "--trace-token", "--filter",
          "--limit", "--page-size", "--sort-by", "--cred-file", "--scopes", "--client-id-file", "--login-config"}
FLAGS = {"-q", "--quiet", "-h", "--help", "--log-http", "--no-log-http", "--user-output-enabled",
         "--no-user-output-enabled", "--activate", "--no-activate", "--brief", "--launch-browser",
         "--no-launch-browser", "--browser", "--no-browser", "--update-adc", "--no-update-adc",
         "--enable-gdrive-access", "--force", "--all"}
OPTIONS = ("--account", "--configuration", "--project", "--access-token-file", "--flags-file")
UNREACHABLE = ("unable to find the server", "failed to establish a new connection", "max retries exceeded",
               "name resolution", "nodename nor servname", "timed out", "connection aborted", "connection reset",
               "network is unreachable", "transporterror", "httpsconnectionpool")
SIGNED_OUT = ("does not have any valid credentials", "reauthentication failed", "invalid_grant",
              "problem refreshing your current auth tokens", "do not currently have an active account",
              "gcloud auth login")


def _home(env: dict) -> Path:
    return Path(env.get("HOME") or Path.home())


def folder(env: dict) -> Path:
    """gcloud's folder: CLOUDSDK_CONFIG, else ~/.config/gcloud."""
    return Path(env[FOLDER]).expanduser() if env.get(FOLDER) else _home(env) / ".config" / "gcloud"


def config_file(env: dict, name: str) -> Path:
    return folder(env) / "configurations" / f"config_{name}"


def configurations(env: dict) -> list[str]:
    """The configurations gcloud has (`gcloud config configurations list`), default first."""
    try:
        files = os.listdir(folder(env) / "configurations")
    except OSError:
        return []
    names = [f[len("config_"):] for f in files if f.startswith("config_")]
    return sorted((n for n in names if CONFIGURATION.fullmatch(n)), key=lambda n: (n != "default", n))


def active(env: dict) -> str:
    """The configuration a plain gcloud uses: CLOUDSDK_ACTIVE_CONFIG_NAME, else the one activated (active_config)."""
    if env.get(ACTIVE):
        return env[ACTIVE]
    try:
        return (folder(env) / "active_config").read_text().strip() or "default"
    except OSError:
        return "default"


def read(env: dict, name: str) -> dict | None:
    """A configuration's settings: {"account", "project", "auth": {…}}, or {"unreadable": True}; None when gcloud has
    no such configuration."""
    path = config_file(env, name)
    if not path.is_file():
        return None
    cfg = configparser.RawConfigParser()
    try:
        cfg.read(path)
    except (configparser.Error, UnicodeDecodeError):
        return {"unreadable": True}

    def get(section: str, key: str) -> str | None:
        value = cfg.get(section, key).strip() if cfg.has_option(section, key) else ""
        return value or None

    auth = {key: cfg.get("auth", key).strip() for key in cfg.options("auth")} if cfg.has_section("auth") else {}
    return {"account": get("core", "account"), "project": get("core", "project"), "auth": auth}


def signed_in(env: dict) -> list[str] | None:
    """The accounts gcloud holds a sign-in for (`gcloud auth list`), from credentials.db: the account column only,
    never a credential. None when the file cannot be read."""
    import contextlib
    import sqlite3

    path = folder(env) / "credentials.db"
    if not path.is_file():
        return []
    try:
        with contextlib.closing(sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=1)) as db:
            return [row[0] for row in db.execute("SELECT account_id FROM credentials") if isinstance(row[0], str)]
    except sqlite3.Error:
        return None


def scrub(env: dict) -> None:
    for var in [v for v in env if v == ADC or (v.startswith("CLOUDSDK_") and not v.startswith(KEEP))]:
        env.pop(var)


def options(args: list[str]) -> list[tuple[str, str | None]]:
    """(name, value) of each OPTIONS on the command line, `--name value` or `--name=value`, anywhere before `--`."""
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
    """The command words and arguments, without options and the values of VALUED options. An option Keyfit does not
    know may take the next word as its value, so the words stop there: an unclear command line is never taken for a
    sign-in command, which would skip every check."""
    out, i = [], 0
    while i < len(args):
        arg = args[i]
        if arg == "--":
            break
        if arg.startswith("-") and len(arg) > 1:
            if arg in VALUED:
                i += 2
                continue
            if "=" not in arg and arg not in FLAGS and i + 1 < len(args) and not args[i + 1].startswith("-"):
                break
            i += 1
            continue
        out.append(arg)
        i += 1
    return out


def sign_in(account: str) -> str:
    return f"gcloud auth login {account}"


def _run(argv: list[str], env: dict, timeout: float) -> tuple[int, str, str]:
    out = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL,
                         check=False)
    return out.returncode, out.stdout, out.stderr


def _fetch(url: str, token: str, timeout: float) -> tuple[int, object]:
    import urllib.error  # only when a check goes online: urllib.request and TLS take ~30 ms to import
    import urllib.parse
    import urllib.request

    from .. import http

    headers = {"User-Agent": "keyfit", "Accept": "application/json"}
    if url == TOKENINFO:  # the token goes in the body, never into a URL a server may log
        req = urllib.request.Request(url, data=urllib.parse.urlencode({"access_token": token}).encode(),
                                     headers={**headers, "Content-Type": "application/x-www-form-urlencoded"},
                                     method="POST")
    else:
        req = urllib.request.Request(url, headers={**headers, "Authorization": f"Bearer {token}"})
    try:
        with http.urlopen(req, timeout) as resp:
            return resp.status, json.loads(resp.read() or b"null")
    except urllib.error.HTTPError as err:
        return err.code, None


class GcloudAdapter(Adapter):
    kind = "gcloud"

    def __init__(self, run=_run, fetch=_fetch, timeout: float = 15, cli_timeout: float = 30):
        self.run = run
        self.fetch = fetch
        self.timeout = timeout
        self.cli_timeout = cli_timeout

    def label(self, conn) -> str:
        return f"{conn.get('configuration') or '?'} → {conn.get('project') or '?'}"

    def inject(self, conn, env: dict) -> None:
        scrub(env)
        account = conn.get("account")
        for var, value, valid in ((ACTIVE, conn.get("configuration"), CONFIGURATION),
                                  (ACCOUNT, account.lower() if isinstance(account, str) else None, EMAIL),
                                  (PROJECT_VAR, conn.get("project"), PROJECT)):  # gcloud keeps accounts as Google
            if isinstance(value, str) and valid.fullmatch(value):  # names them: in lower case
                env[var] = value
        if conn.get("config_dir"):
            env[FOLDER] = str(Path(conn["config_dir"]).expanduser())
        env[MARK] = conn.get("name", "")

    def forget(self, env: dict) -> None:
        if MARK in env:  # Keyfit set them for another project
            for var in (ACTIVE, ACCOUNT, PROJECT_VAR, FOLDER, MARK):
                env.pop(var, None)

    def fingerprint(self, conn, env: dict) -> str:
        """Changes with gcloud's sign-ins (a new `gcloud auth login`) and the configuration's file, so a cached check
        never vouches for another sign-in or setting."""
        seen = []
        for path in (folder(env) / "credentials.db", config_file(env, str(conn.get("configuration")))):
            try:
                st = path.stat()
            except OSError:
                continue
            seen.append(f"{path.name}:{st.st_mtime_ns}:{st.st_size}")
        return hashlib.sha256("|".join(seen).encode()).hexdigest()[:16]

    def local(self, conn, env: dict) -> Check:
        label = self.label(conn)
        name, account, project = conn.get("configuration"), conn.get("account"), conn.get("project")

        def fail(detail: str, fix: str) -> Check:
            return Check(self.kind, "fail", label, detail, fix)

        if not isinstance(name, str) or not CONFIGURATION.fullmatch(name):
            return fail("no gcloud configuration recorded", "keyfit add gcloud --configuration <name>")
        if not isinstance(account, str) or not EMAIL.fullmatch(account):
            return fail("the project has no Google account", "keyfit add google --email <the project's Google account>")
        if not isinstance(project, str) or not PROJECT.fullmatch(project):
            return fail("the expected project must be a Google Cloud project ID",
                        f"keyfit add gcloud --configuration {name} --project-id <project ID>")
        settings = read(env, name)
        if settings is None:
            return fail(f"gcloud has no configuration {name} in {tilde(folder(env))}",
                        f"gcloud config configurations create {name} --no-activate")
        if settings.get("unreadable"):
            return fail(f"{tilde(config_file(env, name))} does not parse",
                        f"gcloud config configurations describe {name}   (names the problem)")
        for key in ELSEWHERE:
            if settings["auth"].get(key):
                return fail(f"configuration {name} sets auth/{key}, so gcloud would not use {account}'s sign-in",
                            f"gcloud config unset auth/{key} --configuration {name}")
        accounts = signed_in(env)
        held = accounts is None or any(same(a, account) for a in accounts)  # unreadable: the online check decides
        if not same(settings["account"], account):
            fix = f"gcloud config set account {account} --configuration {name}" if held else sign_in(account)
            if settings["account"]:
                return fail(f"configuration {name} uses {printable(settings['account'])}, the project's Google account "
                            f"is {account}", fix)
            return fail(f"configuration {name} has no account", fix)
        if not held:
            return fail(f"gcloud is not signed in as {account}", sign_in(account))
        if settings["project"] != project:
            what = f"works on project {printable(settings['project'])}" if settings["project"] else "has no project"
            return fail(f"configuration {name} {what}, the project expects {project}",
                        f"gcloud config set project {project} --configuration {name}")
        acting = settings["auth"].get("impersonate_service_account")
        return Check(self.kind, "ok", label, account + (f" as {printable(acting)}" if acting else ""), pending=True)

    def remote(self, conn, env: dict) -> Check:
        label = self.label(conn)
        name, account, project = conn["configuration"], conn["account"], conn["project"]
        binary = find_real("gcloud", env, skip_wrappers=True)
        if binary is None:
            return Check(self.kind, "warn", label, "not checked online: gcloud not found on PATH")
        token, reason, state = self.access_token(binary, env)
        if token is None:
            if state == "transient":
                return Check(self.kind, "fail", label, reason, "check the network and retry", transient=True)
            if state == "signin":
                return Check(self.kind, "fail", label, reason, sign_in(account))
            return Check(self.kind, "fail", label, reason,
                         f"KEYFIT_DISABLE=1 gcloud projects describe {project} --configuration {name}   "
                         f"(shows the full error)")
        settings = read(env, name) or {}
        expected = settings.get("auth", {}).get("impersonate_service_account") or account
        who, reason, state = self.whose(token)
        if who is None:
            return Check(self.kind, "fail", label, reason,
                         "check the network and retry" if state == "transient" else sign_in(account),
                         transient=state == "transient")
        if not same(who, expected):
            return Check(self.kind, "fail", label, f"gcloud's token for configuration {name} belongs to "
                                                   f"{printable(who)}, the project's Google account is {expected}",
                         sign_in(account))
        reason, state = self.reaches(token, project, who)
        if reason:
            fix = {"transient": "check the network and retry", "signin": sign_in(account)}.get(
                state, f"keyfit add gcloud --project-id <a project {who} can reach>")
            return Check(self.kind, "fail", label, reason, fix, transient=state == "transient")
        return Check(self.kind, "ok", label, "account and project confirmed")

    # helpers, shared with `keyfit add gcloud` ----------------------------------------------------------------------

    def access_token(self, binary: str, env: dict) -> tuple[str | None, str, str]:
        """(token, "", "ok") of the configuration the environment names, or (None, why, state) with state "signin"
        (signed out, or the sign-in expired), "transient" (no answer) or "error". The token is never stored or shown."""
        run_env = dict(env, CLOUDSDK_CORE_DISABLE_PROMPTS="1", CLOUDSDK_COMPONENT_MANAGER_DISABLE_UPDATE_CHECK="true")
        try:
            code, out, err = self.run([binary, "auth", "print-access-token"], run_env, self.cli_timeout)
        except subprocess.TimeoutExpired:
            return None, f"gcloud did not answer within {self.cli_timeout:g} s", "transient"
        except OSError:
            return None, f"{tilde(binary)} could not run", "error"
        token = out.strip()
        if code == 0 and token and not any(c.isspace() for c in token):
            return token, "", "ok"
        low = err.lower()
        account = env.get(ACCOUNT, "the account")
        if any(word in low for word in UNREACHABLE):
            return None, "Google did not answer (network?)", "transient"
        if any(word in low for word in SIGNED_OUT):
            return None, f"gcloud has no valid sign-in for {account} (signed out, expired or revoked)", "signin"
        lines = [line.strip() for line in err.splitlines() if line.strip()]
        first = next((line for line in lines if line.startswith("ERROR")), lines[0] if lines else f"exit {code}")
        return None, f"gcloud auth print-access-token failed: {printable(first)[:160]}", "error"

    def whose(self, token: str) -> tuple[str | None, str, str]:
        """(email, "", "ok") the token belongs to, from Google's tokeninfo, or (None, why, state)."""
        try:
            status, body = self.fetch(TOKENINFO, token, self.timeout)
        except (OSError, ValueError) as err:
            return None, f"Google did not answer ({getattr(err, 'reason', None) or type(err).__name__})", "transient"
        if status == 400 or status == 401:
            return None, "Google does not accept the token gcloud printed", "signin"
        email = body.get("email") if isinstance(body, dict) else None
        if status != 200 or not isinstance(email, str):
            return None, f"Google's tokeninfo answered {status}", "transient" if status >= 500 or status == 429 else "error"
        return email, "", "ok"

    def reaches(self, token: str, project: str, who: str) -> tuple[str, str]:
        """("", "ok") when the token's account sees the project (GET projects/ID), else (why, state)."""
        from urllib.parse import quote

        try:
            status, body = self.fetch(PROJECTS + quote(project, safe=":"), token, self.timeout)
        except (OSError, ValueError) as err:
            return f"Google did not answer ({getattr(err, 'reason', None) or type(err).__name__})", "transient"
        if status == 401:
            return "Google does not accept the token gcloud printed", "signin"
        if status in (403, 404):
            return f"{who} cannot reach project {project} (it does not exist, or the account has no access)", "error"
        if status != 200 or not isinstance(body, dict):
            return f"Cloud Resource Manager answered {status}", "transient" if status >= 500 or status == 429 else "error"
        state = body.get("lifecycleState")
        if state not in (None, "ACTIVE"):
            return f"project {project} is not active ({printable(state)})", "error"
        return "", "ok"
