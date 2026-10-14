"""GitHub: which GitHub account a project acts as (gh, and git over https via gh's credential helper).

Account:  [accounts.github.octocat]  login = "octocat"
          optional: email = "…" (expected git author email), ssh_host = "github.com-work"
Project:  github = "octocat"

inject  — remove inherited GitHub tokens and gh overrides, then read the account's
          own token with `gh auth token --user <login>` (read-only; gh keeps one
          Keychain entry per account) and expose it as GH_TOKEN. Inside a session
          Keyfit started, the session's GH_TOKEN is kept instead when this project's
          check confirmed exactly that token in the last 30 minutes and gh's sign-ins
          (hosts.yml) have not changed since; asking gh again takes ~80 ms per launch.
          Never point gh at a synthetic GH_CONFIG_DIR: gh rewrites its per-account
          Keychain entries from the shared active slot when it does (2026-10-08 incident).
local   — a token exists for the login; the repo's author email and ssh host alias
          match the account (warnings — commits would carry the wrong identity).
remote  — GET https://api.github.com/user with that token must return the login,
          which also catches a stored token that belongs to a different account.
fingerprint — the token's hash and when gh last changed its sign-ins (hosts.yml, by its
          time only): a new token or any `gh auth login/logout/switch` is checked again.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess

from .. import cache
from ..checks import Check
from ..realbin import find_real
from .base import Adapter

SCRUB = ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN", "GH_HOST", "GH_CONFIG_DIR")
API = "https://api.github.com/user"
SSH_REMOTE = re.compile(r"^(?:ssh://)?git@([^:/]+)[:/]")


def read_token(login: str, env: dict, timeout: float = 15) -> tuple[str | None, str]:
    """Ask the real gh for this account's stored token (never prints it)."""
    gh = find_real("gh", env, skip_wrappers=True)
    if gh is None:
        return None, "gh not found on PATH"
    clean = {k: v for k, v in env.items() if k not in SCRUB}
    try:
        out = subprocess.run([gh, "auth", "token", "--hostname", "github.com", "--user", login], env=clean,
                             capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL, check=False)
    except (OSError, subprocess.SubprocessError):
        return None, "gh auth token did not answer"
    token = out.stdout.strip()
    if out.returncode != 0 or not token:
        return None, f"gh has no stored login for {login}"
    return token, ""


def logins(env: dict, timeout: float = 30) -> tuple[list[str], str]:
    """The github.com accounts gh is signed in to, from `gh auth status` (read-only; tokens stay masked)."""
    gh = find_real("gh", env, skip_wrappers=True)
    if gh is None:
        return [], "gh not found on PATH"
    clean = {k: v for k, v in env.items() if k not in SCRUB}
    try:
        out = subprocess.run([gh, "auth", "status", "--hostname", "github.com"], env=clean, capture_output=True,
                             text=True, timeout=timeout, stdin=subprocess.DEVNULL, check=False)
    except (OSError, subprocess.SubprocessError):
        return [], "gh auth status did not answer"
    # "✓ Logged in to github.com account octocat (keyring)"; gh before 2.40 says "… as octocat"
    found = re.findall(r"Logged in to github\.com (?:account|as) ([A-Za-z0-9-]+)", out.stdout + out.stderr)
    return list(dict.fromkeys(found)), ""


def _fetch(token: str, timeout: float) -> tuple[int, dict]:
    import urllib.error  # only for online checks: urllib.request and TLS take ~30 ms to import
    import urllib.request

    from .. import http

    req = urllib.request.Request(API, headers={"Authorization": f"Bearer {token}", "User-Agent": "keyfit",
                                               "Accept": "application/vnd.github+json"})
    try:
        with http.urlopen(req, timeout) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as err:
        return err.code, {}


def git_identity(cwd: str) -> tuple[str, str | None]:
    """(author email, ssh host of origin) for the repository at cwd; both questions go to git at the same time."""
    asks, answers = [], []
    try:
        for args in (("config", "user.email"), ("remote", "get-url", "origin")):
            asks.append(subprocess.Popen(["git", "-C", cwd, *args], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                         text=True))
        for ask in asks:
            out, _ = ask.communicate(timeout=10)
            answers.append(out.strip() if ask.returncode == 0 else "")
    finally:
        for ask in asks:
            if ask.poll() is None:
                ask.kill()
                ask.wait()
    email, origin = answers
    m = SSH_REMOTE.match(origin)
    return email, (m.group(1) if m else None)


