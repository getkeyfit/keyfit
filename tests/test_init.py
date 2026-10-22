"""Guided `keyfit init` and `keyfit doctor`, with fake aws, gh, claude, codex, vercel and security in temporary
folders."""

import base64
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import tomllib
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from keyfit import cli, secrets
from keyfit.adapters import ADAPTERS
from keyfit.adapters.github import logins
from keyfit.cli import main
from test_secrets import FAKE_SECURITY
from test_vercel import TOKEN as VERCEL_TOKEN, api as vercel_api

os.environ["KEYFIT_CONFIG_DIR"] = tempfile.mkdtemp()  # tests never touch the real ~/.config/keyfit

KEY = "lin_api_" + "K3" * 20  # fake; never a real key

# Fake gh: read-only `auth status` and `auth token` only; every call is logged with the GH_CONFIG_DIR it saw.
FAKE_GH = """#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
with open(os.environ["GH_CALLS"], "a") as fh:
    fh.write(json.dumps({"argv": args, "GH_CONFIG_DIR": os.environ.get("GH_CONFIG_DIR")}) + "\\n")
tokens = json.loads(os.environ.get("FAKE_GH_TOKENS", "{}"))
if args == ["auth", "status", "--hostname", "github.com"]:
    out = sys.stderr if os.environ.get("FAKE_GH_OLD") else sys.stdout
    print("github.com", file=out)
    for i, login in enumerate(tokens):
        said = f"as {login} (oauth_token)" if os.environ.get("FAKE_GH_OLD") else f"account {login} (keyring)"
        print(f"  \\u2713 Logged in to github.com {said}", file=out)
        print(f"  - Active account: {'true' if i == 0 else 'false'}", file=out)
        print("  - Token: gho_************************************", file=out)
    print("  X Failed to log in to github.com account expired (keyring)", file=out)
    sys.exit(0 if tokens else 1)
if args[:5] == ["auth", "token", "--hostname", "github.com", "--user"] and tokens.get(args[5]):
    print(tokens[args[5]]); sys.exit(0)
sys.exit(97)
"""

FAKE_CLAUDE = """#!/usr/bin/env python3
import json, os, sys
assert sys.argv[1:] == ["auth", "status", "--json"], sys.argv
folder = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.environ["HOME"], ".claude")
print(json.dumps({"loggedIn": True, "authMethod": "claude.ai", "email": "me@work.io", "orgId": "org-work",
                  "orgName": "Work", "subscriptionType": "team", "configDirectory": folder}))
"""

FAKE_AWS = """#!/usr/bin/env python3
import os, sys
assert sys.argv[1:3] == ["sts", "get-caller-identity"], sys.argv
with open(os.environ["STS_COUNT"], "a") as fh: fh.write("x")
print("123456789012")
"""

AWS_CONFIG = """
[default]
region = eu-central-1

[profile acme-dev]
credential_process = granted credential-process --profile=acme-dev

[profile globex]
credential_process = granted credential-process --profile=globex
"""


def executable(path: Path, text: str) -> None:
    path.write_text(text)
    path.chmod(0o755)


def jwt(claims: dict) -> str:
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
    return f"{enc({'alg': 'none'})}.{enc(claims)}.sig"


class TTY:
    """A terminal on stdin."""
    def isatty(self):
        return True

    def readline(self):
        raise AssertionError("stdin read although it is a terminal")


