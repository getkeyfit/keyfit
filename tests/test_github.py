import json
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

from keyfit.adapters.github import GithubAdapter
from keyfit.shim import is_login_command, positionals

os.environ["KEYFIT_CONFIG_DIR"] = tempfile.mkdtemp()  # tests never touch the real ~/.config/keyfit

# Fake gh: only `auth token --hostname github.com --user <login>` is allowed; anything else fails the test.
FAKE_GH = """#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
with open(os.environ["GH_CALLS"], "a") as fh: fh.write(json.dumps(args) + "\\n")
if args[:4] != ["auth", "token", "--hostname", "github.com"] or args[4] != "--user":
    sys.exit(97)
token = json.loads(os.environ.get("FAKE_GH_TOKENS", "{}")).get(args[5])
if not token: sys.exit(1)
print(token)
"""

WHO = {"tok-octocat": "octocat", "tok-hubot": "hubot"}


def fetch(token, timeout):
    return (200, {"login": WHO[token]}) if token in WHO else (401, {})


class GithubTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        gh = self.bin / "gh"
        gh.write_text(FAKE_GH)
        gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
        self.calls = self.tmp / "calls"
        self.calls.write_text("")
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=self.repo, check=True)
        subprocess.run(["git", "config", "user.email", "me@primary.io"], cwd=self.repo, check=True)
        subprocess.run(["git", "remote", "add", "origin", "git@github.com:org/repo.git"], cwd=self.repo, check=True)
        self.github = GithubAdapter(fetch=fetch)
        self.cwd = os.getcwd()
        os.chdir(self.repo)

    def tearDown(self):
        os.chdir(self.cwd)

    def env(self, tokens, **extra):
        return {"HOME": str(self.tmp), "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}",
                "GH_CALLS": str(self.calls), "FAKE_GH_TOKENS": json.dumps(tokens), **extra}

    def check(self, conn, tokens, **extra):
        env = self.env(tokens, **extra)
        self.github.inject(conn, env)
        return env, self.github.verify(conn, env)

    def test_inject_uses_the_accounts_own_token_and_scrubs_inherited_ones(self):
        env = self.env({"hubot": "tok-hubot"}, GH_TOKEN="inherited", GITHUB_TOKEN="x", GH_CONFIG_DIR="/x", GH_HOST="ghe")
        self.github.inject({"login": "hubot"}, env)
        self.assertEqual(env["GH_TOKEN"], "tok-hubot")
        for var in ("GITHUB_TOKEN", "GH_CONFIG_DIR", "GH_HOST"):
            self.assertNotIn(var, env)

    def test_only_reads_never_writes_gh_state(self):
        self.check({"login": "hubot"}, {"hubot": "tok-hubot"})
        calls = [json.loads(line) for line in self.calls.read_text().splitlines()]
        self.assertTrue(calls)
        self.assertTrue(all(c[:2] == ["auth", "token"] for c in calls), calls)

    def test_confirmed(self):
        _, check = self.check({"login": "octocat"}, {"octocat": "tok-octocat"})
        self.assertEqual(check.status, "ok")

    def test_stored_token_of_another_account_is_caught(self):  # the 2026-10-08 incident
        _, check = self.check({"login": "hubot"}, {"hubot": "tok-octocat"})
        self.assertTrue(check.failed)
        self.assertIn("actually authenticates as octocat", check.detail)
        self.assertIn("gh auth login", check.fix)

    def test_missing_login(self):
        env, check = self.check({"login": "Nobody"}, {}, GH_TOKEN="inherited")
        self.assertNotIn("GH_TOKEN", env)  # never falls back to the inherited token
        self.assertTrue(check.failed)

    def test_revoked_token(self):
        _, check = self.check({"login": "octocat"}, {"octocat": "tok-revoked"})
        self.assertIn("rejected", check.detail)

    def test_wrong_author_email_and_ssh_host_warn(self):
        conn = {"login": "hubot", "email": "me@client.io", "ssh_host": "github.com-client"}
        _, check = self.check(conn, {"hubot": "tok-hubot"})
        self.assertEqual(check.status, "warn")
        self.assertIn("me@primary.io", check.detail)
        self.assertIn("github.com-client", check.detail)

    def test_login_commands_are_recognised(self):
        self.assertTrue(is_login_command("aws", ["--profile", "acme", "sso", "login"]))
        self.assertTrue(is_login_command("aws", ["sso", "login", "--sso-session", "globex"]))
        self.assertTrue(is_login_command("gh", ["auth", "login", "--hostname", "github.com"]))
        self.assertTrue(is_login_command("claude", ["auth", "login"]))
        self.assertFalse(is_login_command("aws", ["s3", "ls"]))
        self.assertFalse(is_login_command("claude", ["-p", "auth the user"]))
        self.assertEqual(positionals(["--profile", "x", "sso", "login"]), ["sso", "login"])
        self.assertTrue(is_login_command("gh", ["auth", "login", "--git-protocol", "ssh", "--web"]))

    def test_a_word_that_may_be_an_option_value_is_no_login_command(self):
        # `--add-dir auth` starts a Claude session in which `auth` is a folder; taking it for `claude auth`
        # would skip every check and start the session without the project's environment
        self.assertFalse(is_login_command("claude", ["--add-dir", "auth"]))
        self.assertFalse(is_login_command("codex", ["-C", "login"]))
        self.assertTrue(is_login_command("claude", ["--model=opus", "auth", "login"]))


