"""Release hygiene (KEY-79): what goes public holds no private name, and the release files stay in step.

The checks on files that only a checkout has (.gitignore, the workflows, RELEASING.md) skip in an unpacked sdist."""

import hashlib
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

os.environ["KEYFIT_CONFIG_DIR"] = tempfile.mkdtemp()  # tests never touch the real ~/.config/keyfit

ROOT = Path(__file__).resolve().parents[1]

# Private project names never go public (CONTRIBUTING.md), not even in a test: an sdist stays on PyPI for good.
# Kept as sha256 digests of two lowercase words joined by a space, so this file does not name them either.
PRIVATE_NAMES = {"2b6565c6155c513405abd9bbe9d9051690c3eab9df476d6c23016ff8c0e946ce"}


def public_files() -> list[Path]:
    """Every file of the repository (git's list), or of the unpacked sdist when there is no git."""
    try:
        listed = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, text=True, check=True,
                                timeout=60).stdout.split("\0")
    except (OSError, subprocess.SubprocessError):
        listed = []
    files = [ROOT / name for name in listed if name] or [path for path in ROOT.rglob("*")
                                                         if "__pycache__" not in path.parts]
    return [path for path in files if path.is_file()]


class PrivateNameTests(unittest.TestCase):
    def test_no_file_names_a_private_project(self):
        files = public_files()
        self.assertIn(ROOT / "keyfit" / "__init__.py", files)
        for path in files:
            words = re.findall(r"[a-z0-9]+", path.read_text(encoding="utf-8", errors="ignore").lower())
            pairs = {hashlib.sha256(f"{a} {b}".encode()).hexdigest() for a, b in zip(words, words[1:])}
            self.assertFalse(pairs & PRIVATE_NAMES, f"{path.relative_to(ROOT)} names a private project")



@unittest.skipUnless((ROOT / ".git").exists(), "needs a git checkout")
class GitignoreTests(unittest.TestCase):
    def test_per_machine_files_are_never_committed(self):
        # direnv's .envrc and its cache, and the .vercel link the keyfit.dev deploy copies into the worktree root
        for name in (".envrc", ".direnv/python-3.13/bin/python", ".vercel/project.json", "site/.vercel/project.json"):
            out = subprocess.run(["git", "check-ignore", "--no-index", "-v", name], cwd=ROOT, capture_output=True,
                                 text=True, timeout=60)
            self.assertEqual(out.stdout.split(":")[0], ".gitignore", f"{name} is not in .gitignore")


@unittest.skipUnless((ROOT / "RELEASING.md").exists(), "RELEASING.md is not in the sdist")
class ReleasingTests(unittest.TestCase):
    def setUp(self):
        self.text = (ROOT / "RELEASING.md").read_text(encoding="utf-8")

    def test_homebrew_takes_the_sha256_pypi_serves(self):
        # two sdist builds of the same commit differ, so a local dist/ never gives PyPI's sha256
        self.assertNotRegex(self.text, r"shasum[^\n]*dist/")
        self.assertIn("https://pypi.org/pypi/keyfit/X.Y.Z/json", self.text)

    def test_first_release_is_public_and_protected_before_the_tag(self):
        self.assertIn("\n## The first release\n", self.text)
        first = self.text.split("\n## The first release\n", 1)[1]
        steps = ("--visibility public", "homebrew-keyfit", "private-vulnerability-reporting", "`pypi` environment",
                 "git tag", "gh release create", "step 7 above")
        where = [first.index(step) for step in steps]
        self.assertEqual(where, sorted(where))

if __name__ == "__main__":
    unittest.main()
