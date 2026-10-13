"""AWS: a named profile plus the 12-digit account it must resolve to.

Connection:  aws = { profile = "acme", account = "123456789012" }
             optional: region = "eu-central-1", config_file = "~/.aws/config-acme"

inject  — drop every inherited AWS credential/profile variable (they beat
          AWS_PROFILE in the CLI's precedence), then set AWS_PROFILE and
          AWS_EXPECTED_ACCOUNT_ID (what a direnv `use aws PROFILE ACCOUNT` sets).
local   — profile exists; account hints in the config (sso_account_id,
          login_session / role ARN) match; SSO and console-sign-in caches
          tell whether you are signed in and for how long.
remote  — `aws sts get-caller-identity` (needs no permissions) must return
          the expected account. Never starts a login; prints the command.
"""

from __future__ import annotations

import configparser
import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..checks import Check, human, mask_account
from ..realbin import find_real
from .base import Adapter

ACCOUNT_RE = re.compile(r"^\d{12}$")
ARN_ACCOUNT = re.compile(r"^arn:aws[a-z-]*:(?:iam|sts)::(\d{12}):")
CONSOLE_SESSION_MAX = timedelta(hours=12)  # `aws login` refreshes for at most 12 hours

# Inherited variables that could override or confuse the project's profile.
SCRUB = (
    "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_SECURITY_TOKEN",
    "AWS_WEB_IDENTITY_TOKEN_FILE", "AWS_ROLE_ARN", "AWS_ROLE_SESSION_NAME",
    "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI", "AWS_CONTAINER_CREDENTIALS_FULL_URI",
    "AWS_CONTAINER_AUTHORIZATION_TOKEN", "AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE",
    "AWS_SESSION_EXPIRATION", "AWS_CREDENTIAL_EXPIRATION",
    "AWS_PROFILE", "AWS_DEFAULT_PROFILE", "AWS_EXPECTED_ACCOUNT_ID",
    "AWS_CONFIG_FILE", "AWS_SHARED_CREDENTIALS_FILE", "AWS_REGION", "AWS_DEFAULT_REGION",
)


@dataclass
class Profile:
    name: str
    kind: str  # sso | login | role | process | static | unknown | missing | unreadable
    account_hint: str | None = None
    sso_session: str | None = None
    sso_start_url: str | None = None
    login_session: str | None = None
    process: str | None = None
    source_profile: str | None = None


def _home(env: dict) -> Path:
    return Path(env.get("HOME") or Path.home())


def config_path(env: dict) -> Path:
    if env.get("AWS_CONFIG_FILE"):
        return Path(env["AWS_CONFIG_FILE"]).expanduser()
    return _home(env) / ".aws" / "config"


def credentials_path(env: dict) -> Path:
    if env.get("AWS_SHARED_CREDENTIALS_FILE"):
        return Path(env["AWS_SHARED_CREDENTIALS_FILE"]).expanduser()
    return _home(env) / ".aws" / "credentials"


def sso_cache_file(p: Profile, env: dict) -> Path:
    key = p.sso_session or p.sso_start_url or ""
    return _home(env) / ".aws" / "sso" / "cache" / (hashlib.sha1(key.encode()).hexdigest() + ".json")


def login_cache_file(p: Profile, env: dict) -> Path:
    base = env.get("AWS_LOGIN_CACHE_DIRECTORY")
    folder = Path(os.path.expandvars(base)).expanduser() if base else _home(env) / ".aws" / "login" / "cache"
    return folder / (hashlib.sha256((p.login_session or "").encode()).hexdigest() + ".json")


def _parse(path: Path) -> configparser.RawConfigParser | None:
    """The INI file, or None when it does not parse. configparser's errors quote the offending line, and in
    ~/.aws/credentials that line can be a secret key: no such error may reach a message."""
    cfg = configparser.RawConfigParser()
    try:
        cfg.read(path)
    except (configparser.Error, UnicodeDecodeError):
        return None
    return cfg


def profile_names(env: dict) -> list[str]:
    """The named profiles in the AWS config file, in file order (never `default`)."""
    cfg = _parse(config_path(env))
    if cfg is None:
        return []
    return [section[8:].strip() for section in cfg.sections() if section.startswith("profile ")]


