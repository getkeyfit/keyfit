"""Which project is this folder?

1. Inside a git work tree: the git *common dir* (`git rev-parse --git-common-dir`)
   names the main checkout's `.git`, also from linked worktrees, so every
   worktree of a registered repository resolves to the same project (a worktree
   counts only when the repository lists it back; see git_main_checkout). When
   that checkout is not registered, the checkout around it is asked the same, so
   a submodule or a nested repository belongs to the checkout it sits in, also in
   a linked worktree.
2. Otherwise: the nearest registered root that is an ancestor of the folder or of
   its main checkout — so a repository nested inside a project folder resolves to
   that project from its own linked worktrees too (e.g. Superset's).
3. Otherwise: not registered — callers must pass through, never guess.

When git fails where a `.git` exists at or above the folder, the folder may belong
to a project, so resolving raises GitError instead of answering "not registered".
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .registry import Project, Registry, canonical, tilde

# What a git hook exports (and a tool it runs inherits): they point git at that repository, or stop its search early,
# whatever folder it is asked about.
GIT_SCRUB = ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE", "GIT_CEILING_DIRECTORIES")


class GitError(Exception):
    """git failed in a folder inside a repository: which project the folder belongs to is unknown."""

    def __init__(self, path: Path, reason: str):
        super().__init__(f"git failed in {tilde(path)} ({reason})")
        self.path, self.reason = path, reason


def in_repository(path: Path) -> bool:
    """True when a `.git` exists at or above *path*: git looks for a repository there. A few stat calls, no git."""
    return any(os.path.lexists(folder / ".git") for folder in (path, *path.parents))


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

    Raises GitError when git finds no repository (git missing or too slow, the Command Line Tools broken by a macOS
    update, Xcode's licence not accepted, dubious ownership, a .git file naming a folder that is gone) while a `.git`
    exists at or above *path*. Without one, no repository can hold *path*, so a failing git costs nothing there.
    """
    env = {name: value for name, value in os.environ.items() if name not in GIT_SCRUB}
    out, reason = None, ""
    try:
        out = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--path-format=absolute", "--git-common-dir", "--show-toplevel",
             "--git-dir"],
            capture_output=True, text=True, timeout=10, check=False, env=env,
        )
    except subprocess.TimeoutExpired:
        reason = "no answer within 10 seconds"
    except (OSError, subprocess.SubprocessError) as err:
        reason = "git is not installed" if isinstance(err, FileNotFoundError) else \
            getattr(err, "strerror", None) or type(err).__name__
    if out is not None and out.returncode != 0 and not out.stdout.strip():  # it printed nothing: no repository found
        reason = next((line.strip() for line in out.stderr.splitlines() if line.strip()), f"exit {out.returncode}")
    if reason:
        if in_repository(path):
            raise GitError(path, reason)
        return None
    lines = out.stdout.splitlines()
    if out.returncode != 0 or len(lines) < 3 or not lines[1]:
        return None  # bare repository or inside .git itself (git found it, then said: no work tree here)
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

    checkouts: list[Path] = []
    main = git_main_checkout(p)
    while main is not None and main not in checkouts:
        project = reg.project_for_root(main)
        if project is not None:
            how = f"nested git repository {checkouts[-1]} → main checkout {main}" if checkouts else \
                f"git common-dir of {p} → {main}"
            return Resolution(p, project, main, how)
        checkouts.append(main)
        # a submodule or a nested repository: the checkout it sits in (git runs again only when there can be one)
        main = git_main_checkout(main.parent) if in_repository(main.parent) else None

    best: tuple[Project, Path, Path] | None = None
    for start in dict.fromkeys((p, *checkouts)):
        for project in reg.projects().values():
            for root in project.canonical_roots():
                if start == root or root in start.parents:
                    if best is None or len(root.parts) > len(best[1].parts):
                        best = (project, root, start)
    if best is not None:
        project, root, start = best
        via = "" if start == p else f" of its main checkout {start}"
        return Resolution(p, project, root, f"nearest registered ancestor {root}{via}")

    if checkouts:
        return Resolution(p, None, None, f"git repository {checkouts[0]} is not registered")
    return Resolution(p, None, None, "not inside a registered project")
