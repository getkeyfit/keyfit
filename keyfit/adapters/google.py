"""Google: the one Google account a project uses — the work or the personal email.

Account:  [accounts.google.work]  email = "me@acme.com"
Project:  google = "work"

A setting more than a login: Keyfit keeps no Google token. Every Google tool checks its own sign-in against this
email — gcloud (its configuration's account) and the firebase CLI (the sign-in it uses in the folder) now, Gemini
CLI later — so a work folder never runs on the personal Google login or the other way round. The registry hands the email to those connections (as
`account`, see registry.GOOGLE_TOOLS); this adapter only says which account it is.

inject  — nothing: each tool's adapter sets its own variables.
local   — the email is an email address.
remote  — nothing to ask: each tool's own check confirms its sign-in is this account.
"""

from __future__ import annotations

import re

from ..checks import Check
from .base import Adapter

EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s.]+")  # use fullmatch
CONSUMER = ("gmail.com", "googlemail.com")  # a personal Google account; anything else is a company's (Workspace)


def email_of(conn) -> str | None:
    """The account's email, when it is one."""
    email = conn.get("email") if isinstance(conn, dict) else None
    return email if isinstance(email, str) and EMAIL.fullmatch(email) else None


def same(a: str | None, b: str | None) -> bool:
    """Google treats addresses without regard to case."""
    return bool(a) and bool(b) and a.lower() == b.lower()


def default_name(email: str) -> str:
    """The account's name when none is given: personal for a Gmail address, else the company's domain (acme)."""
    domain = email.rsplit("@", 1)[-1].lower()
    if domain in CONSUMER:
        return "personal"
    return re.sub(r"[^a-z0-9]+", "-", domain.rsplit(".", 1)[0]).strip("-")[:40] or "work"


class GoogleAdapter(Adapter):
    kind = "google"

    def label(self, conn) -> str:
        return str(conn.get("email") or "?")

    def inject(self, conn, env: dict) -> None:
        pass

    def local(self, conn, env: dict) -> Check:
        if email_of(conn) is None:
            return Check(self.kind, "fail", self.label(conn), "not an email address",
                         "keyfit add google --email <the project's Google account>")
        return Check(self.kind, "ok", self.label(conn), "")

    def remote(self, conn, env: dict) -> Check:
        return Check(self.kind, "ok", self.label(conn), "")
