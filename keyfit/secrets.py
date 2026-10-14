"""Secret references: the registry stores where a secret lives, never the secret.

    env:LINEAR_ACME_API_KEY              an environment variable of the launching shell
    keychain:<service>/<account>         a macOS Keychain generic password (read with /usr/bin/security)

Values are only ever placed into the environment of the launched tool. Keys
pasted into Keyfit are written to the Keychain (service `keyfit`) through the
stdin of `security -i`, so a value never appears in any process's arguments.
"""

from __future__ import annotations

import re
import subprocess
import sys

SECURITY = "/usr/bin/security"  # Apple-signed; Keychain items made by `security` trust it without prompts
SERVICE = "keyfit"  # Keychain service of the items Keyfit stores itself
NOT_MACOS = "Keyfit stores keys in the macOS Keychain; Linux is planned"
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")  # account names Keyfit stores under (use fullmatch)
_STORABLE = re.compile(r"[\x20-\x7e]+")  # printable ASCII: `find-generic-password -w` prints anything else as hex
_MAX_LINE = 4000  # `security -i` reads 4095 bytes per line and runs the rest as a separate command
# Keys of the kinds Keyfit handles, typed where a name or reference belongs (`--key lin_api_…`): never echoed back
_KEY_LIKE = re.compile(r"lin_(?:api|oauth)_[A-Za-z0-9]{8,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_\w{20,}|sk-[\w-]{20,}"
                       r"|sbp_(?:oauth_|v0_)?[a-f0-9]{20,}|eyJ2MiI6[\w+/=-]{16,}|(?:dev|prod|preview|project):[\w:.-]*\|\S{16,}")


def looks_like_key(text: str) -> bool:
    return bool(_KEY_LIKE.search(text or ""))


def normalise(ref: str) -> str:
    """Accept a bare variable name as shorthand for env:NAME."""
    if ":" not in ref:
        return f"env:{ref}"
    return ref


def describe(ref: str) -> str:
    if looks_like_key(ref):
        return "a key written where its reference belongs (not shown)"
    scheme, _, rest = normalise(ref).partition(":")
    if scheme == "env":
        return f"${rest}"
    if scheme == "keychain":
        return f"Keychain item {rest}"
    return ref


def exists(ref: str, env: dict) -> bool:
    """True when the secret is there — for Keychain items without reading the value."""
    scheme, _, rest = normalise(ref).partition(":")
    if scheme == "env":
        return bool(env.get(rest))
    if scheme == "keychain":
        service, _, account = rest.rpartition("/")
        if not service or not account:
            return False
        try:
            out = subprocess.run([SECURITY, "find-generic-password", "-s", service, "-a", account],
                                 capture_output=True, text=True, timeout=15, check=False)
        except (OSError, subprocess.SubprocessError):
            return False
        return out.returncode == 0
    return False


def resolve(ref: str, env: dict) -> str | None:
    scheme, _, rest = normalise(ref).partition(":")
    if scheme == "env":
        value = env.get(rest, "")
        return value or None
    if scheme == "keychain":
        service, _, account = rest.rpartition("/")
        if not service or not account:
            return None
        try:
            out = subprocess.run([SECURITY, "find-generic-password", "-s", service, "-a", account, "-w"],
                                 capture_output=True, text=True, timeout=15, check=False)
        except (OSError, subprocess.SubprocessError):
            return None
        value = out.stdout.rstrip("\n")
        return value if out.returncode == 0 and value else None
    return None


def can_store() -> bool:
    return sys.platform == "darwin"


def keychain_reachable() -> str:
    """"" when `security` answers a read-only question (the keychain search list), else the reason. Reads no item."""
    if not can_store():
        return NOT_MACOS
    try:
        out = subprocess.run([SECURITY, "list-keychains"], capture_output=True, text=True, timeout=15, check=False)
    except (OSError, subprocess.SubprocessError) as err:
        return f"could not run {SECURITY} ({type(err).__name__})"
    if out.returncode != 0 or not out.stdout.strip():
        said = out.stderr.strip().splitlines()[:1]
        return said[0] if said else f"{SECURITY} list-keychains exited with {out.returncode}"
    return ""


def _quote(arg: str) -> str:
    """One argument for `security -i`'s line parser: inside "…" a backslash makes the next character literal."""
    return '"' + arg.replace("\\", "\\\\").replace('"', '\\"') + '"'


def store(service: str, account: str, value: str) -> str:
    """Write (or replace, -U) a generic password and read it back. Returns "" on success, else the reason.

    The command goes to the stdin of `security -i`, never into argv; errors never include the value.
    """
    if not (_STORABLE.fullmatch(service) and NAME.fullmatch(account)):
        return f"'{service}/{account}' is not a name Keyfit stores under"
    if not _STORABLE.fullmatch(value):  # fullmatch: `$` would let a trailing newline through
        return "the value is empty or has characters Keyfit cannot store safely (line breaks, tabs, non-ASCII)"
    line = " ".join(["add-generic-password", "-U", "-s", _quote(service), "-a", _quote(account),
                     "-w", _quote(value)]) + "\n"  # without the newline `security -i` drops the line silently
    if len(line) > _MAX_LINE:
        return "the value is too long to store this way"
    try:
        out = subprocess.run([SECURITY, "-i"], input=line, capture_output=True, text=True, timeout=60, check=False)
    except (OSError, subprocess.SubprocessError) as err:
        return f"could not run {SECURITY} ({type(err).__name__})"
    if out.returncode != 0:
        said = out.stderr.strip().splitlines()[:1]
        return said[0] if said and value not in said[0] else f"{SECURITY} refused (exit {out.returncode})"
    if resolve(f"keychain:{service}/{account}", {}) != value:
        return "the Keychain item did not read back as stored"
    return ""