def hosts_file(env: dict) -> str:
    """gh's list of sign-ins, where gh reads it once GH_CONFIG_DIR is scrubbed. Keyfit only looks at its time."""
    if env.get("XDG_CONFIG_HOME"):
        return os.path.join(env["XDG_CONFIG_HOME"], "gh", "hosts.yml")
    return os.path.join(env.get("HOME") or os.path.expanduser("~"), ".config", "gh", "hosts.yml")


class GithubAdapter(Adapter):
    kind = "github"
    token_var = "GH_TOKEN"  # where inject puts the account's token (Copilot CLI's adapter uses its own variable)

    def __init__(self, fetch=_fetch, timeout: float = 15):
        self.fetch = fetch
        self.timeout = timeout

    def label(self, conn) -> str:
        return conn.get("login", "?")

    def login_fix(self, conn) -> str:
        return (f"gh auth login --hostname github.com --git-protocol ssh --web   (sign in as {conn.get('login')}), "
                f"then switch back: gh auth switch --user <your usual account>")

    def inject(self, conn, env: dict) -> None:
        inherited = env.get("GH_TOKEN")
        for var in SCRUB:
            env.pop(var, None)
        if inherited and self.confirmed(conn, env, inherited):
            token = inherited
        else:
            token, _ = read_token(conn.get("login", ""), env)
        if token:
            env["GH_TOKEN"] = token
        env["KEYFIT_GITHUB_LOGIN"] = conn.get("login", "")

    def confirmed(self, conn, env: dict, token: str) -> bool:
        """True when the project's GitHub check (KEYFIT_PROJECT, set by environment.build) confirmed exactly this
        token within the cache's 30 minutes and gh's sign-ins have not changed since: both are in the fingerprint."""
        project = env.get("KEYFIT_PROJECT")
        if not project:
            return False
        hit = cache.get(cache.key(project, self.kind, conn, self._fingerprint(token, env)))
        return hit is not None and hit.get("status") == "ok"  # a warning never vouches for a token

    def fingerprint(self, conn, env: dict) -> str:
        return self._fingerprint(env.get(self.token_var, ""), env)

    @staticmethod
    def _fingerprint(token: str, env: dict) -> str:
        try:
            changed = str(os.stat(hosts_file(env)).st_mtime_ns)
        except OSError:
            changed = ""
        return f"{hashlib.sha256(token.encode()).hexdigest()[:16]}:{changed}"

    def local(self, conn, env: dict) -> Check:
        label = self.label(conn)
        if not conn.get("login"):
            return Check(self.kind, "fail", label, "no GitHub login recorded", "keyfit add github --login <login>")
        if not env.get("GH_TOKEN"):
            return Check(self.kind, "fail", label, f"gh has no stored login for {conn['login']}", self.login_fix(conn))
        notes, fixes = [], []
        email, host = git_identity(os.getcwd())
        if conn.get("email") and email and email != conn["email"]:
            notes.append(f"commits here are authored as {email}, account expects {conn['email']}")
            fixes.append(f"git config user.email {conn['email']}")
        if conn.get("ssh_host") and host and host != conn["ssh_host"]:
            notes.append(f"origin uses ssh host {host}, account expects {conn['ssh_host']}")
        if notes:
            return Check(self.kind, "warn", label, "; ".join(notes), "; ".join(fixes), pending=True)
        return Check(self.kind, "ok", label, "", pending=True)

    def remote(self, conn, env: dict) -> Check:
        label = self.label(conn)
        try:
            status, body = self.fetch(env[self.token_var], self.timeout)
        except (OSError, ValueError) as err:
            return Check(self.kind, "fail", label, f"GitHub did not answer ({getattr(err, 'reason', None) or type(err).__name__})",
                         "check the network and retry", transient=True)
        if status == 401:
            return Check(self.kind, "fail", label, f"the stored token for {conn['login']} was rejected (revoked?)",
                         self.login_fix(conn))
        actual = body.get("login")
        if status != 200 or not actual:
            return Check(self.kind, "fail", label, f"GitHub answered {status}", "retry, or: gh auth status")
        if actual.lower() != conn["login"].lower():
            return Check(self.kind, "fail", label,
                         f"gh's stored login for {conn['login']} actually authenticates as {actual}", self.login_fix(conn))
        return Check(self.kind, "ok", label, "login confirmed")

    def whoami(self, token: str) -> str | None:
        try:
            status, body = self.fetch(token, self.timeout)
        except (OSError, ValueError):
            return None
        return body.get("login") if status == 200 else None
