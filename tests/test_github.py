import contextlib
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from keyfit import cache
from keyfit.adapters import ADAPTERS
from keyfit.adapters.github import GithubAdapter
from keyfit.shim import is_login_command, positionals

os.environ["KEYFIT_CONFIG_DIR"] = tempfile.mkdtemp()  # tests never touch the real ~/.config/keyfit

# Fake gh: only `auth token --hostname github.com --user <login>` is allowed; anything else fails the test.
FAKE_GH = f"#!{sys.executable}" + """
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
        # docs/accounts.md: "Other GitHub token variables, GH_HOST and GH_CONFIG_DIR are removed."
        documented = ("GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN", "GH_HOST", "GH_CONFIG_DIR")
        env = self.env({"hubot": "tok-hubot"}, GH_TOKEN="inherited", **{var: "inherited" for var in documented})
        self.github.inject({"login": "hubot"}, env)
        self.assertEqual(env["GH_TOKEN"], "tok-hubot")
        self.assertEqual([var for var in documented if var in env], [])

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
        for words in (["logout"], ["refresh"], ["switch", "--user", "octocat"], ["setup-git"]):
            self.assertTrue(is_login_command("gh", ["auth", *words]), words)
        for words in (["token"], ["status", "--show-token"], ["git-credential", "get"], []):  # they print tokens
            self.assertFalse(is_login_command("gh", ["auth", *words]), words)

    def test_a_word_that_may_be_an_option_value_is_no_login_command(self):
        # `--add-dir auth` starts a Claude session in which `auth` is a folder; taking it for `claude auth`
        # would skip every check and start the session without the project's environment
        self.assertFalse(is_login_command("claude", ["--add-dir", "auth"]))
        self.assertFalse(is_login_command("codex", ["-C", "login"]))
        self.assertTrue(is_login_command("claude", ["--model=opus", "auth", "login"]))


# Fake gh for the shim: it answers `gh auth token` like gh, with GH_TOKEN first, else the token of its active account
# (octocat); it logs only its arguments.
FAKE_GH_SHIM = f"#!{sys.executable}" + """
import json, os, sys
args = sys.argv[1:]
stored = json.loads(os.environ["FAKE_GH_TOKENS"])
token = os.environ.get("GH_TOKEN") or stored.get("octocat")
with open(os.environ["GH_CALLS"], "a") as fh: fh.write(json.dumps(args) + "\\n")
if args[:5] == ["auth", "token", "--hostname", "github.com", "--user"]:  # how Keyfit reads an account's token
    if not stored.get(args[5]): sys.exit(1)
    print(stored[args[5]]); sys.exit(0)
if args[:2] == ["auth", "token"]:
    print(token); sys.exit(0)
if args[:3] == ["auth", "git-credential", "get"]:
    print(f"protocol=https\\nhost=github.com\\nusername=x-access-token\\npassword={token}"); sys.exit(0)
