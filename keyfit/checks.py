"""The result of checking one connection, and how it is printed."""

from __future__ import annotations

from dataclasses import dataclass

_RANK = {"ok": 0, "warn": 1, "fail": 2}
_SYMBOL = {"ok": "✔", "warn": "!", "fail": "✘"}


@dataclass
class Check:
    kind: str
    status: str  # ok | warn | fail
    label: str
    detail: str = ""
    fix: str = ""
    pending: bool = False  # the local verdict depends on a remote check
    transient: bool = False  # failed because the network did not answer — retry, or trust a recent success

    @property
    def failed(self) -> bool:
        return self.status == "fail"

    def merge(self, remote: "Check") -> "Check":
        """Combine a local check with the remote check that settles it."""
        if remote.failed:
            return remote
        status = max(self.status, remote.status, key=_RANK.__getitem__)
        detail = " · ".join(d for d in (self.detail, remote.detail) if d)
        return Check(self.kind, status, self.label, detail, self.fix or remote.fix)

    def lines(self) -> list[str]:
        head = f"  {_SYMBOL[self.status]} {self.kind:<9} {self.label}"
        if self.detail:
            head += f"   {self.detail}"
        out = [head]
        if self.fix and self.status != "ok":
            out.append(f"    → {self.fix}")
        return out


def mask_account(account: str | None) -> str:
    """Show only the last four digits of an AWS account ID."""
    if not account:
        return "?"
    return "…" + account[-4:]


def human(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return "<1m"
    hours, rest = divmod(seconds, 3600)
    minutes = rest // 60
    if hours >= 48:
        return f"{hours // 24}d"
    return f"{hours}h {minutes}m" if hours else f"{minutes}m"
