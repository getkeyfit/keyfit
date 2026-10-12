"""What every connection type implements.

inject  — change the environment the tool will run with (never print values)
local   — fast checks without network; may leave the verdict pending
remote  — the definitive check against the provider (cached later, KEY-10)
"""

from __future__ import annotations

from ..checks import Check


class Adapter:
    kind = ""

    def label(self, conn) -> str:
        return str(conn)

    def inject(self, conn, env: dict) -> None:
        raise NotImplementedError

    def local(self, conn, env: dict) -> Check:
        raise NotImplementedError

    def remote(self, conn, env: dict) -> Check:
        raise NotImplementedError

    def fingerprint(self, conn, env: dict) -> str:
        """Cheap local signal that the identity behind a connection changed (busts cached checks)."""
        return ""

    def verify(self, conn, env: dict, remote: bool = True) -> Check:
        local = self.local(conn, env)
        if local.failed:
            return local
        if not remote:
            if local.pending:
                local.status = "warn"
                local.detail = (local.detail + " · not checked online").strip(" ·")
            return local
        return local.merge(self.remote(conn, env))
