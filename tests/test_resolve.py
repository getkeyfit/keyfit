import contextlib
import io
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from keyfit import environment
from keyfit.registry import LEGACY_KINDS, Registry
from keyfit.resolve import GitError, resolve
from keyfit.cli import main

os.environ["KEYFIT_CONFIG_DIR"] = tempfile.mkdtemp()  # tests never touch the real ~/.config/keyfit


def git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@x"})


class ResolveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        git("init", "-q", "-b", "main", cwd=self.repo)
        (self.repo / "README").write_text("x")
        git("add", ".", cwd=self.repo)
        git("commit", "-q", "-m", "init", cwd=self.repo)
        self.worktree = self.tmp / "worktrees" / "feature"
        git("worktree", "add", "-q", str(self.worktree), "-b", "feature", cwd=self.repo)
        self.registry = Registry(self.tmp / "registry.toml")
        self.registry.add_project("demo", self.repo)
        self.registry.save()

    def test_main_checkout_resolves(self):
        r = resolve(self.repo, self.registry)
        self.assertEqual(r.project.name, "demo")
        self.assertEqual(r.root, self.repo)

    def test_subdirectory_resolves(self):
        sub = self.repo / "a" / "b"
        sub.mkdir(parents=True)
        self.assertEqual(resolve(sub, self.registry).project.name, "demo")

    def test_linked_worktree_resolves_to_same_project(self):
        r = resolve(self.worktree, self.registry)
        self.assertEqual(r.project.name, "demo")
        self.assertEqual(r.root, self.repo)
        self.assertIn("common-dir", r.how)

    def test_worktree_subdirectory_resolves(self):
        sub = self.worktree / "src"
        sub.mkdir()
        self.assertEqual(resolve(sub, self.registry).project.name, "demo")

    def test_unregistered_repo_is_not_guessed(self):
        other = self.tmp / "other"
        other.mkdir()
        git("init", "-q", cwd=other)
        r = resolve(other, self.registry)
        self.assertIsNone(r.project)
        self.assertIn("not registered", r.how)

    def test_plain_folder_is_not_registered(self):
        plain = self.tmp / "plain"
        plain.mkdir()
        r = resolve(plain, self.registry)
        self.assertIsNone(r.project)

    def test_non_git_folder_under_registered_root_uses_ancestor(self):
        self.registry.add_project("docs", self.tmp / "docs")
        self.registry.save()
        deep = self.tmp / "docs" / "notes" / "2026"
        deep.mkdir(parents=True)
        r = resolve(deep, self.registry)
        self.assertEqual(r.project.name, "docs")
        self.assertIn("ancestor", r.how)

    def test_worktree_of_nested_repo_resolves_through_its_main_checkout(self):
        # a separate repository inside the registered root, with a linked worktree elsewhere (Superset style)
        nested = self.repo / "platform"
        nested.mkdir()
        git("init", "-q", "-b", "main", cwd=nested)
        (nested / "README").write_text("x")
        git("add", ".", cwd=nested)
        git("commit", "-q", "-m", "init", cwd=nested)
        elsewhere = self.tmp / "superset" / "platform-feature"
        git("worktree", "add", "-q", str(elsewhere), "-b", "feature", cwd=nested)
        r = resolve(elsewhere, self.registry)
        self.assertEqual(r.project.name, "demo")
        self.assertIn("main checkout", r.how)

    def test_symlinked_path_resolves(self):
        link = self.tmp / "link"
        link.symlink_to(self.repo)
        self.assertEqual(resolve(link, self.registry).project.name, "demo")

    def test_registry_round_trip_keeps_connections(self):
        self.registry.set_connection("demo", "aws", {"profile": "p", "account": "123456789012"})
        self.registry.set_connection("demo", "github", "octocat")
        self.registry.save()
        again = Registry.load(self.registry.path)
        demo = again.get("demo")
        self.assertEqual(demo.connections["aws"], {"profile": "p", "account": "123456789012"})
        self.assertEqual(demo.connections["github"], "octocat")
        self.assertEqual(demo.roots, self.registry.get("demo").roots)

    def test_settings_round_trip_and_are_not_a_project(self):
        self.assertEqual(self.registry.shared_aws_profiles(), set())  # default: none
        self.registry.data["settings"] = {"shared_aws_profiles": ["ci-worker"]}
        self.registry.save()
        self.assertIn('[settings]\nshared_aws_profiles = ["ci-worker"]\n', self.registry.path.read_text())
        again = Registry.load(self.registry.path)
        self.assertEqual(again.shared_aws_profiles(), {"ci-worker"})
        self.assertEqual(list(again.projects()), ["demo"])
        again.save()
        self.assertEqual(Registry.load(self.registry.path).data, again.data)

    def test_legacy_kinds_are_ignored_but_kept_in_the_file(self):
        for kind in LEGACY_KINDS:
            self.registry.set_connection("demo", kind, {"testing": {"token": "keychain:s/a"}})
        self.registry.set_connection("demo", "aws", {"profile": "p", "account": "123456789012"})
        self.registry.save()
        demo = Registry.load(self.registry.path).get("demo")
        self.assertEqual(list(demo.connections), ["aws"])
        self.assertEqual(demo.legacy, list(LEGACY_KINDS))
        env = environment.build(demo, {})
        self.assertEqual(env["KEYFIT_SUMMARY"], "demo · aws p → …9012")
        self.assertEqual(Registry.load(self.registry.path).data["projects"]["demo"][LEGACY_KINDS[0]],
                         {"testing": {"token": "keychain:s/a"}})  # left for the user to remove

    def test_init_imports_envrc_and_skips_quietly_without_one(self):
        self.enterContext(mock.patch.dict(os.environ, self.clean(), clear=True))
        reg_path = self.tmp / "envrc-registry.toml"
        for name, envrc in (("plain", None), ("direnv", "use aws acme 123456789012\n")):
            repo = self.tmp / name
            repo.mkdir()
            git("init", "-q", cwd=repo)
            if envrc:
                (repo / ".envrc").write_text(envrc)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(main(["--registry", str(reg_path), "init", "--yes", "--root", str(repo)]), 0)
            self.assertEqual("imported" in out.getvalue(), bool(envrc), out.getvalue())
        projects = Registry.load(reg_path).projects()
        self.assertEqual(projects["plain"].connections, {})
        self.assertEqual(projects["direnv"].connections["aws"], {"profile": "acme", "account": "123456789012"})

    def test_git_file_pointing_at_a_registered_repo_does_not_borrow_its_project(self):
        # an unpacked archive can carry a `.git` file or symlink naming any repository on the machine
        for name, make in (("gitfile", lambda d: (d / ".git").write_text(f"gitdir: {self.repo / '.git'}\n")),
                           ("symlink", lambda d: (d / ".git").symlink_to(self.repo / ".git")),
                           ("worktree-entry", lambda d: (d / ".git").write_text(
                               f"gitdir: {self.repo / '.git' / 'worktrees' / 'feature'}\n"))):
            folder = self.tmp / "downloads" / name
            folder.mkdir(parents=True)
            make(folder)
            r = resolve(folder, self.registry)
            self.assertIsNone(r.project, f"{name}: {r.how}")

    def test_worktree_listed_with_a_relative_path_still_resolves(self):
        admin = self.repo / ".git" / "worktrees" / "feature"
        (admin / "gitdir").write_text(os.path.relpath(self.worktree / ".git", admin) + "\n")
        self.assertEqual(resolve(self.worktree, self.registry).project.name, "demo")

    def clean(self) -> dict:
        """An environment for `keyfit init` in-process: a temporary HOME and config folder, none of the shell's tokens."""
        return {"HOME": str(self.tmp / "home"), "PATH": "/usr/bin:/bin", "KEYFIT_CONFIG_DIR": str(self.tmp / "keyfit")}

    def broken_git(self, script: str) -> dict:
        """PATH with a git that fails as *script* says, like one broken by a macOS update."""
        folder = self.tmp / "broken-git"
        folder.mkdir(exist_ok=True)
        (folder / "git").write_text(f"#!/bin/sh\n{script}\n")
        (folder / "git").chmod(0o755)
        return {"PATH": f"{folder}:/bin"}

    def test_failing_git_inside_a_repository_refuses_instead_of_answering_not_registered(self):
        plain = self.tmp / "plain"
        plain.mkdir()
        sub = self.worktree / "src"
        sub.mkdir()
        for script, reason in (
                ("echo 'You have not agreed to the Xcode license agreements.' >&2; exit 69",
                 "You have not agreed to the Xcode license agreements."),
                ("echo 'xcrun: error: invalid active developer path' >&2; exit 1",
                 "xcrun: error: invalid active developer path"),
                ("echo \"fatal: detected dubious ownership in repository at '$2'\" >&2; exit 128",
                 "fatal: detected dubious ownership in repository at '{}'"),
                ("exit 1", "exit 1")):
            with self.subTest(reason), mock.patch.dict(os.environ, self.broken_git(script)):
                for folder in (self.repo, self.worktree, sub):
                    with self.assertRaises(GitError) as caught:
                        resolve(folder, self.registry)
                    self.assertEqual((caught.exception.path, caught.exception.reason), (folder, reason.format(folder)))
                r = resolve(plain, self.registry)  # no .git at or above: no repository, whatever git says
                self.assertIsNone(r.project)
                self.assertEqual(r.how, "not inside a registered project")
        with mock.patch.dict(os.environ, {"PATH": str(self.tmp / "nowhere")}):  # no git at all
            with self.assertRaisesRegex(GitError, "git is not installed"):
                resolve(sub, self.registry)
            self.assertIsNone(resolve(plain, self.registry).project)

    def test_a_git_file_naming_a_folder_that_is_gone_refuses(self):
        stale = self.tmp / "stale-worktree"
        stale.mkdir()
        (stale / ".git").write_text(f"gitdir: {self.tmp / 'gone' / '.git' / 'worktrees' / 'x'}\n")
        # older git: "not a git repository", newer git: "gitfile does not point to a valid repository"
        with self.assertRaisesRegex(GitError, "not a git repository|does not point to a valid repository"):
            resolve(stale, self.registry)

    def test_inside_git_dir_and_bare_repositories_still_answer(self):
        # git finds the repository there and only says there is no work tree: not a git failure
        bare = self.tmp / "bare.git"
        git("init", "-q", "--bare", str(bare), cwd=self.tmp)
        self.assertIsNone(resolve(bare, self.registry).project)
        self.assertEqual(resolve(self.repo / ".git", self.registry).project.name, "demo")  # ancestor rule, as before

    def test_git_hook_variables_do_not_redirect_the_lookup(self):
        # a git hook exports these; a tool it runs from a subfolder of a worktree inherits them
        sub = self.worktree / "src"
        sub.mkdir()
        for name, value in (("GIT_DIR", str(self.repo / ".git" / "worktrees" / "feature")),
                            ("GIT_WORK_TREE", str(sub)), ("GIT_COMMON_DIR", str(self.tmp / "elsewhere")),
                            ("GIT_INDEX_FILE", str(self.tmp / "index")), ("GIT_CEILING_DIRECTORIES", str(self.worktree))):
            with self.subTest(name), mock.patch.dict(os.environ, {name: value}):
                r = resolve(sub, self.registry)
                self.assertEqual((r.project and r.project.name, r.root), ("demo", self.repo), r.how)

    def test_submodule_and_nested_repository_in_a_worktree_resolve_to_its_project(self):
        lib = self.tmp / "lib"
        lib.mkdir()
        git("init", "-q", "-b", "main", cwd=lib)
        (lib / "README").write_text("x")
        git("add", ".", cwd=lib)
        git("commit", "-q", "-m", "init", cwd=lib)
        git("-c", "protocol.file.allow=always", "submodule", "add", "-q", str(lib), "lib", cwd=self.repo)
        git("commit", "-q", "-m", "lib", cwd=self.repo)
        worktree = self.tmp / "superset" / "demo-with-lib"
        git("worktree", "add", "-q", str(worktree), "-b", "with-lib", cwd=self.repo)
        git("-c", "protocol.file.allow=always", "submodule", "update", "-q", "--init", cwd=worktree)
        nested = worktree / "vendor" / "tool"
        nested.mkdir(parents=True)
        git("init", "-q", cwd=nested)
        for folder in (self.repo / "lib", worktree / "lib", nested):
            r = resolve(folder, self.registry)
            self.assertEqual((r.project and r.project.name, r.root), ("demo", self.repo), f"{folder}: {r.how}")
            self.assertIn("nested git repository", r.how)

    def test_unregistered_folders_ask_git_once(self):
        other = self.tmp / "other"
        other.mkdir()
        git("init", "-q", cwd=other)
        plain = self.tmp / "plain"
        plain.mkdir()
        for folder in (other, plain):
            with mock.patch("keyfit.resolve.subprocess.run", wraps=subprocess.run) as run:
                self.assertIsNone(resolve(folder, self.registry).project)
            self.assertEqual(run.call_count, 1, folder)

    def test_cli_init_and_which(self):
        self.enterContext(mock.patch.dict(os.environ, self.clean(), clear=True))
        reg_path = self.tmp / "cli-registry.toml"
        other = self.tmp / "cli-repo"
        other.mkdir()
        git("init", "-q", cwd=other)
        self.assertEqual(main(["--registry", str(reg_path), "init", "--yes", "--root", str(other), "--name", "cli"]), 0)
        self.assertEqual(main(["--registry", str(reg_path), "which", str(other)]), 0)
        self.assertEqual(main(["--registry", str(reg_path), "which", str(self.tmp / "plain-x")]), 1)


if __name__ == "__main__":
    unittest.main()
