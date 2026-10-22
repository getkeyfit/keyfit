"""Build the website's docs pages from the repository's README and docs/, so both always say the same.

    python3 site/scripts/sync_docs.py          # write site/src/content/docs/docs/*.md
    python3 site/scripts/sync_docs.py --check  # exit 1 if they are out of date (CI)

Run from anywhere; paths are relative to this file. Standard library only.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "site" / "src" / "content" / "docs" / "docs"
REPO = "https://github.com/FlamurMaliqi/keyfit/blob/main"

# page slug → (title, description, sidebar order, [(source file, [README sections] or None for the whole file)])
PAGES = {
    "quickstart": ("Quickstart", "Install Keyfit and register your first project in five minutes.", 1,
                   [("README.md", ["Install", "Quickstart (5 minutes)"])]),
    "how-it-works": ("How it works", "How Keyfit resolves the project, checks its accounts and starts the tool.", 2,
                     [("README.md", ["How it works", "What is checked"])]),
    "commands": ("Commands", "Everyday Keyfit commands.", 3, [("README.md", ["Everyday commands"])]),
    "limits": ("Limits and turning it off", "What Keyfit does not cover, and how to skip or remove it.", 4,
               [("README.md", ["Limits", "Turning it off"])]),
    "security": ("Security", "Everything Keyfit reads, stores and sends.", 5, [("docs/security.md", None)]),
    "troubleshooting": ("Troubleshooting", "When a tool doesn't go through Keyfit, or a check fails.", 6,
                        [("docs/troubleshooting.md", None)]),
}

# links in the sources → links on the site
LINKS = {
    f"{REPO}/docs/security.md": "/docs/security/",
    f"{REPO}/docs/troubleshooting.md": "/docs/troubleshooting/",
    "docs/security.md": "/docs/security/",  # README's relative links
    "docs/troubleshooting.md": "/docs/troubleshooting/",
    "../SECURITY.md": f"{REPO}/SECURITY.md",
    "security.md": "/docs/security/",
    "troubleshooting.md": "/docs/troubleshooting/",
}


def sections(text: str) -> dict[str, str]:
    """README split at its `## ` headings: heading → body (the heading line itself excluded)."""
    out, name, lines = {}, None, []
    for line in text.splitlines():
        if line.startswith("## "):
            if name:
                out[name] = "\n".join(lines).strip("\n")
            name, lines = line[3:].strip(), []
        elif name:
            lines.append(line)
    if name:
        out[name] = "\n".join(lines).strip("\n")
    return out


def without_title(text: str) -> str:
    """Drop a leading `# Title` line: the page title comes from the frontmatter."""
    lines = text.splitlines()
    while lines and not lines[0].strip():
        lines.pop(0)
    if lines and lines[0].startswith("# "):
        lines.pop(0)
    return "\n".join(lines).strip("\n")


def url(target: str, source: str) -> str:
    """A link target in a source → the target on the site."""
    anchor = ""
    if "#" in target and not target.startswith("#"):
        target, anchor = target.split("#", 1)
        anchor = "#" + anchor
    if target in LINKS:
        return LINKS[target] + anchor
    if not target.startswith(("http", "#", "/", "mailto:")):  # another repo file: link it on GitHub
        return f"{REPO}/{(Path(source).parent / target).as_posix().lstrip('./')}{anchor}"
    return target + anchor


def relink(text: str, source: str) -> str:
    return re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", lambda m: f"[{m.group(1)}]({url(m.group(2), source)})", text)


DEFINITION = re.compile(r"^\[([^\]]+)\]:[ \t]+(\S+)", re.M)  # a reference-style link's `[label]: target`


def render(slug: str) -> str:
    title, description, order, parts = PAGES[slug]
    chunks, defined = [], {}
    for source, wanted in parts:
        text = (ROOT / source).read_text()
        for match in DEFINITION.finditer(text):
            defined.setdefault(match.group(1).lower(), url(match.group(2), source))
        if wanted is None:
            chunks.append(relink(without_title(text), source))
            continue
        found = sections(text)
        for name in wanted:
            if name not in found:
                raise SystemExit(f"sync_docs: section '## {name}' not found in {source}")
            heading = re.sub(r"\s*\(.*\)$", "", name)  # "Quickstart (5 minutes)" → "Quickstart"
            body = found[name] if len(wanted) == 1 else f"## {heading}\n\n{found[name]}"
            chunks.append(relink(body, source))
    page = "\n\n".join(chunks).strip()
    # README defines its `[text][label]` links at its end, outside the sections a page takes: bring them along
    own = {match.group(1).lower() for match in DEFINITION.finditer(page)}
    used = dict.fromkeys(label.lower() for label in re.findall(r"\]\[([^\]]+)\]", page))
    brought = [f"[{label}]: {defined[label]}" for label in used if label in defined and label not in own]
    if brought:
        page += "\n\n" + "\n".join(brought)
    sources = ", ".join(sorted({source for source, _ in parts}))
    return (f"---\ntitle: {title}\ndescription: {description}\nsidebar:\n  order: {order}\n---\n\n"
            f"<!-- Generated from {sources} by site/scripts/sync_docs.py. Edit the source, not this file. -->\n\n"
            + page + "\n")


def main(argv: list[str]) -> int:
    check = "--check" in argv
    stale = []
    for slug in PAGES:
        path = OUT / f"{slug}.md"
        want = render(slug)
        if check:
            if not path.exists() or path.read_text() != want:
                stale.append(path.relative_to(ROOT))
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(want)
    if check and stale:
        print("Website docs are out of date; run: python3 site/scripts/sync_docs.py", file=sys.stderr)
        for path in stale:
            print(f"  {path}", file=sys.stderr)
        return 1
    if not check:
        print(f"wrote {len(PAGES)} pages to {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
