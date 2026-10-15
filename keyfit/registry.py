"""The registry: one TOML file in the user's config dir, nothing in repos.

    [projects.acme]
    roots = ["~/git/acme"]
    aws = { profile = "acme", account = "123456789012" }
    github = "octocat"
    google = "work"

    [projects.globex]
    roots = ["~/git/globex"]
    mcp_servers = ["globex_cloud_*"]

    [accounts.github.octocat]
    login = "octocat"

    [accounts.google.work]
    email = "me@acme.com"

    [settings]
    shared_aws_profiles = ["ci-worker"]

    [secrets]
    keychain = ["linear-acme"]

Projects are keyed by name; `roots` are main-checkout paths (worktrees resolve
to them through git); `mcp_servers` are name patterns of MCP servers that
belong to the project and stay hidden in every other project. Everything else
on a project is a connection, kept as plain data here and interpreted by the
adapters. `[settings]` holds machine-wide options: `shared_aws_profiles` are
AWS profiles of tools that serve every project (`aws --profile` with only
those runs unchanged in any folder). `[secrets]` names the Keychain items Keyfit
stored itself (service `keyfit`) — names only, never values.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from . import tomlwrite

# Connection kinds earlier versions supported; old registries may still carry them. They are ignored:
# never injected or checked, only mentioned by `keyfit status` so they can be removed from registry.toml.
LEGACY_KINDS = ("workos", "bitwarden")
# Connections checked against the project's one Google account (`google = "work"`, its email): each gets the email as
# `account`, None while the project has none.
GOOGLE_TOOLS = ("gcloud", "firebase", "gemini")


class RegistryError(ValueError):
    """registry.toml cannot be used as it is: unreadable, not TOML, or not shaped like a registry (a hand edit).
    The message says where; `fix` is the next step. Callers print both instead of a traceback."""

    def __init__(self, path: Path, reason: str, fix: str = ""):
        super().__init__(reason)
        self.path = path
        self.fix = fix or f"fix it in {tilde(path)}"


def _check_shape(data: dict, path: Path) -> None:
    """Refuse a registry every project would misread: a hand edit must stop with its place named, never crash or
    (roots = "~/code/acme" read as the letters "~", "/", …) quietly make the home folder a project. A connection
    Keyfit cannot use fails only its own project's check (environment.unusable)."""
    def bad(reason: str, fix: str = ""):
        raise RegistryError(path, reason, fix)

    for table in ("projects", "accounts", "settings", "secrets"):
        if not isinstance(data.get(table, {}), dict):
            bad(f"`{table}` must be a table, like [{table}.…]")
    for kind, named in data.get("accounts", {}).items():
        if not isinstance(named, dict) or not all(isinstance(a, dict) for a in named.values()):
            bad(f"[accounts.{kind}] must hold one table per account, like [accounts.{kind}.NAME]")
    home = Path.home().resolve()
    for name, body in data.get("projects", {}).items():
        where = f"[projects.{name}]"
        if not isinstance(body, dict):
            bad(f"{where} must be a table")
        roots = body.get("roots")
        if not isinstance(roots, list) or not roots or not all(isinstance(r, str) and r.strip() for r in roots):
            bad(f'{where}: roots must be a list of folders, like roots = ["~/code/{name}"]')
        for root in roots:
            if canonical(root) == home or canonical(root) in home.parents:
                bad(f'{where}: roots holds "{root}", which would put every folder under it in {name}',
                    f"remove that root from {tilde(path)}; register repositories with: keyfit init")
        servers = body.get("mcp_servers", [])
        if not isinstance(servers, list) or not all(isinstance(s, str) for s in servers):
            bad(f'{where}: mcp_servers must be a list of names, like mcp_servers = ["{name}_*"]')


def config_dir() -> Path:
    return Path(os.environ.get("KEYFIT_CONFIG_DIR", "~/.config/keyfit")).expanduser()


def default_path() -> Path:
    return config_dir() / "registry.toml"