class Machine(unittest.TestCase):
    """A temporary HOME with AWS profiles, gh, Claude and Codex logins, and an empty Keyfit config."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.home = self.tmp / "home"
        (self.home / ".aws").mkdir(parents=True)
        (self.home / ".aws" / "config").write_text(AWS_CONFIG)
        (self.home / ".claude").mkdir()
        (self.home / ".codex").mkdir()
        claims = {"https://api.openai.com/auth": {"chatgpt_plan_type": "pro", "chatgpt_account_id": "acct-1"}}
        (self.home / ".codex" / "auth.json").write_text(json.dumps(
            {"auth_mode": "chatgpt", "OPENAI_API_KEY": None, "tokens": {"id_token": jwt(claims), "access_token": "x"}}))
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        for name, text in (("gh", FAKE_GH), ("claude", FAKE_CLAUDE), ("aws", FAKE_AWS), ("codex", "#!/bin/sh\n"),
                           ("vercel", "#!/bin/sh\nexit 97\n"), ("supabase", "#!/bin/sh\nexit 97\n"),
                           ("wrangler", "#!/bin/sh\nexit 97\n"), ("convex", "#!/bin/sh\nexit 97\n"),
                           ("opencode", "#!/bin/sh\nexit 97\n"), ("copilot", "#!/bin/sh\nexit 97\n"),
                           ("gcloud", "#!/bin/sh\nexit 97\n"), ("firebase", "#!/bin/sh\nexit 97\n"),
                           ("gemini", "#!/bin/sh\nexit 97\n")):
            executable(self.bin / name, text)
        self.stub = self.tmp / "stub"
        self.stub.mkdir()
        executable(self.stub / "security", FAKE_SECURITY)
        self.config = self.tmp / "keyfit"
        self.repo = self.tmp / "acme"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=self.repo, check=True)
        subprocess.run(["git", "config", "user.email", "me@acme.io"], cwd=self.repo, check=True)
        self.gh_calls = self.tmp / "gh-calls"
        self.gh_calls.write_text("")
        self.sts = self.tmp / "sts-count"
        self.sts.write_text("")
        env = {"PATH": f"{self.bin}{os.pathsep}/usr/bin:/bin", "HOME": str(self.home),
               "KEYFIT_CONFIG_DIR": str(self.config), "GH_CALLS": str(self.gh_calls), "STS_COUNT": str(self.sts),
               "FAKE_GH_TOKENS": json.dumps({"octocat": "tok-octocat", "hubot": "tok-hubot"}),
               "GH_CONFIG_DIR": str(self.tmp / "elsewhere")}
        who = {"tok-octocat": "octocat", "tok-hubot": "hubot"}
        workspaces = {KEY: "acme"}
        for patcher in (
                mock.patch.dict(os.environ, env, clear=True),
                mock.patch.object(secrets, "SECURITY", str(self.stub / "security")),
                mock.patch.object(sys, "platform", "darwin"),
                mock.patch("urllib.request.urlopen", side_effect=AssertionError("no network in tests")),
                mock.patch.object(ADAPTERS["vercel"], "fetch", vercel_api),
                mock.patch.object(ADAPTERS["github"], "fetch",
                                  lambda token, timeout: (200, {"login": who[token]}) if token in who else (401, {})),
                mock.patch.object(ADAPTERS["copilot"], "fetch",
                                  lambda token, timeout: (200, {"login": who[token]}) if token in who else (401, {})),
                mock.patch.object(ADAPTERS["linear"], "fetch", lambda key, timeout: (
                    (200, {"data": {"organization": {"urlKey": workspaces[key]}}}) if key in workspaces
                    else (401, {"errors": [{"message": "Authentication required"}]})))):
            patcher.start()
            self.addCleanup(patcher.stop)
        old = os.getcwd()
        os.chdir(self.repo)
        self.addCleanup(os.chdir, old)

    def registry(self) -> dict:
        return tomllib.loads((self.config / "registry.toml").read_text())

    def init(self, *answers, stdin=None, password=None, argv=()):
        """Run `keyfit init` with scripted answers; every question must be answered, no answer left over."""
        queue, self.asked, self.hidden = list(answers), [], []

        def ask(prompt):
            self.asked.append(prompt)
            if not queue:
                raise AssertionError(f"unexpected question: {prompt}")
            answer = queue.pop(0)
            if isinstance(answer, BaseException):
                raise answer
            return answer

        def getpass(prompt="Password: "):
            self.hidden.append(prompt)
            return password

        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(cli, "_ask", ask), mock.patch("sys.stdin", stdin or TTY()), \
                mock.patch("getpass.getpass", getpass), redirect_stdout(out), redirect_stderr(err):
            code = main(["init", *argv])
        self.assertEqual(queue, [], f"answers left over; asked: {self.asked}")
        return code, out.getvalue(), err.getvalue()


class GuidedInitTests(Machine):
    def test_new_user_gets_a_fully_configured_project_from_init_alone(self):
        code, out, err = self.init("9", "1", "2", "y", "y", "y", "y", password=KEY)
        self.assertEqual(code, 0, out + err)
        self.assertIn("  1. acme-dev\n  2. globex\n", out)  # `default` is never offered
        self.assertIn("type a number from 1-2", out)
        self.assertIn("  1. octocat\n  2. hubot\n", out)  # the failed login is not offered
        self.assertIn("Claude: signed in to Work (team) in ~/.claude", out)
        self.assertIn("Codex: signed in with ChatGPT pro in ~/.codex", out)
        self.assertEqual(self.asked, ["AWS profile for acme [1-2, Enter to skip]: "] * 2 + [
            "GitHub account for acme [1-2, Enter to skip]: ", "Use this Claude login for acme? [y/N] ",
            "Use this Codex login for acme? [y/N] ", "Start Copilot CLI in acme as its GitHub account hubot? [y/N] ",
            "Add a Linear API key for acme? [y/N] "])
        self.assertEqual(self.hidden, ["Linear API key (input hidden): "])

        reg = self.registry()
        project = reg["projects"]["acme"]
        self.assertEqual(project["aws"], {"profile": "acme-dev", "account": "123456789012"})
        self.assertEqual((project["github"], project["claude"], project["codex"], project["copilot"]),
                         ("hubot", "work", "pro", "hubot"))
        self.assertEqual(project["linear"], {"workspace": "acme", "key": "keychain:keyfit/linear-acme"})
        self.assertEqual(reg["accounts"]["github"]["hubot"], {"login": "hubot", "email": "me@acme.io"})
        self.assertEqual(reg["accounts"]["claude"]["work"]["org_name"], "Work")

        status = out[out.index("\nacme\n"):]  # ends with `keyfit status`
        for kind in ("aws", "github", "claude", "codex", "copilot", "linear"):
            self.assertRegex(status, rf"✔ {kind}\s")
        self.assertNotIn("✘", out)
        self.assertEqual(self.sts.read_text(), "xx")  # the account asked once, then confirmed by the status check

        calls = [json.loads(line) for line in self.gh_calls.read_text().splitlines()]
        self.assertIn(["auth", "status", "--hostname", "github.com"], [c["argv"] for c in calls])
        for call in calls:  # gh is only ever read, with its own config folder
            self.assertIn(call["argv"][:2], (["auth", "status"], ["auth", "token"]))
            self.assertIsNone(call["GH_CONFIG_DIR"])

        self.assertNotIn(KEY, out + err)
        for path in self.config.rglob("*"):
            if path.is_file():
                self.assertNotIn(KEY.encode(), path.read_bytes(), f"key in {path}")
        argv = [json.loads(line) for line in (self.stub / "argv.log").read_text().splitlines()]
        self.assertFalse(any(KEY in a for call in argv for a in call))

    def test_current_vercel_login_is_offered(self):
        folder = self.home / "Library" / "Application Support" / "com.vercel.cli"
        folder.mkdir(parents=True)
        (folder / "auth.json").write_text(json.dumps({"token": VERCEL_TOKEN}))
        (folder / "config.json").write_text(json.dumps({"currentTeam": "team_acme"}))
        code, out, err = self.init("", "", "n", "n", "y", "")
        self.assertEqual(code, 0, out + err)
        self.assertIn("Vercel: signed in as octocat, team acme in ~/Library/Application Support/com.vercel.cli\n", out)
        self.assertEqual(self.asked[4], "Use this Vercel login for acme? [y/N] ")
        reg = self.registry()
        self.assertEqual(reg["projects"]["acme"]["vercel"], "acme")
        self.assertEqual(reg["accounts"]["vercel"]["acme"]["team_id"], "team_acme")
        self.assertIn("  ✔ vercel     octocat → acme   login and team confirmed\n", out)
        self.assertNotIn(VERCEL_TOKEN, out + err + (self.config / "registry.toml").read_text())

    def test_declined_accounts_are_skipped(self):
        code, out, err = self.init("", "", "n", "", "")
        self.assertEqual(code, 0, out + err)
        self.assertEqual(list(self.registry()["projects"]["acme"]), ["roots"])
        self.assertIn("  no accounts yet — keyfit init offers the ones on this Mac, or map one: keyfit add "
                      "aws|github|claude|codex|opencode|copilot|gemini|vercel|supabase|cloudflare|convex|google|gcloud|firebase|"
                      "google-mcp|linear …\n"
                      "  aws, gh and vercel won't start here until mapped: keyfit add aws|github|vercel …\n", out)
        self.assertEqual(self.hidden, [])
        self.assertEqual(self.sts.read_text(), "")

    def test_what_is_already_set_is_shown_and_left_alone(self):
        (self.repo / ".envrc").write_text("use aws acme-dev 123456789012\n")
        code, out, err = self.init("", "n", "n", "n")
        self.assertEqual(code, 0, out + err)
        self.assertIn("imported aws acme-dev → …9012 (from .envrc)", out)
        self.assertIn("AWS: already set — acme-dev → …9012", out)
        self.assertFalse(any(q.startswith("AWS") for q in self.asked))
        self.assertEqual(self.registry()["projects"]["acme"]["aws"], {"profile": "acme-dev", "account": "123456789012"})
        self.assertRegex(out, r"✔ aws\s+acme-dev → …9012\s+IAM key via Granted · account confirmed")

    def test_without_a_terminal_or_with_yes_nothing_is_asked(self):
        expected = (f"Registered project 'acme' → {self.repo}\n"
                    "Worktrees of this repository resolve to it automatically. Check it with: keyfit status\n"
                    "Keyfit is not set up yet, so claude, codex, opencode, copilot, gemini, aws, gh, vercel, supabase, "
                    "wrangler, convex, gcloud and firebase still run without checks.\n"
                    "  Set it up: keyfit setup --rc, then open a new terminal\n")
        self.assertEqual(self.init(stdin=io.StringIO("1\ny\n")), (0, expected, ""))
        self.assertEqual(self.init(argv=["--yes"]), (0, expected, ""))
        self.assertEqual(self.gh_calls.read_text(), "")

    def test_init_says_when_keyfit_is_not_active_in_this_terminal(self):
        with redirect_stdout(io.StringIO()):
            self.assertEqual(main(["setup"]), 0)
        _, out, _ = self.init(argv=["--yes"])
        self.assertIn(f"{self.config / 'bin'} is not on PATH in this terminal, so tools here still run without "
                      "checks.\n  Open a new terminal", out)
        os.environ["PATH"] = f"{self.config / 'bin'}{os.pathsep}{os.environ['PATH']}"
        _, out, _ = self.init(argv=["--yes"])
        self.assertNotIn("without checks", out)

    def test_init_refuses_the_home_folder_and_above(self):
        # one project for every folder under it: tools anywhere in home would run with (or wait for) its accounts
        for folder in (self.home, self.home.parent, Path("/")):
            with self.subTest(folder=folder):
                code, out, err = self.init(argv=["--yes", "--root", str(folder)])
                self.assertEqual(code, 1)
                self.assertIn("would belong to one project.\n", err)
                self.assertIn("Go to a project's repository first", err)
        self.assertFalse((self.config / "registry.toml").exists())
        code, _, err = self.init(argv=["--yes", "--root", str(self.tmp / "missing")])
        self.assertEqual((code, err), (1, f"no folder {self.tmp / 'missing'}\n"))

    def test_folder_outside_git_is_registered_with_a_note(self):
        plain = self.tmp / "clients"
        plain.mkdir()
        code, out, _ = self.init(argv=["--yes", "--root", str(plain)])
        self.assertEqual(code, 0)
        self.assertIn(f"{plain} is not a git repository: every folder under it belongs to clients\n", out)

    def test_aws_config_file_is_respected(self):
        other = self.tmp / "aws-config"
        other.write_text("[profile shared-dev]\ncredential_process = granted credential-process --profile=shared-dev\n")
        os.environ["AWS_CONFIG_FILE"] = str(other)
        code, out, err = self.init("shared-dev", "", "n", "n", "n")
        self.assertEqual(code, 0, out + err)
        self.assertIn(f"AWS profiles in {other}:\n  1. shared-dev\n", out)
        self.assertEqual(self.registry()["projects"]["acme"]["aws"],
                         {"profile": "shared-dev", "config_file": str(other), "account": "123456789012"})
        self.assertRegex(out, r"✔ aws\s+shared-dev")  # the shims scrub AWS_CONFIG_FILE; the project keeps the file

    def test_missing_tools_and_logins_are_explained_and_skipped(self):
        for name in ("gh", "claude"):
            (self.bin / name).unlink()
        (self.home / ".codex" / "auth.json").unlink()
        (self.home / ".aws" / "config").unlink()
        code, out, err = self.init("n")
        self.assertEqual(code, 0, out + err)
        self.assertIn("AWS: no named profiles in ~/.aws/config", out)
        self.assertIn("GitHub: gh not found on PATH — skipped", out)
        self.assertIn("Claude: claude not found on PATH — skipped", out)
        self.assertIn("Codex: no clean ChatGPT login in ~/.codex (not signed in) — sign in (codex login)", out)
        self.assertIn("Vercel: not signed in (~/Library/Application Support/com.vercel.cli) — sign in (vercel login), "
                      "then: keyfit add vercel\n", out)
        self.assertEqual(self.asked, ["Add a Linear API key for acme? [y/N] "])
        for name in ("vercel", "codex"):
            (self.bin / name).unlink()
        code, out, err = self.init("n")
        self.assertIn("Vercel: vercel not found on PATH — skipped", out)
        self.assertIn("Codex: codex not found on PATH — skipped", out)  # not "no clean ChatGPT login"
        self.assertNotIn("codex login", out)

    def test_ctrl_c_stops_but_keeps_the_registration(self):
        code, out, _ = self.init("1", KeyboardInterrupt())
        self.assertEqual(code, 130)
        self.assertIn("Stopped. 'acme' is registered", out)
        self.assertEqual(sorted(self.registry()["projects"]["acme"]), ["aws", "roots"])

    def test_gh_logins_from_older_gh_on_stderr(self):
        os.environ["FAKE_GH_OLD"] = "1"
        self.assertEqual(logins(dict(os.environ)), (["octocat", "hubot"], ""))


class SameFolderNameTests(Machine):
    """Folder names such as api repeat across clients: a second `api` never joins the first one's project unasked."""

    def api_of(self, client: str, envrc: str = "") -> Path:
        repo = self.tmp / client / "api"
        repo.mkdir(parents=True)
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        if envrc:
            (repo / ".envrc").write_text(envrc)
        return repo

    def test_a_second_folder_of_the_same_name_needs_a_name_of_its_own(self):
        acme = self.api_of("acme", "use aws acme-dev 111111111111\n")
        globex = self.api_of("globex", "use aws globex 222222222222\n")
        self.assertEqual(self.init(argv=["--yes", "--root", str(acme)])[0], 0)
        before = (self.config / "registry.toml").read_text()
        self.assertEqual(self.init(argv=["--yes", "--root", str(globex)]), (1, "", (
            f"a project named 'api' already exists in {acme}; {globex} is another folder.\n"
            "  Give this one a name of its own: keyfit init --name <client>-api\n"
            "  (or, to add it to 'api' with the same accounts: keyfit init --name api)\n")))
        self.assertEqual((self.config / "registry.toml").read_text(), before)

        code, out, err = self.init(argv=["--yes", "--root", str(globex), "--name", "globex-api"])
        self.assertEqual(code, 0, err)
        self.assertIn(f"Registered project 'globex-api' → {globex}\n  imported aws globex → …2222 (from .envrc)\n", out)
        projects = self.registry()["projects"]
        self.assertEqual(projects["api"], {"roots": [str(acme)], "aws": {"profile": "acme-dev", "account": "111111111111"}})
        self.assertEqual(projects["globex-api"], {"roots": [str(globex)],
                                                  "aws": {"profile": "globex", "account": "222222222222"}})
        os.chdir(globex)
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(main(["which", "-v"]), 0)
        self.assertRegex(out.getvalue(), r"^globex-api  .*\n  aws +globex → …2222\n$")

    def test_an_explicit_name_adds_the_folder_to_that_project_and_says_so(self):
        acme, old = self.api_of("acme"), self.api_of("acme-old")
        self.assertEqual(self.init(argv=["--yes", "--root", str(acme)])[0], 0)
        code, out, err = self.init(argv=["--yes", "--root", str(old), "--name", "api"])
        self.assertEqual(code, 0, err)
        self.assertIn(f"Added a second folder to project 'api' → {old} (it also has {acme}; every folder of api uses "
                      "the same accounts)\n", out)
        self.assertEqual(self.registry()["projects"]["api"]["roots"], [str(acme), str(old)])
        code, out, err = self.init(argv=["--yes", "--root", str(old)])  # registered already: nothing changes
        self.assertEqual(code, 0, err)
        self.assertIn(f"Registered project 'api' → {old}\n", out)
        self.assertEqual(self.registry()["projects"]["api"]["roots"], [str(acme), str(old)])


