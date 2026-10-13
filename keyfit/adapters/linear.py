"""Linear: one workspace per project, reached with that workspace's API key.

Connection:  linear = { workspace = "acme", key = "env:LINEAR_ACME_API_KEY" }
             key may also be keychain:<service>/<account>; optional readonly = true

inject  — read the project's key, remove every other Linear key variable from
          the environment (the shell carries one per workspace), and expose the
          project's key under one generic name: LINEAR_API_KEY.
local   — key present and well-formed (catches a key pasted twice).
remote  — GraphQL `organization { urlKey }` must equal the expected workspace.
mcp     — one server named plainly `linear`, authenticated via ${LINEAR_API_KEY};
          no secret is written to config files or command arguments.
"""

from __future__ import annotations

import json
import re

from .. import secrets
from ..checks import Check
from .base import Adapter

GRAPHQL = "https://api.linear.app/graphql"
MCP_URL = "https://mcp.linear.app/mcp"
KEY_SHAPE = re.compile(r"^lin_(api|oauth)_[A-Za-z0-9]+$")
QUERY = "{ organization { urlKey name } }"


def is_linear_key_var(name: str) -> bool:
    return "LINEAR" in name and name.endswith("API_KEY")


def _fetch(key: str, timeout: float) -> tuple[int, dict]:
    """POST the identity query; return (http_status, json_body)."""
    import urllib.error  # only for online checks: urllib.request and TLS take ~30 ms to import
    import urllib.request

    from .. import http

    auth = f"Bearer {key}" if key.startswith("lin_oauth_") else key  # personal API keys take no Bearer prefix
    req = urllib.request.Request(GRAPHQL, data=json.dumps({"query": QUERY}).encode(),
                                 headers={"Content-Type": "application/json", "Authorization": auth})
    try:
        with http.urlopen(req, timeout) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as err:
        try:
            body = json.loads(err.read() or b"{}")
        except ValueError:
            body = {}
        return err.code, body


class LinearAdapter(Adapter):
    kind = "linear"

    def __init__(self, fetch=_fetch, timeout: float = 15):
        self.fetch = fetch
        self.timeout = timeout

    def label(self, conn) -> str:
        return conn.get("workspace", "?")

    def inject(self, conn, env: dict) -> None:
        ref = secrets.normalise(conn.get("key", ""))
        value = secrets.resolve(ref, env)
        for name in [n for n in env if is_linear_key_var(n)]:
            del env[name]
        if value:
            env["LINEAR_API_KEY"] = value
            if ref.startswith("env:"):
                env[ref[4:]] = value  # keep the project's own variable: its MCP server (e.g. linear_acme) reads it
        env["KEYFIT_LINEAR_WORKSPACE"] = conn.get("workspace", "")

    def fingerprint(self, conn, env: dict) -> str:
        """Changes when the key changes, so a cached check never vouches for a different key."""
        import hashlib
        return hashlib.sha256(env.get("LINEAR_API_KEY", "").encode()).hexdigest()[:16]

    def local(self, conn, env: dict) -> Check:
        label = self.label(conn)
        ref = conn.get("key", "")
        fix = "keyfit add linear   (paste the key; or --key env:VAR / keychain:service/account)"
        if not conn.get("workspace"):
            return Check(self.kind, "fail", label, "no expected workspace recorded", fix)
        key = env.get("LINEAR_API_KEY")
        if not key:
            return Check(self.kind, "fail", label, f"key not found ({secrets.describe(ref)})", fix)
        half = len(key) // 2
        if len(key) % 2 == 0 and key[:half] == key[half:]:
            return Check(self.kind, "fail", label, f"the key in {secrets.describe(ref)} looks pasted twice", fix)
        if not KEY_SHAPE.fullmatch(key):
            return Check(self.kind, "fail", label, f"{secrets.describe(ref)} does not look like a Linear key", fix)
        return Check(self.kind, "ok", label, "", pending=True)

    def remote(self, conn, env: dict) -> Check:
        label = self.label(conn)
        expected = conn["workspace"]
        try:
            status, body = self.fetch(env["LINEAR_API_KEY"], self.timeout)
        except (OSError, ValueError) as err:
            return Check(self.kind, "fail", label, f"Linear did not answer ({getattr(err, 'reason', None) or type(err).__name__})",
                         "check the network and retry", transient=True)
        if status in (400, 401, 403) or body.get("errors"):
            return Check(self.kind, "fail", label, "Linear rejected the key (revoked, expired or wrong)",
                         "create a key in Linear → Settings → Security & access → Personal API keys, "
                         "then: keyfit add linear")
        if status != 200:  # a redirect (never followed) or a server error: no answer about the key
            return Check(self.kind, "fail", label, f"Linear answered {status}", "retry, or: keyfit status")
        org = ((body.get("data") or {}).get("organization") or {})
        actual = org.get("urlKey")
        if actual != expected:
            return Check(self.kind, "fail", label,
                         f"the key belongs to workspace '{actual}', project expects '{expected}'",
                         f"keyfit add linear --workspace {expected}   (paste the key for {expected})")
        return Check(self.kind, "ok", label, "workspace confirmed")

    def workspace_of(self, key: str) -> tuple[str | None, str]:
        """Ask Linear which workspace a key belongs to (used when recording a mapping). Only a value shaped like a
        Linear key is sent: `--key env:VAR` or an .envrc line can name any variable, such as a GitHub token."""
        if not KEY_SHAPE.fullmatch(key or ""):
            return None, "that is not a Linear API key (lin_api_…), so it was not sent to Linear"
        try:
            status, body = self.fetch(key, self.timeout)
        except (OSError, ValueError) as err:
            return None, f"Linear did not answer ({type(err).__name__})"
        org = ((body.get("data") or {}).get("organization") or {})
        if status != 200 or not org.get("urlKey"):
            return None, "Linear rejected the key"
        return org["urlKey"], ""

    def mcp_servers(self, conn) -> dict:
        url = MCP_URL + ("/readonly" if conn.get("readonly") else "")
        return {
            "claude": {"linear": {"type": "http", "url": url, "headers": {"Authorization": "Bearer ${LINEAR_API_KEY}"}}},
            "codex": {"linear": {"url": url, "bearer_token_env_var": "LINEAR_API_KEY"}},
        }


def parse_envrc(text: str, env: dict) -> str | None:
    """Find `export LINEAR_API_KEY=$NAME` in an .envrc and return a key reference for it."""
    for line in text.splitlines():
        m = re.match(r"^\s*export\s+LINEAR_API_KEY=\"?\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?\"?\s*$", line)
        if not m:
            continue
        name = m.group(1)
        if env.get(name):
            return f"env:{name}"
        # the variable may have been renamed from NAME_LINEAR_API_KEY to LINEAR_NAME_API_KEY; follow the rename
        legacy = re.match(r"^([A-Z0-9]+)_LINEAR_API_KEY$", name)
        if legacy and env.get(f"LINEAR_{legacy.group(1)}_API_KEY"):
            return f"env:LINEAR_{legacy.group(1)}_API_KEY"
        return f"env:{name}"
    return None