class AddGithubMergeTests(unittest.TestCase):
    def test_second_project_does_not_erase_shared_account_details(self):
        from keyfit.cli import main
        from keyfit.registry import Registry
        import keyfit.cli as cli
        tmp = Path(tempfile.mkdtemp()).resolve()
        repos = []
        for name, remote in (("a", "git@github.com:org/a.git"), ("b", "https://github.com/org/b")):
            repo = tmp / name
            repo.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            subprocess.run(["git", "remote", "add", "origin", remote], cwd=repo, check=True)
            repos.append(repo)
        reg = tmp / "r.toml"
        for repo in repos:
            main(["--registry", str(reg), "init", "--yes", "--root", str(repo), "--name", repo.name])
        import keyfit.adapters.github as gh
        orig_read, orig_who = gh.read_token, cli.ADAPTERS["github"].whoami
        gh.read_token = lambda login, env, timeout=15: ("tok", "")
        cli.ADAPTERS["github"].whoami = lambda token: "Me"
        try:
            main(["--registry", str(reg), "add", "github", "--project", "a", "--login", "Me", "--name", "me"])
            main(["--registry", str(reg), "add", "github", "--project", "b", "--login", "Me", "--name", "me"])
        finally:
            gh.read_token, cli.ADAPTERS["github"].whoami = orig_read, orig_who
        account = Registry.load(reg).data["accounts"]["github"]["me"]
        self.assertEqual(account["ssh_host"], "github.com")

    def test_a_shared_name_given_to_another_login_names_who_changed_and_keeps_nothing_of_the_old_login(self):
        import contextlib
        import io
        from unittest import mock

        from keyfit.cli import main
        from keyfit.registry import Registry
        import keyfit.adapters.github as gh
        import keyfit.cli as cli
        tmp = Path(tempfile.mkdtemp()).resolve()
        for name, email in (("acme", "me@acme.io"), ("globex", "me@globex.io")):
            (tmp / name).mkdir()
            subprocess.run(["git", "init", "-q"], cwd=tmp / name, check=True)
            subprocess.run(["git", "config", "user.email", email], cwd=tmp / name, check=True)
        reg = tmp / "r.toml"

        def keyfit(*argv):
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = main(["--registry", str(reg), *argv])
            return code, out.getvalue(), err.getvalue()
        with mock.patch.dict(os.environ, {"HOME": str(tmp), "GIT_CONFIG_NOSYSTEM": "1"}), \
                mock.patch.object(gh, "read_token", lambda login, env, timeout=15: (f"tok-{login}", "")), \
                mock.patch.object(cli.ADAPTERS["github"], "whoami", lambda token: WHO[token]):
            for name in ("acme", "globex"):
                keyfit("init", "--yes", "--root", str(tmp / name), "--name", name)
            keyfit("add", "github", "--project", "acme", "--login", "octocat", "--name", "me")
            self.assertEqual(keyfit("add", "github", "--project", "globex", "--login", "octocat", "--name", "me"),
                             (0, "globex: github account 'me' → octocat (email me@acme.io)\n", ""))  # shared
            self.assertEqual(keyfit("add", "github", "--project", "globex", "--login", "hubot", "--name", "me"),
                             (0, "globex: github account 'me' → hubot (email me@globex.io)\n"
                                 "  account 'me' was login octocat; it changed for acme too\n", ""))
            self.assertEqual(keyfit("add", "github", "--project", "globex", "--login", "hubot", "--name", "a\nb"),
                             (1, "", "a name is letters, digits, '.', '_' and '-' (at most 64)\n"))
        self.assertEqual(Registry.load(reg).data["accounts"]["github"]["me"], {"login": "hubot", "email": "me@globex.io"})


if __name__ == "__main__":
    unittest.main()