class MessagesTests(Machine):
    """What a first-time user sees when something is missing or mistyped: what is wrong and the next command,
    never a traceback."""

    def run_main(self, *argv, stdin=None):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err), mock.patch("sys.stdin", stdin or io.StringIO()):
            code = main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def write_registry(self, text):
        self.config.mkdir(exist_ok=True)
        (self.config / "registry.toml").write_text(text)

    def register(self):
        self.assertEqual(self.run_main("init", "--yes")[0], 0)

    def test_registry_that_is_not_toml(self):
        self.write_registry("[projects.acme\nroots = ['~/acme']\n")
        reg = re.escape(str(self.config / "registry.toml"))
        for argv in (["status"], ["which"], ["projects"], ["init", "--yes"], ["add", "aws", "--profile", "acme-dev"]):
            with self.subTest(argv=argv):
                code, out, err = self.run_main(*argv)
                self.assertEqual((code, out), (1, ""))
                self.assertRegex(err, rf"^keyfit: cannot use {reg}: .*\(at line 1, column \d+\)\n"
                                      rf"  → fix that line in {reg}\n$")

    def test_roots_must_be_a_list_of_folders(self):
        # read as a string, the root "~/acme" would have been "~", "/", "a", …: the home folder as a project
        self.write_registry(f'[projects.acme]\nroots = "{self.repo}"\n')
        code, out, err = self.run_main("which", str(self.home))
        self.assertEqual((code, out), (1, ""))
        self.assertIn('[projects.acme]: roots must be a list of folders, like roots = ["~/code/acme"]', err)
        self.write_registry('[projects.acme]\nroots = ["~"]\n')
        code, out, err = self.run_main("which", str(self.home))
        self.assertIn('roots holds "~", which would put every folder under it in acme', err)
        self.assertIn("→ remove that root from", err)

    def test_unusable_connections_fail_their_check_with_the_fix(self):
        self.write_registry(f'[projects.acme]\nroots = ["{self.repo}"]\ngithub = "octocta"\n'
                            'asw = { profile = "acme-dev" }\naws = { profile = "acme-dev" }\n\n'
                            '[accounts.github.octocat]\nlogin = "octocat"\n')
        code, out, err = self.run_main("status", "--offline")
        self.assertEqual((code, err), (1, ""))
        self.assertIn("✘ github     octocta   no account [accounts.github.octocta]\n"
                      "    → map it again: keyfit add github …", out)
        self.assertRegex(out, r"✘ asw\s+unknown connection \(a typo\? Keyfit knows aws, linear, claude, codex, opencode, copilot, "
                              r"gemini, github, vercel, supabase, cloudflare, convex, google, gcloud, firebase, google-mcp\)")
        self.assertIn("✘ aws        acme-dev → ?   the expected account must be a 12-digit AWS account ID", out)
        self.assertIn("  vercel won't start here until mapped: keyfit add vercel …\n", out)
        code, out, _ = self.run_main("which", "-v")
        self.assertEqual(code, 0)
        self.assertIn("  github     octocta\n", out)

    def test_status_outside_a_project_says_how_to_register(self):
        os.chdir(self.tmp)
        self.assertEqual(self.run_main("status"), (1, "not registered  (not inside a registered project)\n"
                                                      "  Tools run here unchanged. To register a repository, run "
                                                      "inside it: keyfit init\n", ""))
        code, _, err = self.run_main("add", "aws", "--profile", "acme-dev")
        self.assertEqual(code, 1)
        self.assertIn("  Run this inside the project's repository, name it with --project NAME, or register it: "
                      "keyfit init\n", err)

    def test_add_aws_names_the_profiles_there_are(self):
        self.register()
        code, _, err = self.run_main("add", "aws", "--profile", "acme-prod")
        self.assertEqual((code, err), (1, "no AWS profile named 'acme-prod' (named profiles in ~/.aws/config: "
                                          "acme-dev, globex)\n  Create it first: aws configure sso --profile acme-prod\n"))
        code, _, err = self.run_main("add", "aws", "--profile", "default")
        self.assertEqual((code, err), (1, "use a named profile, never `default` (named profiles in ~/.aws/config: "
                                          "acme-dev, globex)\n"))

    def test_add_aws_says_what_next_when_aws_has_no_answer(self):
        self.register()
        executable(self.bin / "aws", "#!/bin/sh\necho 'An error occurred (AccessDenied) when calling it' >&2\nexit 255\n")
        code, _, err = self.run_main("add", "aws", "--profile", "acme-dev")
        self.assertEqual((code, err), (1, "could not read the account: An error occurred (AccessDenied) when calling it\n"
                                          "  → KEYFIT_DISABLE=1 aws sts get-caller-identity --profile acme-dev   "
                                          "(shows the full error)\n"))
        (self.bin / "aws").unlink()
        code, _, err = self.run_main("add", "aws", "--profile", "acme-dev")
        self.assertEqual(err, "could not read the account: aws CLI not found on PATH\n  → brew install awscli\n")

    def test_add_without_the_tool_says_how_to_install_it(self):
        self.register()
        for name in ("gh", "claude"):
            (self.bin / name).unlink()
        code, _, err = self.run_main("add", "github", "--login", "octocat")
        self.assertEqual((code, err), (1, "gh not found on PATH — install it (brew install gh), then sign in: "
                                          "gh auth login --hostname github.com --web\n"))
        code, _, err = self.run_main("add", "claude")
        self.assertEqual((code, err), (1, "could not ask Claude Code: claude not found on PATH\n"
                                          "  Install it: curl -fsSL https://claude.ai/install.sh | bash\n"))

    def test_add_linear_says_what_next(self):
        self.register()
        code, _, err = self.run_main("add", "linear", "--key", "env:LINEAR_ACME_API_KEY")
        self.assertEqual((code, err), (1, "$LINEAR_ACME_API_KEY is empty or missing in this shell.\n  Set it first, or "
                                          "paste the key instead (stored in the Keychain): keyfit add linear\n"))
        wrong = "lin_api_" + "W9" * 20  # well-formed, but Linear does not know it: nothing is stored
        code, _, err = self.run_main("add", "linear", stdin=io.StringIO(wrong + "\n"))
        self.assertEqual((code, err), (1, "could not read the workspace: Linear rejected the key\n  → create a key in "
                                          "Linear → Settings → Security & access → Personal API keys, then: "
                                          "keyfit add linear\n"))
        self.assertFalse((self.stub / "argv.log").exists())  # the Keychain was never asked
        self.assertNotIn(wrong, err)


