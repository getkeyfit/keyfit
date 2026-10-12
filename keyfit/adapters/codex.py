"""Codex: the ChatGPT login a project's Codex sessions run on.

Account:  [accounts.codex.pro]  codex_home = "~/.codex"   plan = "pro"
          optional: account_id = "…", pin_account = true (require exactly this ChatGPT account)
Project:  codex = "pro"

inject  — remove CODEX_API_KEY (it overrides the stored login in exec/review and bills the API)
          and point CODEX_HOME at the account's home.
local   — read <home>/auth.json: a ChatGPT login (subscription), not an API key; never both
          (a stored API key next to the login can silently switch billing); the account is the
          pinned one when pinning is on. Identity comes from the id_token claims; nothing is printed.
          Pools such as codex-multi-auth switch between accounts, so pinning is opt-in.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

from ..checks import Check
from ..registry import canonical
from .base import Adapter

SCRUB = ("CODEX_API_KEY",)


def home(conn, env: dict) -> Path:
    raw = conn.get("codex_home") or str(Path(env.get("HOME") or Path.home()) / ".codex")
    return canonical(raw)


def read_login(folder: Path) -> tuple[dict | None, str]:
    """Return {mode, plan, account_id, api_key, tokens} from auth.json — labels only, no token values."""
    try:
        data = json.loads((folder / "auth.json").read_text())
    except FileNotFoundError:
        return None, "not signed in"
    except (OSError, ValueError):
        return None, "auth.json is unreadable"
    tokens = data.get("tokens") or {}
    claims = {}
    id_token = tokens.get("id_token")
    if isinstance(id_token, dict):
        claims = id_token
    elif isinstance(id_token, str) and id_token.count(".") >= 2:
        part = id_token.split(".")[1]
        try:
            claims = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
        except ValueError:
            claims = {}
    auth = claims.get("https://api.openai.com/auth") or {}
    return {"mode": data.get("auth_mode") or ("apikey" if data.get("OPENAI_API_KEY") else None),
            "plan": auth.get("chatgpt_plan_type"), "account_id": auth.get("chatgpt_account_id"),
            "api_key": bool(data.get("OPENAI_API_KEY")), "tokens": bool(tokens)}, ""


class CodexAdapter(Adapter):
    kind = "codex"

    def label(self, conn) -> str:
        plan = conn.get("plan")
        return f"ChatGPT {plan}" if plan else conn.get("name", "?")

    def inject(self, conn, env: dict) -> None:
        for var in SCRUB:
            env.pop(var, None)
        env["CODEX_HOME"] = str(home(conn, env))

    def local(self, conn, env: dict) -> Check:
        label = self.label(conn)
        folder = home(conn, env)
        login_fix = "codex login" if folder == canonical(Path(env.get("HOME") or Path.home()) / ".codex") \
            else f"CODEX_HOME={folder} codex login"
        login, reason = read_login(folder)
        if login is None:
            return Check(self.kind, "fail", label, reason, login_fix)
        if login["api_key"] and login["tokens"]:
            return Check(self.kind, "fail", label,
                         "auth.json holds an API key next to the ChatGPT login — Codex may bill the API",
                         f"codex logout && {login_fix}")
        if login["mode"] != "chatgpt":
            return Check(self.kind, "fail", label,
                         "Codex is signed in with an API key (API billing), not your ChatGPT subscription",
                         f"codex logout && {login_fix}")
        if conn.get("pin_account") and login["account_id"] != conn.get("account_id"):
            return Check(self.kind, "fail", label, "signed in to a different ChatGPT account than the pinned one",
                         login_fix)
        return Check(self.kind, "ok", label, "subscription login, no API key")

    def remote(self, conn, env: dict) -> Check:
        return Check(self.kind, "ok", self.label(conn), "")  # identity is read locally from auth.json
