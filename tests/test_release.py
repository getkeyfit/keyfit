"""Release hygiene (KEY-79): what goes public holds no private name, and the release files stay in step.

The checks on files that only a checkout has (.gitignore, the workflows, RELEASING.md) skip in an unpacked sdist."""

import hashlib
import os
import re
import subprocess
import tempfile
import tomllib
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


@unittest.skipUnless((ROOT / ".github" / "workflows").is_dir(), "the workflows are not in the sdist")
class WorkflowTests(unittest.TestCase):
    def workflow(self, name: str) -> str:
        return (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")

    def assertBefore(self, text: str, first: str, then: str):
        self.assertIn(first, text)
        self.assertIn(then, text)
        self.assertLess(text.index(first), text.index(then), f"{first!r} must come before {then!r}")

    def test_installed_wheel_tests_never_skip(self):
        # tests/test_install.py skips itself without setuptools 77+, which setup-python's 3.12+ do not have
        for name in ("ci.yml", "release.yaml"):
            with self.subTest(name):
                text = self.workflow(name)
                self.assertBefore(text, 'pip install "setuptools>=77"', "sys.exit(test_install.CANNOT_BUILD or None)")
                self.assertBefore(text, "sys.exit(test_install.CANNOT_BUILD or None)", "unittest discover -s tests")

    def test_release_runs_the_built_wheel_before_publishing(self):
        text = self.workflow("release.yaml")
        self.assertBefore(text, "python -m build", "pip install --no-index")
        step = text[text.index("python -m build"):text.index("actions/upload-artifact")]
        for command in ('pip install --no-index "$PWD"/dist/*.whl', '"$venv/bin/keyfit" --version',
                        'HOME="$(mktemp -d)" KEYFIT_CONFIG_DIR="$(mktemp -d)" "$venv/bin/keyfit" setup'):
            self.assertIn(command, step)
        self.assertIn("needs: build", text)  # nothing publishes when the wheel does not run

    def test_ci_tests_every_python_the_classifiers_list(self):
        matrix = re.search(r"python-version: \[([^]]*)\]", self.workflow("ci.yml")).group(1)
        tested = re.findall(r'"(3\.\d+)"', matrix)
        project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
        listed = [c.rsplit(" :: ", 1)[1] for c in project["classifiers"]
                  if re.fullmatch(r"Programming Language :: Python :: 3\.\d+", c)]
        self.assertEqual(tested, listed)
        self.assertEqual(project["requires-python"], f">={tested[0]}")
        self.assertIn("3.14", tested)

if __name__ == "__main__":
    unittest.main()