# Stands in for /usr/bin/security in doctor: answers only `list-keychains`, logs every argv.
FAKE_SECURITY_LIST = """#!/bin/sh
echo "$*" >> "$(dirname "$0")/doctor-argv.log"
if [ "$*" = list-keychains ] && [ -z "$KEYCHAIN_DOWN" ]; then
  echo '    "/Users/someone/Library/Keychains/login.keychain-db"'; exit 0
fi
echo "security: SecKeychainCopySearchList: User interaction is not allowed." >&2; exit 1
"""


class DoctorTests(Machine):
    def setUp(self):
        super().setUp()
        executable(self.stub / "security", FAKE_SECURITY_LIST)
        self.shims = self.config / "bin"

    def doctor(self, *path, cwd=None):
        os.environ["PATH"] = os.pathsep.join(str(p) for p in (*path, "/usr/bin", "/bin"))
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(out):
            if cwd:
                os.chdir(cwd)
            code = main(["doctor"])
        return code, out.getvalue()

    def setup_shims(self):
        with redirect_stdout(io.StringIO()):
            self.assertEqual(main(["setup"]), 0)

    def register(self):
        with redirect_stdout(io.StringIO()):
            self.assertEqual(main(["init", "--yes"]), 0)

    def test_everything_in_place(self):
        self.setup_shims()
        self.register()
        code, out = self.doctor(self.shims, self.bin)
        self.assertEqual(code, 0, out)
        lines = out.splitlines()
        self.assertEqual([line.split()[1] for line in lines[:-1]],
                         ["macOS", "python", "shims", "PATH", "claude", "codex", "opencode", "copilot", "gemini", "aws", "gh",
                          "vercel", "supabase", "wrangler", "convex", "gcloud", "firebase", "git", "registry", "keychain",
                          "project"])
        self.assertTrue(all(line.startswith("  ✔ ") for line in lines[:-1]), out)
        self.assertIn("✔ git        /usr/bin/git   git version ", out)
        self.assertIn("first, before every real tool", out)
        self.assertIn(f"✔ gh         {self.bin / 'gh'}\n", out)
        self.assertIn(f"✔ vercel     {self.bin / 'vercel'}\n", out)
        self.assertIn(f"✔ registry   {self.config / 'registry.toml'}   1 project\n", out)
        self.assertRegex(out, r"✔ project\s+acme\s")
        self.assertEqual(lines[-1], "All good.")
        self.assertEqual((self.stub / "doctor-argv.log").read_text(), "list-keychains\n")  # never reads an item

    def test_right_after_superset(self):
        self.setup_shims()
        superset = self.home / ".superset" / "bin"
        superset.mkdir(parents=True)
        executable(superset / "claude", "#!/bin/sh\n")
        code, out = self.doctor(superset, self.shims, self.bin)
        self.assertEqual(code, 0, out)
        self.assertIn("right after Superset's agent wrapper, before every real tool", out)
        self.assertIn(f"✔ claude     {self.bin / 'claude'}\n", out)  # the wrapper is not the real tool

    def test_wrong_orders_fail_with_the_fix(self):
        self.setup_shims()
        superset = self.home / ".superset" / "bin"
        superset.mkdir(parents=True)
        code, out = self.doctor(self.bin, self.shims)
        self.assertEqual(code, 1)
        self.assertIn(f"{self.bin} comes first: claude, codex, opencode, copilot, gemini, aws, gh, vercel, supabase, wrangler, convex, gcloud, firebase would run without Keyfit", out)
        self.assertIn("→ make Keyfit's line (`…  # keyfit: …`) the last line of your shell startup file, "
                      "then open a new terminal", out)
        code, out = self.doctor(self.shims, superset, self.bin)
        self.assertEqual(code, 1)
        self.assertIn("comes before Superset's agent wrapper (~/.superset/bin)", out)
        self.assertEqual(out.splitlines()[-1], "1 problem — the fix is under each ✘")

    def test_not_on_path(self):
        self.setup_shims()
        code, out = self.doctor(self.bin)
        self.assertEqual(code, 1)
        self.assertRegex(out, r"✘ PATH .* not on PATH\n    → keyfit setup --rc, then open a new terminal\n")
        with redirect_stdout(io.StringIO()):
            main(["setup", "--shell", "bash", "--rc"])
        code, out = self.doctor(self.bin)
        self.assertIn("→ open a new terminal (~/.bash_profile loads Keyfit)", out)
        profile = self.home / ".bash_profile"  # the line as an earlier version wrote it counts too
        profile.write_text(profile.read_text().replace("# keyfit: tool shims on PATH", cli.OLD_COMMENT))
        code, out = self.doctor(self.bin)
        self.assertIn("→ open a new terminal (~/.bash_profile loads Keyfit)", out)
        code, out = self.doctor(self.bin, self.shims)
        self.assertIn("→ make Keyfit's line (`…  # keyfit: …`) the last line of ~/.bash_profile", out)

    def test_missing_or_stale_shims(self):
        code, out = self.doctor(self.shims, self.bin)
        self.assertEqual(code, 1)
        self.assertRegex(out, r"✘ shims .* not written yet\n    → keyfit setup")
        self.setup_shims()
        (self.shims / "gh").write_text('#!/bin/sh\nexec "/nowhere/python3" -I "/nowhere/_shim_entry.py" gh "$@"\n')
        code, out = self.doctor(self.shims, self.bin)
        self.assertEqual(code, 1)
        self.assertIn("gh runs /nowhere/python3, which no longer exists", out)

    def test_missing_tool_is_only_a_warning(self):
        self.setup_shims()
        (self.bin / "gh").unlink()
        code, out = self.doctor(self.shims, self.bin)
        self.assertEqual(code, 0, out)
        self.assertIn("  ! gh         not found on PATH   needed only if you use it\n    → brew install gh\n", out)
        (self.bin / "vercel").unlink()
        code, out = self.doctor(self.shims, self.bin)
        self.assertIn("  ! vercel     not found on PATH   needed only if you use it\n    → npm install -g vercel\n", out)

    def test_unreadable_registry_and_keychain_fail(self):
        self.setup_shims()
        self.config.mkdir(exist_ok=True)
        (self.config / "registry.toml").write_text("[projects.acme\nroots = 1\n")
        os.environ["KEYCHAIN_DOWN"] = "1"
        code, out = self.doctor(self.shims, self.bin)
        self.assertEqual(code, 1)
        self.assertRegex(out, r"✘ registry .* unreadable \(.*line 1.*\)\n")
        self.assertIn(f"    → fix that line in {self.config / 'registry.toml'}\n", out)
        self.assertIn("✘ keychain   macOS Keychain   security: SecKeychainCopySearchList", out)
        self.assertNotIn("project", out)  # no registry, no project to resolve
        self.assertEqual(out.splitlines()[-1], "2 problems — the fix is under each ✘")

    def test_unregistered_folder_is_a_note(self):
        self.setup_shims()
        code, out = self.doctor(self.shims, self.bin)  # a git repository: register it
        self.assertEqual(code, 0, out)
        self.assertRegex(out, r"! project\s+this folder\s+not registered .*\n    → keyfit init\n")
        code, out = self.doctor(self.shims, self.bin, cwd=self.tmp)  # not one (the home folder, say): find one first
        self.assertEqual(code, 0, out)
        self.assertRegex(out, r"! project\s+this folder\s+not registered .*\n"
                              r"    → cd into a project's repository, then: keyfit init\n")

    def test_other_systems_fail_clearly(self):
        self.setup_shims()
        with mock.patch.object(sys, "platform", "linux"):
            code, out = self.doctor(self.shims, self.bin)
        self.assertEqual(code, 1)
        self.assertIn("✘ macOS      linux   Keyfit runs on macOS only for now", out)
        self.assertIn("✘ keychain   macOS Keychain   Keyfit stores keys in the macOS Keychain", out)

    def test_never_prints_secret_values(self):
        self.setup_shims()
        self.register()
        os.environ["LINEAR_ACME_API_KEY"] = KEY
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(main(["add", "linear", "--key", "env:LINEAR_ACME_API_KEY"]), 0)
        code, out = self.doctor(self.shims, self.bin)
        self.assertEqual(code, 0, out)
        self.assertNotIn(KEY, out)
        self.assertNotIn("find-generic-password", (self.stub / "doctor-argv.log").read_text())


if __name__ == "__main__":
    unittest.main()
