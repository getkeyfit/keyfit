"""Which project is this folder?

1. Inside a git work tree: the git *common dir* (`git rev-parse --git-common-dir`)
   names the main checkout's `.git`, also from linked worktrees, so every
   worktree of a registered repository resolves to the same project (a worktree
   counts only when the repository lists it back; see git_main_checkout).
2. Otherwise: the nearest registered root that is an ancestor of the folder or of
   its main checkout — so a repository nested inside a project folder resolves to
   that project from its own linked worktrees too (e.g. Superset's).
3. Otherwise: not registered — callers must pass through, never guess.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from .registry import Project, Registry, canonical


@dataclass
class Resolution:
    path: Path
    project: Project | None
    root: Path | None
    how: str

    @property
    def registered(self) -> bool:
        return self.project is not None


def git_main_checkout(path: Path) -> Path | None:
    """Return the main checkout directory for *path*, or None when not in a git work tree.

    A `.git` file or symlink can point any folder at any repository (an unpacked archive can carry one),
    so a work tree counts as a checkout of another folder only as a linked worktree that its repository
    lists back (<git dir>/gitdir names this work tree). Anything else is its own checkout.
    """
    try:
        out = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--path-format=absolute", "--git-common-dir", "--show-toplevel",
             "--git-dir"],
            capture_output=True, text=True, timeout=10, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    lines = out.stdout.splitlines()
    if len(lines) < 3 or not lines[1]:
        return None  # bare repository or inside .git itself
    common, top, own = canonical(lines[0]), canonical(lines[1]), canonical(lines[2])
    if common.name == ".git" and own != common and _lists_worktree(common, own, top):
        return common.parent
    return top


def _lists_worktree(common: Path, own: Path, top: Path) -> bool:
    """True when *own* is a worktree entry of the repository at *common* that points back at *top*."""
    if own.parent != common / "worktrees":
        return False
    try:
        back = (own / "gitdir").read_text().strip()  # absolute, or relative to *own* (worktree.useRelativePaths)
    except OSError:
        return False
    return bool(back) and canonical(own / back) == canonical(top / ".git")


def resolve(path: str | Path = ".", registry: Registry | None = None) -> Resolution:
    reg = registry if registry is not None else Registry.load()
    p = canonical(path)

    main = git_main_checkout(p)
    if main is not None:
        project = reg.project_for_root(main)
        if project is not None:
            return Resolution(p, project, main, f"git common-dir of {p} → {main}")

    best: tuple[Project, Path, Path] | None = None
    for start in (p, main) if main is not None and main != p else (p,):
        for project in reg.projects().values():
            for root in project.canonical_roots():
                if start == root or root in start.parents:
                    if best is None or len(root.parts) > len(best[1].parts):
                        best = (project, root, start)
    if best is not None:
        project, root, start = best
        via = "" if start == p else f" of its main checkout {start}"
        return Resolution(p, project, root, f"nearest registered ancestor {root}{via}")

    if main is not None:
        return Resolution(p, None, None, f"git repository {main} is not registered")
    return Resolution(p, None, None, "not inside a registered project")
