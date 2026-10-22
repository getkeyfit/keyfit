"""Cloudflare: which wrangler sign-in (auth profile) and Cloudflare account a project deploys with.

Account:  [accounts.cloudflare.acme]
          profile = "acme"   (a wrangler auth profile; "default" is the sign-in of `wrangler login`)
          account_id = "<32 hex digits>"   account_name = "Acme"
Project:  cloudflare = "acme"

wrangler keeps named sign-ins since 4.106 (June 2026): `wrangler auth create NAME` signs in, `wrangler auth activate
NAME DIR` binds a profile to a folder and its subfolders. Without --profile, wrangler uses the profile of the nearest
bound folder (the folder it reads its config from), else the default profile. CLOUDFLARE_API_TOKEN (or
CLOUDFLARE_API_KEY with CLOUDFLARE_EMAIL; older names CF_…) beats every profile, also from the folder's .env or
.env.local, which wrangler loads into its environment for each variable not set already (with --env NAME also
.env.NAME and .env.NAME.local; with --env-file, those files instead). The same files can set where the profile's
sign-in goes: CLOUDFLARE_API_BASE_URL (the host of every API request, which carries the token) and wrangler's
sign-in endpoints (WRANGLER_AUTH_DOMAIN, …_AUTH_URL, …_TOKEN_URL, …_REVOKE_URL, …_CLIENT_ID). The account is
account_id in the wrangler config (wrangler.json, wrangler.jsonc, wrangler.toml, each looked for upwards in that
order from the folder, or from the folder of an entry file: `wrangler deploy src/index.ts`), else
CLOUDFLARE_ACCOUNT_ID, else the one picked at sign-in; an account the sign-in cannot reach fails, it never falls back.
Profiles live in wrangler's config folder (~/.wrangler when it exists, else ~/Library/Preferences/.wrangler):
config/<profile>.toml, or .enc when wrangler keeps the key in the Keychain, and profiles/directory-bindings.json
(folder → profile, compared as text). Keyfit reads the bindings and the profile names, never a sign-in, and keeps no
Cloudflare token of its own: wrangler switches, Keyfit checks and enforces.

inject  — remove inherited credentials that would beat the profile (CLOUDFLARE_API_TOKEN, CLOUDFLARE_API_KEY,
          CLOUDFLARE_EMAIL and their CF_ names) and set CLOUDFLARE_ACCOUNT_ID to the account, so every wrangler the
          session starts (`npx wrangler` from node_modules too) works on it or fails loudly. The folder's binding
          picks the sign-in. CLOUDFLARE_API_BASE_URL is set to Cloudflare's own (a variable already set beats the
          folder's .env) and inherited sign-in endpoints are removed.
forget  — in a project without a Cloudflare account, the CLOUDFLARE_ACCOUNT_ID Keyfit set for another project goes.
local   — no .env file wrangler may load in the folder sets CLOUDFLARE_API_BASE_URL, a sign-in endpoint or
          XDG_CONFIG_HOME (only the names are read); the profile exists; the folder resolves to it (bound there or
          above, not shadowed by a subfolder's binding); the wrangler configs wrangler may read (the one it finds and
          the one a build's .wrangler/deploy/config.json redirects deploy to) set no other account_id (top level and
          env.*), and one Keyfit cannot parse fails; the wranglers `npx wrangler` and `wrangler` would run here are new
          enough for profiles (a warning when a version is unknown).
remote  — `wrangler whoami --json` in the folder (wrangler renews an expired sign-in first, as every command does)
          must list the account. It runs with the shell's environment and the account only, never the project's
          other tokens (an agent start holds them all).
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

API = "https://api.cloudflare.com/client/v4"  # wrangler's API host when CLOUDFLARE_API_BASE_URL is not set
ACCOUNT = re.compile(r"[0-9a-f]{32}")  # a Cloudflare account ID (use fullmatch)
PROFILE = re.compile(r"[A-Za-z0-9_-]+")  # wrangler's profile names (use fullmatch)
DEFAULT = "default"  # the profile of `wrangler login`; "staging" is reserved too
MINIMUM = (4, 106, 0)  # the first wrangler with auth profiles
MARK = "KEYFIT_CLOUDFLARE"  # set next to the CLOUDFLARE_ACCOUNT_ID Keyfit injected: the account's name
# Credentials wrangler takes before any profile, and the account variable Keyfit sets itself (older CF_ names too).
SCRUB = ("CLOUDFLARE_API_TOKEN", "CF_API_TOKEN", "CLOUDFLARE_API_KEY", "CF_API_KEY", "CLOUDFLARE_EMAIL", "CF_EMAIL",
         "CLOUDFLARE_ACCOUNT_ID", "CF_ACCOUNT_ID")
# Where wrangler sends a profile's sign-in: the API host (pinned to API) and the sign-in endpoints, which renew it
# (token URL), sign in (auth domain and URL, client ID) and sign out (revoke URL). Inherited ones are removed.
HOST = "CLOUDFLARE_API_BASE_URL"
ENDPOINTS = (HOST, "WRANGLER_AUTH_DOMAIN", "WRANGLER_AUTH_URL", "WRANGLER_TOKEN_URL", "WRANGLER_REVOKE_URL",
             "WRANGLER_CLIENT_ID")
STORE = "XDG_CONFIG_HOME"  # where wrangler keeps its profiles and bindings when ~/.wrangler does not exist
DOTENV_NAMES = (*ENDPOINTS, STORE)  # what no .env file wrangler loads may set (Keyfit pins them, or reads the shell's)
# A variable set in a .env file, as the dotenv package wrangler uses reads one: `NAME=…`, `export NAME=…`, `NAME: …`
# at the start of a line (anything that looks like one counts, also inside a quoted value: a false alarm, never a miss).
DOTENV_LINE = re.compile(r"[\s\ufeff]*(?:export\s+)?([A-Za-z0-9_.-]+)\s*[=:]")
CONFIGS = ("wrangler.json", "wrangler.jsonc", "wrangler.toml")
DEPLOY = Path(".wrangler") / "deploy" / "config.json"  # a build tool's redirect to the config it generated
BINDINGS = Path("profiles") / "directory-bindings.json"
LINE_END = re.compile(r"[\r\n]")  # where a // comment ends in JSON with comments (a lone CR too)
# Options that take a value (yargs: `--name value`, `--name=value`, `-c value`, `-cVALUE`, `-ec value`): wrangler's
# global ones and those of the commands Keyfit looks at (login, auth create, whoami; deploy's entry file), with yargs'
# camelCase spellings. Every other option is read as a flag, so `wrangler --x-provision login` is still `login`.
VALUED = {"c", "config", "cwd", "e", "env", "profile", "scopes", "callback-host", "callbackHost", "callback-port",
          "callbackPort", "account", "script", "path"}
# Those wrangler declares with requiresArg, which yargs reads like nargs 1 (eatNargs): the next argument is the value
# unless it looks like an option (/^-[^0-9]/), so `--config -` and `--config -0x.toml` name files.
NARGS = {"c", "config", "cwd", "e", "env", "profile", "script"}
OPTIONS = {"c": "--config", "config": "--config", "cwd": "--cwd", "profile": "--profile", "env-file": "--env-file",
           "envFile": "--env-file", "e": "--env", "env": "--env", "script": "--script", "path": "--path"}
# Commands whose arguments name the Worker's entry file, from whose folder wrangler looks for its config: deploy and
# versions upload take a file or a folder of static assets (`path`), dev and preview a file (`script`).
ENTRY_COMMANDS = (("versions", "upload"), ("deploy",), ("dev",), ("preview",))
ASSETS_TOO = (("versions", "upload"), ("deploy",))
ARRAYS = {"env-file", "envFile"}  # options that take every following argument up to the next option
NEGATIVE = re.compile(r"-([0-9]+(\.[0-9]+)?|\.[0-9]+)")  # a negative number: an argument, not options (fullmatch)
NUMBER = re.compile(r"-?[0-9]+(\.[0-9]*)?(e-?[0-9]+)?")  # taken by the letter it is attached to (fullmatch)
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


def redirected(folder, user: Path | None) -> tuple[Path, Path] | None:
    """(config, redirect file) for the config a build tool generated (the Vite plugin's dist/<name>/wrangler.json) that
    deploy, dev, versions upload and the other deploying commands read instead of *user*: the configPath in
    .wrangler/deploy/config.json, looked for upwards from *folder*, when that file sits beside *user* or there is
    none. The redirect file twice when Keyfit cannot read it (its check then fails); None without one, or where
    wrangler stops on it."""
    start = Path(os.path.abspath(folder))
    deploy = next((d / DEPLOY for d in (start, *start.parents) if (d / DEPLOY).is_file()), None)
    if deploy is None or (user is not None and user.parent / DEPLOY != deploy):
        return None  # elsewhere than beside the config: wrangler stops ("do not share the same base path")
    data = _parse(deploy)
    if data is None:
        return deploy, deploy
    target = data.get("configPath") if isinstance(data, dict) else None
    if not isinstance(target, str) or not target:
        return None  # wrangler stops: no configPath
    path = Path(os.path.abspath(os.path.join(deploy.parent, target)))
    return (path, deploy) if path.exists() else None  # else wrangler stops: it points nowhere


def configs(folder, entry: str | None = None) -> list[tuple[Path, Path | None]]:
    """(config, the redirect that leads to it or None) for each config file wrangler may read in *folder* (its working
    folder): the one it finds upwards from there or, given an entry file (`wrangler deploy src/index.ts`), from that
    file's folder, and the one a build tool's .wrangler/deploy/config.json redirects the deploying commands to."""
    starts = [folder]
    if entry is not None:
        parent = os.path.dirname(entry) or "."
        # wrangler 4.152 starts that search a folder deeper for a relative path (path.resolve(dir, dir): src/src for
        # src/index.ts); both searches are checked
        starts = [os.path.join(folder, parent), os.path.join(folder, parent, parent)]
    found: dict[Path, Path | None] = {}
    for start in dict.fromkeys(os.path.abspath(s) for s in starts):
        user = find_config(start)
        if user is not None:
            found.setdefault(user, None)
        redirect = redirected(start, user)
        if redirect is not None:
            found.setdefault(*redirect)
    return list(found.items())


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
            end = LINE_END.search(text, i)
            i = end.start() if end else n
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end < 0 else end + 2
        elif c == ",":
            j = i + 1
            while j < n and (text[j].isspace() or text.startswith(("//", "/*"), j)):
                if text.startswith("//", j):
                    end = LINE_END.search(text, j)
                    j = end.start() if end else n
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


def _parse(path: Path):
    """A config file as wrangler reads it: a UTF-8 byte order mark dropped and bytes that are not UTF-8 replaced, then
    TOML for .toml, else JSON with comments. None when Keyfit cannot read or parse it."""
    import tomllib

    try:
        text = path.read_bytes().decode("utf-8-sig", errors="replace")
        return tomllib.loads(text) if path.suffix == ".toml" else json.loads(_jsonc(text))
    except (OSError, ValueError, RecursionError):
        return None


def account_ids(path: Path) -> list[tuple[str, object]] | None:
    """(where, value) of every account_id in a wrangler config: the top level ("") and each env.NAME. None when the
    file cannot be read or parsed (a missing file stops wrangler too; a file Keyfit cannot parse fails the check)."""
    if path.suffix not in (".toml", ".json", ".jsonc"):
        return []  # wrangler reads no settings from it
    data = _parse(path)
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
    """wrangler's version from the package.json next to its bin/ (npm, Homebrew and bun link to it), or next to the
    bin/wrangler.js a pnpm script runs (`exec node "$basedir/../wrangler/bin/wrangler.js"`); None when unknown."""
    real = os.path.realpath(binary)
    places = [real]
    try:
        with open(real, "rb") as fh:
            head = fh.read(4096).decode("utf-8", "replace")
    except OSError:
        head = ""
    if head.startswith("#!"):
        places += [os.path.realpath(os.path.join(os.path.dirname(real), script))
                   for script in re.findall(r'"\$basedir/([^"$]*\bwrangler\.js)"', head)]
    for place in dict.fromkeys(places):
        try:
            data = json.loads((Path(place).parent.parent / "package.json").read_text())
        except (OSError, ValueError):
            continue
        match = re.match(r"(\d+)\.(\d+)\.(\d+)", str(data.get("version", ""))) if isinstance(data, dict) else None
        if match and data.get("name") == "wrangler":
            return tuple(int(part) for part in match.groups())
    return None


def shell_path(path) -> str:
    """A folder as a shell argument: ~/… for the home folder, with special characters escaped (Acme\\ Corp)."""
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


def mismatch(conn, folder, env: dict, asked: str | None = None, config: Path | None = None,
             entry: str | None = None) -> tuple[str, str] | None:
    """Why wrangler started in *folder* would not use the account's profile or account: (detail, fix), else None.
    *asked*: a --profile given on the command line; *config*: a --config file (else those wrangler may find, see
    configs(), from the folder of *entry*, an entry file given on the command line, when there is one)."""
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
    for path, via in [(config, None)] if config else configs(folder, entry):
        found = account_ids(path)
        if found is None and path.is_file():  # wrangler may read what Keyfit cannot: a failed check, never a skip
            shown = printable(tilde(path))
            syntax = "TOML 1.0 (not yet TOML 1.1)" if path.suffix == ".toml" else "JSON with comments"
            return (f"Keyfit cannot read {shown}, so it cannot tell which account_id wrangler would use",
                    f"fix {shown}: Keyfit reads it as {syntax}" + (", or delete it if a build left it behind"
                                                                 if path == via else ""))
        for where, value in found or []:
            if value != expected:
                shown = printable(value) + (f" in [{where}]" if where else "")
                if via:  # generated: building again rewrites it from the project's own config
                    return (f"{tilde(path)} (where {tilde(via)} sends wrangler deploy) sets account_id {shown}, "
                            f"project expects {title} ({expected})",
                            f"build again, or delete {tilde(via)} if a build left it behind")
                return (f"{tilde(path)} sets account_id {shown}, project expects {title} ({expected})",
                        f"set account_id = \"{expected}\" there; if the project moved: keyfit add cloudflare --profile "
                        f"{profile} --account {printable(value)}")
    return None


def _takes(name: str, nxt: str | None, short: bool = False) -> bool:
    """Whether option *name* takes the next argument as its value, as yargs-parser decides: one that takes a value,
    unless the next argument is an option (for the last letter of a group, `-x` or `--x`; a negative number is not),
    and any option followed by `true` or `false`. One of NARGS takes it unless it starts with `-` and a non-digit."""
    if nxt is None or nxt in ("true", "false"):
        return nxt is not None
    if name in NARGS:
        return not re.match(r"-[^0-9]", nxt) or bool(NEGATIVE.fullmatch(nxt))
    option = re.match(r"(-|--)[^-]", nxt) if short else nxt.startswith("-")
    return name in VALUED and (not option or bool(NEGATIVE.fullmatch(nxt)))


def _read(args: list[str]):
    """A wrangler command line as wrangler's parser (yargs-parser 21) reads it, up to `--`: (name, value) for each
    option it sets (value None when it takes none, or none was given) and (None, word) for each command word.
    Short options come in groups: `-ec FILE` sets e and c, and the letter c takes the rest of `-c/abs/w.toml`,
    `-c~/w.toml` or `-c=w.toml` (an attached value starting with `=`, a non-word character or a number)."""
    i = 0
    while i < len(args):
        arg, nxt = args[i], args[i + 1] if i + 1 < len(args) else None
        i += 1
        if arg == "--":
            return
        if not arg.startswith("-") or arg == "-" or NEGATIVE.fullmatch(arg) or re.fullmatch(r"---+(=.*)?", arg, re.S):
            yield None, arg
        elif arg.startswith("--"):
            name, eq, value = arg[2:].partition("=")
            if name in ARRAYS:  # every argument up to the next option: `--env-file a.env b.env`
                values = [value] if eq else []
                while i < len(args) and (not args[i].startswith("-") or NEGATIVE.fullmatch(args[i])):
                    values.append(args[i])
                    i += 1
                yield from ((name, v or None) for v in values or [None])
                continue
            if not eq and not name.startswith("no-") and _takes(name, nxt):  # `--no-x` is x = false
                value, i = nxt, i + 1
            yield name, value or None
        elif re.match(r"-.\..+=", arg):  # `-c.x=1`: a dotted key (wrangler gets an object, not a path)
            yield arg[1:].partition("=")[0], None
        elif re.match(r"-.\..+", arg):  # `-c./w.toml VALUE` too: the next argument is the object's value
            if nxt is not None and not nxt.startswith("-"):
                i += 1
            yield arg[1:], None
        else:
            letters, broken = arg[1:-1], False
            for j, letter in enumerate(letters):
                rest, after = arg[j + 2:], letters[j + 1:j + 2]
                if after == "=":
                    yield letter, arg[j + 3:] or None
                    broken = True
                    break
                if rest == "-":
                    yield letter, rest
                    continue
                attached = re.fullmatch(r"[A-Za-z]", letter) and NUMBER.fullmatch(rest)  # `-c123`
                if attached or re.fullmatch(r"[^A-Za-z0-9_]", after):  # `-c/abs/w.toml`, `-c~/w.toml`
                    yield letter, rest
                    broken = True
                    break
                yield letter, None
            last = arg[-1]
            if not broken and last != "-":
                value = None
                if _takes(last, nxt, short=True):
                    value, i = nxt, i + 1
                yield last, value or None


def options(args: list[str]) -> list[tuple[str, str | None]]:
    """(long name, value) of each --profile, --config, --cwd and --env-file option, in every spelling wrangler's parser
    takes: `--config FILE`, `--config=FILE`, `--c FILE`, `-c FILE`, `-c=FILE`, `-cFILE` (FILE starting with a non-word
    character: `-c/abs/w.toml`, `-c~/w.toml`) and a group of short options with c in it (`-ec FILE`, `-ec./w.toml`)."""
    return [(OPTIONS[name], value) for name, value in _read(args) if name in OPTIONS]


def words(args: list[str]) -> list[str]:
    """The command words and arguments, without options and their values (read as in options())."""
    return [word for name, word in _read(args) if name is None]


def entries(args: list[str], folder) -> list[str]:
    """The entry files a command line may give wrangler in *folder*: --script, and --path or every argument after the
    command words of deploy, versions upload, dev and preview, except a folder deploy and versions upload take as
    static assets. A value of an option Keyfit reads as a flag counts too: a false alarm, never a miss. (Arguments
    after `--` are neither: yargs fills positionals before it hands them on.)"""
    said = words(args)
    command = next((c for c in ENTRY_COMMANDS if tuple(said[:len(c)]) == c), None)
    found = [value for name, value in options(args) if name == "--script" and value]
    if command is not None:
        given = [value for name, value in options(args) if name == "--path" and value] + said[len(command):]
        found += [path for path in given if command not in ASSETS_TOO or not os.path.isdir(os.path.join(folder, path))]
    return list(dict.fromkeys(found))


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
    """The environment Keyfit runs wrangler with for a check: no credentials that would beat the profile, Cloudflare's
    own API host and sign-in endpoints (set, they beat the folder's .env), no metrics, no colours."""
    run = {k: v for k, v in env.items() if (k not in SCRUB or k == "CLOUDFLARE_ACCOUNT_ID") and k not in ENDPOINTS}
    run.update({HOST: API, "WRANGLER_SEND_METRICS": "false", "NO_COLOR": "1", "FORCE_COLOR": "0"})
    return run


def dotenv_files(folder, named=(), envs=()) -> list[Path]:
    """The .env files wrangler may load in *folder*: those named with --env-file (relative to the folder), else
    `.env`, `.env.local` and, for any --env NAME, `.env.NAME` and `.env.NAME.local` (every `.env.*` there, and for
    each NAME in *envs* those two as path.resolve reads them: `--env /../x` loads x)."""
    if named:
        return [Path(os.path.abspath(os.path.join(folder, name))) for name in named]
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        names = []
    found = [Path(folder) / name for name in names if name == ".env" or name.startswith(".env.")]
    for env in envs:
        for name in (f".env.{env}", f".env.{env}.local"):
            path = Path(os.path.abspath(os.path.join(folder, name)))
            if path not in found:
                found.append(path)
    return found


def endpoints_set(folder, named=(), envs=()) -> list[tuple[Path, list[str]]]:
    """(file, variable names) for each .env file wrangler may load in *folder* that sets DOTENV_NAMES. Only names are
    read: a value is never kept or shown."""
    found = []
    for path in dotenv_files(folder, named, envs):
        try:
            if not path.is_file():  # not a folder, a pipe or a device: wrangler skips what it cannot read too
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        seen = {m.group(1) for m in map(DOTENV_LINE.match, text.splitlines()) if m}
        names = [name for name in DOTENV_NAMES if name in seen]
        if names:
            found.append((path, names))
    return found


def endpoints_problem(found: list[tuple[Path, list[str]]]) -> tuple[str, str]:
    """(detail, fix) for .env files that set DOTENV_NAMES: the files and the names, never the values."""
    files = "; ".join(f"{printable(tilde(path))} sets {', '.join(names)}" for path, names in found)
    named = [name for name in DOTENV_NAMES if any(name in listed for _, listed in found)]
    where = printable(tilde(found[0][0])) if len(found) == 1 else "those files"
    sign_in, store = any(name in ENDPOINTS for name in named), STORE in named
    decides = " and ".join(text for text, on in (("where wrangler sends the profile's sign-in", sign_in),
                                                 ("where wrangler keeps its profiles", store)) if on)
    keeps = " and ".join(text for text, on in (("Cloudflare's own API host and sign-in", sign_in),
                                               ("the profiles it checks", store)) if on)
    return (f"{files}, which would decide {decides}",
            f"remove {', '.join(named)} from {where}   (Keyfit keeps wrangler on {keeps})")


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
        for var in (*SCRUB, *ENDPOINTS):
            env.pop(var, None)
        env[HOST] = API
        account = conn.get("account_id")
        if isinstance(account, str) and ACCOUNT.fullmatch(account):
            env["CLOUDFLARE_ACCOUNT_ID"] = account
        env[MARK] = conn.get("name", "")

    def forget(self, env: dict) -> None:
        if MARK in env:  # Keyfit set the account (and the API host) for another project
            for var in ("CLOUDFLARE_ACCOUNT_ID", MARK):
                env.pop(var, None)
            if env.get(HOST) == API:
                env.pop(HOST)

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
        found = endpoints_set(os.getcwd())  # first: no sign-in is offered in such a folder
        if found:
            return Check(self.kind, "fail", label, *endpoints_problem(found))
        if not has_profile(env, profile):
            what = "wrangler is not signed in (no default profile)" if profile == DEFAULT else \
                f"wrangler has no profile {profile} on this Mac"
            return Check(self.kind, "fail", label, what, sign_in(profile))
        folder = os.getcwd()
        problem = mismatch(conn, folder, env)
        if problem:
            return Check(self.kind, "fail", label, *problem)
        unknown = None
        here, typed = wrangler_bin(env, folder), find_real("wrangler", env, skip_wrappers=True)  # npx's, PATH's
        for binary in dict.fromkeys(b for b in (here, typed) if b):
            found = version(binary)
            if found is None:
                unknown = unknown or binary
            elif found < MINIMUM:
                local = f"{os.sep}node_modules{os.sep}" in binary
                return Check(self.kind, "fail", label,
                             f"{tilde(binary)} is wrangler {'.'.join(map(str, found))}, which ignores profiles "
                             f"(4.106 or newer needed)",
                             "npm install --save-dev wrangler@latest" if local else "npm install -g wrangler@latest")
        _, where = bound(folder, bindings(env))
        detail = f"bound at {tilde(where)}" if where else "default login"
        if unknown:
            return Check(self.kind, "warn", label, f"{detail} · could not tell which wrangler {tilde(unknown)} is",
                         f"{shell_path(unknown)} --version must be 4.106 or newer (older ones ignore profiles)",
                         pending=True)
        return Check(self.kind, "ok", label, detail, pending=True)

    def remote(self, conn, env: dict) -> Check:
        label = self.label(conn)
        profile, expected = conn["profile"], conn["account_id"]
        title = conn.get("account_name") or expected
        folder = os.getcwd()
        binary = wrangler_bin(env, folder)
        if binary is None:
            return Check(self.kind, "warn", label, "not checked online: no wrangler here (npm install -g wrangler, "
                                                   "or npm install in the project)")
        shell = {k: v for k, v in env.items() if os.environ.get(k) == v or k.startswith("KEYFIT_")}
        self.inject(conn, shell)  # the shell's environment and the account: none of the project's other tokens
        accounts, reason, state = self.whoami(binary, folder, shell)
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