def read_profile(name: str, env: dict) -> Profile:
    cfg = _parse(config_path(env))
    if cfg is None:
        return Profile(name, "unreadable")
    section = "default" if name == "default" else f"profile {name}"

    def get(key: str) -> str | None:
        return cfg.get(section, key).strip() if cfg.has_option(section, key) else None

    def from_credentials_file() -> str:
        """"static" when the credentials file has a key for the profile, "unreadable" when it does not parse."""
        creds = _parse(credentials_path(env))
        if creds is None:
            return "unreadable"
        return "static" if creds.has_option(name, "aws_access_key_id") else ""

    if not cfg.has_section(section):
        return Profile(name, from_credentials_file() or "missing")
    if get("sso_session") or get("sso_start_url"):
        session, url = get("sso_session"), get("sso_start_url")
        session_section = f"sso-session {session}"
        if session and not url and cfg.has_option(session_section, "sso_start_url"):
            url = cfg.get(session_section, "sso_start_url").strip()
        return Profile(name, "sso", account_hint=get("sso_account_id"), sso_session=session, sso_start_url=url)
    if get("login_session"):
        m = ARN_ACCOUNT.match(get("login_session"))
        return Profile(name, "login", account_hint=m.group(1) if m else None, login_session=get("login_session"))
    if get("role_arn"):
        m = ARN_ACCOUNT.match(get("role_arn"))
        return Profile(name, "role", account_hint=m.group(1) if m else None, source_profile=get("source_profile"))
    if get("credential_process"):
        return Profile(name, "process", process=get("credential_process"))
    if get("aws_access_key_id"):
        return Profile(name, "static")
    return Profile(name, from_credentials_file() or "unknown")


def _parse_time(value) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("UTC", "+00:00").replace("Z", "+00:00"))
    except ValueError:
        return None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _sso_login_command(p: Profile) -> str:
    return f"aws sso login --sso-session {p.sso_session}" if p.sso_session else f"aws sso login --profile {p.name}"


def _login_fix(p: Profile) -> str:
    if p.kind == "sso":
        return _sso_login_command(p)
    if p.kind == "login":
        return f"aws login --profile {p.name}"
    return f"aws sts get-caller-identity --profile {p.name}   (shows the full error)"