print(json.dumps({"argv": args, "GH_TOKEN": os.environ.get("GH_TOKEN")}))
"""


class ShimTests(unittest.TestCase):
    """The gh shim written by `keyfit setup`, run as a real process: project demo acts as hubot, while gh's active
    account is octocat."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        (self.bin / "gh").write_text(FAKE_GH_SHIM)
        (self.bin / "gh").chmod(0o755)
        self.calls = self.tmp / "calls"
        self.calls.write_text("")
        self.config = self.tmp / "keyfit"
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=self.repo, check=True)
        with mock.patch.dict(os.environ, {"KEYFIT_CONFIG_DIR": str(self.config), "HOME": str(self.home),
                                          "PATH": f"{self.bin}{os.pathsep}/usr/bin:/bin"}, clear=True):
            with contextlib.redirect_stdout(io.StringIO()):
                from keyfit.cli import main
                main(["init", "--yes", "--root", str(self.repo), "--name", "demo"])
                with mock.patch.object(sys, "platform", "darwin"):
                    main(["setup"])
            reg = self.config / "registry.toml"
            reg.write_text(reg.read_text().replace(f'roots = ["{self.repo}"]\n', f'roots = ["{self.repo}"]\ngithub = "hubot"\n')
                           + '\n[accounts.github.hubot]\nlogin = "hubot"\n')
            # what a passing online check leaves behind: the shim starts from the cache, never asking GitHub
            fingerprint = ADAPTERS["github"]._fingerprint("tok-hubot", {"HOME": str(self.home)})
            cache.put(cache.key("demo", "github", {"login": "hubot", "name": "hubot"}, fingerprint), "ok",
                      "login confirmed")

    def run_gh(self, *args, tokens=None, cwd=None, **extra):
        env = {"PATH": f"{self.config / 'bin'}{os.pathsep}{self.bin}{os.pathsep}/usr/bin:/bin", "HOME": str(self.home),
               "KEYFIT_CONFIG_DIR": str(self.config), "GH_CALLS": str(self.calls),
               "FAKE_GH_TOKENS": json.dumps({"octocat": "tok-octocat", "hubot": "tok-hubot"} if tokens is None else tokens),
               "HTTPS_PROXY": "http://127.0.0.1:9", **extra}  # should a check ever go online: a closed port
        return subprocess.run(["gh", *args], cwd=cwd or self.repo, env=env, capture_output=True, text=True, timeout=60)

    def gh_calls(self):
        return [json.loads(line) for line in self.calls.read_text().splitlines()]

    def test_auth_token_prints_the_projects_token(self):
        # gh answers with GH_TOKEN first: the project's checked token, never gh's active account (octocat)
        for args in (["auth", "token"], ["auth", "token", "--hostname", "github.com"], ["auth", "token", "-u", "HuBot"],
                     ["auth", "token", "--secure-storage", "--user", "hubot"]):
            out = self.run_gh(*args, GH_TOKEN="tok-octocat")
            self.assertEqual((out.returncode, out.stdout), (0, "tok-hubot\n"), (args, out.stderr))
        out = self.run_gh("auth", "git-credential", "get")
        self.assertIn("password=tok-hubot\n", out.stdout)
        seen = json.loads(self.run_gh("auth", "status").stdout)
        self.assertEqual(seen, {"argv": ["auth", "status"], "GH_TOKEN": "tok-hubot"})
        seen = json.loads(self.run_gh("auth", "status", "--active", "--show-token").stdout)  # the project's only
        self.assertEqual(seen["GH_TOKEN"], "tok-hubot")

    def test_auth_token_goes_through_the_checks(self):
        out = self.run_gh("auth", "token", tokens={"octocat": "tok-octocat"})  # gh keeps no token for hubot
        self.assertEqual((out.returncode, out.stdout), (1, ""))
        self.assertIn("keyfit: gh not started, demo needs attention\n  ✘ github     hubot   gh has no stored login for "
                      "hubot", out.stderr)

    def test_another_accounts_token_is_refused(self):
        for args, refused in ((["auth", "token", "--user", "octocat"], "refusing --user octocat."),
                              (["auth", "token", "--user=octocat"], "refusing --user octocat."),
                              (["auth", "token", "-uoctocat"], "refusing --user octocat."),
                              (["auth", "-u", "octocat", "token"], "refusing --user octocat."),
                              (["auth", "status", "--user", "octocat"], "refusing --user octocat."),
                              (["auth", "token", "--secure-storage"], "refusing --secure-storage without --user hubot: "
                               "gh would read the token of its active account, which may be another one."),
                              (["auth", "status", "--show-token"], "refusing --show-token without --active: gh would "
                               "print the token of every account it keeps, not only hubot's.\n  Only hubot's: gh auth "
                               "status --active --show-token"),
                              (["auth", "status", "-t", "--hostname", "github.com"], "refusing --show-token"),
                              (["auth", "status", "--json", "hosts", "--show-token"], "refusing --show-token"),
                              (["auth", "status", "-th", "github.com"], "refusing --show-token")):
            with self.subTest(args=args):
                self.calls.write_text("")
                out = self.run_gh(*args)
                self.assertEqual((out.returncode, out.stdout), (1, ""))
                self.assertTrue(out.stderr.startswith(f"keyfit: this folder belongs to demo (GitHub hubot); {refused}"),
                                out.stderr)
                self.assertTrue(out.stderr.endswith("\n  Bypass once: KEYFIT_DISABLE=1 gh …\n"), out.stderr)
                self.assertNotIn(args, self.gh_calls())  # the real gh never ran it
        for args in (["auth", "status", "-at"], ["auth", "status", "--show-token=false"], ["auth", "--", "-u", "x"]):
            self.assertEqual(self.run_gh(*args).returncode, 0, args)

    def test_a_project_without_a_github_account_lists_gh_logins_but_prints_no_token(self):
        plain = self.tmp / "plain"
        plain.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=plain, check=True)
        with mock.patch.dict(os.environ, {"KEYFIT_CONFIG_DIR": str(self.config), "HOME": str(self.home),
                                          "PATH": f"{self.bin}{os.pathsep}/usr/bin:/bin"}, clear=True), \
                contextlib.redirect_stdout(io.StringIO()):
            from keyfit.cli import main
            main(["init", "--yes", "--root", str(plain), "--name", "plain"])
        # `gh auth status` names the logins to map, so it runs as a sign-in does: unchecked, without GH_TOKEN
        for args in (["auth", "status"], ["auth", "status", "--hostname", "github.com", "--active"]):
            out = self.run_gh(*args, cwd=plain, GH_TOKEN="tok-octocat")
            self.assertEqual((out.returncode, json.loads(out.stdout)), (0, {"argv": args, "GH_TOKEN": None}),
                             out.stderr)
        for args in (["auth", "status", "--show-token"], ["auth", "status", "-t"], ["auth", "token"],
                     ["auth", "git-credential", "get"]):
            out = self.run_gh(*args, cwd=plain)
            self.assertEqual((out.returncode, out.stdout), (1, ""), args)
            self.assertEqual(out.stderr, "keyfit: project plain has no github account mapped, so gh was not started.\n"
                                         "  Map one: keyfit add github --login <a login gh auth status lists>   "
                                         "(bypass once: KEYFIT_DISABLE=1 gh …)\n")

    def test_sign_ins_run_unchecked_without_the_projects_token(self):
        # gh keeps no token for hubot, so the check fails: the sign-ins still run, as gh manages its own tokens
        for args in (["auth", "login", "--web"], ["auth", "logout", "--user", "octocat"], ["auth", "refresh"],
                     ["auth", "switch", "--user", "octocat"], ["auth", "setup-git"]):
            out = self.run_gh(*args, tokens={"octocat": "tok-octocat"}, GH_TOKEN="tok-octocat")
            self.assertEqual((out.returncode, json.loads(out.stdout)), (0, {"argv": args, "GH_TOKEN": None}),
                             out.stderr)


class AddGithubMergeTests(unittest.TestCase):
    def test_second_project_does_not_erase_shared_account_details(self):
        from keyfit.cli import main
        from keyfit.registry import Registry
        import keyfit.cli as cli
        tmp = Path(tempfile.mkdtemp()).resolve()
        self.enterContext(mock.patch.dict(os.environ, {"HOME": str(tmp), "PATH": "/usr/bin:/bin",
                                                       "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
                                                       "KEYFIT_CONFIG_DIR": str(tmp / "keyfit")}, clear=True))
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
        with mock.patch.dict(os.environ, {"HOME": str(tmp), "PATH": "/usr/bin:/bin", "GIT_CONFIG_NOSYSTEM": "1",
                                          "GIT_CONFIG_GLOBAL": "/dev/null", "KEYFIT_CONFIG_DIR": str(tmp / "keyfit")},
                             clear=True), \
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