def write_private(path: Path, text: str) -> None:
    """Write a file only its owner can read, from the first byte: writing first and chmod-ing after leaves it
    readable by other users of the Mac in between (or for good, should the chmod never run)."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        os.fchmod(fd, 0o600)  # a file that already existed keeps its mode through O_CREAT
        fh.write(text)


def canonical(path: str | Path) -> Path:
    """Expand ~, make absolute, resolve symlinks — the one form paths are compared in."""
    return Path(path).expanduser().resolve()


@dataclass
class Project:
    name: str
    roots: list[str] = field(default_factory=list)
    connections: dict = field(default_factory=dict)
    mcp_servers: list[str] = field(default_factory=list)
    legacy: list[str] = field(default_factory=list)  # LEGACY_KINDS present in the registry entry

    def canonical_roots(self) -> list[Path]:
        return [canonical(r) for r in self.roots]

    def to_toml(self) -> dict:
        extra = {"mcp_servers": list(self.mcp_servers)} if self.mcp_servers else {}
        return {"roots": list(self.roots), **extra, **self.connections}


class Registry:
    def __init__(self, path: Path | None = None, data: dict | None = None):
        self.path = Path(path) if path else default_path()
        self.data = data if data is not None else {"projects": {}, "accounts": {}}
        self.data.setdefault("projects", {})
        self.data.setdefault("accounts", {})

    @classmethod
    def load(cls, path: Path | None = None) -> "Registry":
        p = Path(path) if path else default_path()
        if not p.exists():
            return cls(p)
        try:
            with p.open("rb") as fh:
                data = tomllib.load(fh)
        except OSError as err:
            raise RegistryError(p, f"cannot read it ({err.strerror})", f"chmod 600 {tilde(p)}") from None
        except ValueError as err:  # TOMLDecodeError names the line and column; also text that is not UTF-8
            raise RegistryError(p, str(err), f"fix that line in {tilde(p)}") from None
        _check_shape(data, p)
        return cls(p, data)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".toml.tmp")
        write_private(tmp, tomlwrite.dumps(self.data))
        os.replace(tmp, self.path)

    # projects ---------------------------------------------------------------

    def projects(self) -> dict[str, Project]:
        """Projects with account references expanded: `claude = "work"` → the [accounts.claude.work] table. Google tools
        (GOOGLE_TOOLS) also get the email of the project's Google account."""
        out = {}
        for name, body in self.data["projects"].items():
            body = dict(body)
            roots = body.pop("roots", [])
            servers = body.pop("mcp_servers", [])
            legacy = [kind for kind in LEGACY_KINDS if body.pop(kind, None) is not None]
            for kind, value in list(body.items()):
                account = self.data["accounts"].get(kind, {}).get(value) if isinstance(value, str) else None
                if account is not None:
                    body[kind] = {**account, "name": value}
            google = body.get("google")
            for kind in GOOGLE_TOOLS:  # said once per project, checked by every Google tool
                if isinstance(body.get(kind), dict):
                    body[kind] = {**body[kind], "account": google.get("email") if isinstance(google, dict) else None}
            out[name] = Project(name, list(roots), body, list(servers), legacy)
        return out

    def mcp_owners(self) -> dict[str, list[str]]:
        """Project name → the MCP server name patterns it owns."""
        return {name: p.mcp_servers for name, p in self.projects().items() if p.mcp_servers}

    def shared_aws_profiles(self) -> set[str]:
        """AWS profiles that run unchanged in every folder ([settings] shared_aws_profiles, default none)."""
        profiles = self.data.get("settings", {}).get("shared_aws_profiles", [])
        return set(profiles) if isinstance(profiles, list) else set()

    def set_account(self, kind: str, name: str, data: dict) -> None:
        self.data["accounts"].setdefault(kind, {})[name] = data

    def get(self, name: str) -> Project | None:
        return self.projects().get(name)

    def add_project(self, name: str, root: str | Path) -> Project:
        root_str = tilde(root)
        existing = self.data["projects"].get(name)
        if existing is None:
            self.data["projects"][name] = {"roots": [root_str]}
        elif root_str not in existing.get("roots", []):
            existing.setdefault("roots", []).append(root_str)
        return self.get(name)

    def set_connection(self, name: str, kind: str, value) -> None:
        self.data["projects"][name][kind] = value

    def add_mcp_servers(self, name: str, patterns: list[str]) -> list[str]:
        owned = self.data["projects"][name].setdefault("mcp_servers", [])
        owned += [p for p in patterns if p not in owned]
        return list(owned)

    def project_for_root(self, root: Path) -> Project | None:
        root = canonical(root)
        for p in self.projects().values():
            if root in p.canonical_roots():
                return p
        return None

    # keys Keyfit stored itself ----------------------------------------------

    def stored_secrets(self) -> list[str]:
        """Account names of the Keychain items Keyfit wrote (`[secrets] keychain = [...]`) — names only."""
        return list(self.data.get("secrets", {}).get("keychain", []))

    def add_stored_secret(self, name: str) -> None:
        names = self.data.setdefault("secrets", {}).setdefault("keychain", [])
        if name not in names:
            names.append(name)


def tilde(path: str | Path) -> str:
    """Store paths under the home directory as ~/… so the registry is portable across machines."""
    p = canonical(path)
    home = Path.home().resolve()
    try:
        rel = p.relative_to(home)
    except ValueError:
        return str(p)
    return "~" if rel == Path(".") else f"~/{rel}"
