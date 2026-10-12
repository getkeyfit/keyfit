"""The registry: one TOML file in the user's config dir, nothing in repos.

    [projects.acme]
    roots = ["~/git/acme"]
    aws = { profile = "acme", account = "123456789012" }
    github = "octocat"

    [projects.globex]
    roots = ["~/git/globex"]
    mcp_servers = ["globex_cloud_*"]

    [accounts.github.octocat]
    login = "octocat"

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


def config_dir() -> Path:
    return Path(os.environ.get("KEYFIT_CONFIG_DIR", "~/.config/keyfit")).expanduser()


def default_path() -> Path:
    return config_dir() / "registry.toml"


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
        with p.open("rb") as fh:
            data = tomllib.load(fh)
        return cls(p, data)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".toml.tmp")
        tmp.write_text(tomlwrite.dumps(self.data))
        tmp.chmod(0o600)
        os.replace(tmp, self.path)

    # projects ---------------------------------------------------------------

    def projects(self) -> dict[str, Project]:
        """Projects with account references expanded: `claude = "work"` → the [accounts.claude.work] table."""
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
        return "~/" + str(p.relative_to(home))
    except ValueError:
        return str(p)