class AwsAdapter(Adapter):
    kind = "aws"

    def __init__(self, timeout: float = 40):
        self.timeout = timeout  # cold STS round-trips measured at 5.6–15.8 s on this Mac

    def label(self, conn) -> str:
        return f"{conn.get('profile', '?')} → {mask_account(str(conn.get('account', '')))}"

    # inject -----------------------------------------------------------------

    def inject(self, conn, env: dict) -> None:
        for var in SCRUB:
            env.pop(var, None)
        env["AWS_PROFILE"] = conn["profile"]
        env["AWS_EXPECTED_ACCOUNT_ID"] = str(conn["account"])
        if conn.get("config_file"):
            env["AWS_CONFIG_FILE"] = str(Path(conn["config_file"]).expanduser())
        if conn.get("region"):
            env["AWS_REGION"] = env["AWS_DEFAULT_REGION"] = conn["region"]

    # local ------------------------------------------------------------------

    def local(self, conn, env: dict) -> Check:
        profile, account = conn.get("profile", ""), str(conn.get("account", ""))
        label = self.label(conn)

        def fail(detail: str, fix: str) -> Check:
            return Check(self.kind, "fail", label, detail, fix)

        if not profile or profile == "default":
            return fail("a named profile is required (never `default`)", "keyfit add aws --profile <name>")
        if not ACCOUNT_RE.match(account):
            return fail("the expected account must be a 12-digit AWS account ID",
                        f"keyfit add aws --profile {profile} --account <12 digits>")

        p = read_profile(profile, env)
        if p.kind == "unreadable":
            return fail(f"{config_path(env)} or {credentials_path(env)} does not parse "
                        "(the line is not shown: it may hold a key)",
                        f"aws configure list --profile {profile}   (names the file)")
        if p.kind == "missing":
            return fail(f"no profile '{profile}' in {config_path(env)}",
                        f"aws configure sso --profile {profile}   (or: keyfit add aws --profile <existing profile>)")
        if p.account_hint and p.account_hint != account:
            return fail(f"profile '{profile}' points at account {mask_account(p.account_hint)}, "
                        f"project expects {mask_account(account)}",
                        "keyfit add aws --profile <the right profile>")

        if p.kind == "sso":
            return self._local_sso(p, label, env)
        if p.kind == "login":
            return self._local_login(p, account, label, env)
        if p.kind == "process":
            via = "IAM key via Granted" if "granted" in (p.process or "") else "credential_process"
            if "--auto-login" in (p.process or ""):
                return Check(self.kind, "warn", label, f"{via} · --auto-login may open a browser during checks",
                             f"remove --auto-login from profile {profile} in ~/.aws/config", pending=True)
            return Check(self.kind, "ok", label, via, pending=True)
        if p.kind == "static":
            return Check(self.kind, "warn", label, "plaintext access key in ~/.aws/credentials",
                         f"granted credentials import {profile}", pending=True)
        if p.kind == "role":
            return Check(self.kind, "ok", label, f"assume-role via {p.source_profile or 'credential source'}", pending=True)
        return Check(self.kind, "ok", label, "profile found", pending=True)

    def _local_sso(self, p: Profile, label: str, env: dict) -> Check:
        fix = _sso_login_command(p)
        cache = sso_cache_file(p, env)
        if not cache.exists():
            return Check(self.kind, "fail", label, "not signed in to SSO", fix)
        try:
            token = json.loads(cache.read_text())
        except (OSError, ValueError):
            return Check(self.kind, "fail", label, "SSO token cache is unreadable", fix)
        now = _now()
        expires = _parse_time(token.get("expiresAt"))
        refreshable = bool(token.get("refreshToken"))
        if expires and expires > now:
            detail = f"SSO token valid {human((expires - now).total_seconds())}"
            return Check(self.kind, "ok", label, detail + (" · refreshes automatically" if refreshable else ""), pending=True)
        registration = _parse_time(token.get("registrationExpiresAt"))
        if refreshable and (registration is None or registration > now):
            return Check(self.kind, "ok", label, "SSO token due for refresh", fix, pending=True)
        return Check(self.kind, "fail", label, "SSO session expired", fix)

    def _local_login(self, p: Profile, account: str, label: str, env: dict) -> Check:
        fix = f"aws login --profile {p.name}"
        cache = login_cache_file(p, env)
        if not cache.exists():
            return Check(self.kind, "fail", label, "not signed in (aws login)", fix)
        try:
            token = json.loads(cache.read_text())
        except (OSError, ValueError):
            return Check(self.kind, "fail", label, "sign-in cache is unreadable", fix)
        access = token.get("accessToken") or {}
        cached_account = str(access.get("accountId") or "")
        if cached_account and cached_account != account:
            return Check(self.kind, "fail", label,
                         f"signed in to account {mask_account(cached_account)}, project expects {mask_account(account)}",
                         "keyfit add aws --profile <the right profile>")
        now = _now()
        expires = _parse_time(access.get("expiresAt"))
        if expires and expires > now:
            return Check(self.kind, "ok", label, "console sign-in active", fix, pending=True)
        # Credentials are re-issued every 15 minutes for at most 12 hours after `aws login`;
        # the session can therefore not outlive the last issue time by more than 12 hours.
        if expires is None or now - expires > CONSOLE_SESSION_MAX or not token.get("refreshToken"):
            return Check(self.kind, "fail", label, "console sign-in expired (12 h limit)", fix)
        return Check(self.kind, "ok", label, "console sign-in due for refresh", fix, pending=True)

    # remote -----------------------------------------------------------------

    def identify(self, profile: str, env: dict) -> tuple[str | None, str, bool]:
        """Return (account, "", False) or (None, reason, transient) from `aws sts get-caller-identity`."""
        aws = find_real("aws", env)
        if aws is None:
            return None, "aws CLI not found on PATH", False
        cmd = [aws, "sts", "get-caller-identity", "--profile", profile, "--query", "Account",
               "--output", "text", "--cli-connect-timeout", "5", "--cli-read-timeout", "10"]
        try:
            out = subprocess.run(cmd, env=env, capture_output=True, text=True,
                                 timeout=self.timeout, stdin=subprocess.DEVNULL, check=False)
        except subprocess.TimeoutExpired:
            return None, f"AWS did not answer within {self.timeout:g} s", True
        if out.returncode != 0:
            lines = [line.strip() for line in out.stderr.splitlines() if line.strip()]
            reason = (lines[-1] if lines else f"aws exited with {out.returncode}")[:200]
            network = any(s in reason for s in ("Could not connect", "EndpointConnectionError", "Connect timeout",
                                                  "Read timeout", "Temporary failure in name resolution"))
            return None, reason, network
        account = out.stdout.strip()
        if not ACCOUNT_RE.match(account):
            return None, "unexpected answer from sts get-caller-identity", False
        return account, "", False

    def fingerprint(self, conn, env: dict) -> str:
        """A new SSO or console sign-in rewrites its cache file; its mtime busts cached checks."""
        p = read_profile(conn.get("profile", ""), env)
        cache = sso_cache_file(p, env) if p.kind == "sso" else login_cache_file(p, env) if p.kind == "login" else None
        try:
            return str(cache.stat().st_mtime_ns) if cache else ""
        except OSError:
            return ""

    def remote(self, conn, env: dict) -> Check:
        profile, account = conn["profile"], str(conn["account"])
        label = self.label(conn)
        actual, reason, network = self.identify(profile, env)
        if actual is None:
            p = read_profile(profile, env)
            if p.kind == "process" and "did not answer" in reason:
                reason += " (a Keychain prompt may be waiting — choose Always Allow)"
            return Check(self.kind, "fail", label, reason, _login_fix(p), transient=network)
        if actual != account:
            return Check(self.kind, "fail", label,
                         f"profile '{profile}' is signed in to account {mask_account(actual)}, "
                         f"project expects {mask_account(account)}",
                         "keyfit add aws --profile <the right profile>")
        return Check(self.kind, "ok", label, "account confirmed")


def parse_envrc(text: str) -> dict | None:
    """Read a `use aws PROFILE ACCOUNT` line (a direnv helper) from an .envrc, if present."""
    for line in text.splitlines():
        m = re.match(r"^\s*use\s+aws\s+(\S+)\s+(\d{12})\s*(?:#.*)?$", line)
        if m:
            return {"profile": m.group(1), "account": m.group(2)}
    return None
