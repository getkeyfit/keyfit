"""End-to-end: shims written by `keyfit setup`, run as real processes in temporary folders."""

import contextlib
import io
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from keyfit import mcp, secrets
from keyfit.cli import main
from keyfit.registry import LEGACY_KINDS, Project, Registry
from keyfit.shim import Refused, adjust_args, sign_in_command, subcommand, CLAUDE_SUBCOMMANDS, CODEX_SUBCOMMANDS

os.environ["KEYFIT_CONFIG_DIR"] = tempfile.mkdtemp()  # tests never touch the real ~/.config/keyfit

FAKE_AWS = """#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
if args[:2] == ["sts", "get-caller-identity"]:
    with open(os.environ["STS_COUNT"], "a") as fh: fh.write("x")
    print(os.environ.get("FAKE_ACCOUNT", "444444444444")); sys.exit(0)
print(json.dumps({"argv": args, "AWS_PROFILE": os.environ.get("AWS_PROFILE"),
                  "AWS_ACCESS_KEY_ID": os.environ.get("AWS_ACCESS_KEY_ID"),
                  "KEYFIT_PROJECT": os.environ.get("KEYFIT_PROJECT")}))
"""

FAKE_CLAUDE = """#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
if args[:3] == ["auth", "status", "--json"]:
    folder = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.environ["HOME"], ".claude")
    print(json.dumps({"loggedIn": True, "authMethod": "claude.ai", "email": "me@work.io", "orgId": "org-work",
                      "orgName": "Work", "subscriptionType": "team", "configDirectory": folder})); sys.exit(0)
print(json.dumps({"argv": args, "ANTHROPIC_API_KEY": os.environ.get("ANTHROPIC_API_KEY"),
                  "CLAUDE_CODE_DISABLE_AGENT_VIEW": os.environ.get("CLAUDE_CODE_DISABLE_AGENT_VIEW")}))
"""


# Signed out until `claude auth login` has run once (it leaves the file $SIGNED_IN behind).
FAKE_CLAUDE_SIGNED_OUT = """#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
marker = os.environ["SIGNED_IN"]
folder = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.environ["HOME"], ".claude")
if args[:2] == ["auth", "login"]:
    open(marker, "w").close(); print("fake sign-in done"); sys.exit(0)
if args[:3] == ["auth", "status", "--json"]:
    if not os.path.exists(marker):
        print(json.dumps({"loggedIn": False, "authMethod": "none", "configDirectory": folder})); sys.exit(1)
    print(json.dumps({"loggedIn": True, "authMethod": "claude.ai", "email": "me@work.io", "orgId": "org-work",
                      "orgName": "Work", "subscriptionType": "team", "configDirectory": folder})); sys.exit(0)
print(json.dumps({"argv": args}))
"""

def executable(path: Path, text: str) -> None:
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


class ShimTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.home = self.tmp / "home"
        (self.home / ".aws").mkdir(parents=True)
        (self.home / ".aws" / "config").write_text(
            "[profile iam]\ncredential_process = granted credential-process --profile=iam\n"
            "[profile other]\ncredential_process = granted credential-process --profile=other\n")
        (self.home / ".claude").mkdir()
        (self.home / ".claude.json").write_text(json.dumps({"mcpServers": {
            "linear_a": {"type": "http", "url": "https://mcp.linear.app/mcp", "headers": {"Authorization": "Bearer ${LINEAR_A_API_KEY}"}},
            "linear_b": {"type": "http", "url": "https://mcp.linear.app/mcp", "headers": {"Authorization": "Bearer ${LINEAR_B_API_KEY}"}},
            "paper": {"type": "http", "url": "http://127.0.0.1:29979/mcp"}}}))
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        executable(self.bin / "aws", FAKE_AWS)
        executable(self.bin / "claude", FAKE_CLAUDE)
        self.config = self.tmp / "keyfit"
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=self.repo, check=True)
        self.outside = self.tmp / "outside"
        self.outside.mkdir()
        self.sts = self.tmp / "sts-count"
        self.sts.write_text("")

        old = os.environ.get("KEYFIT_CONFIG_DIR")
        os.environ["KEYFIT_CONFIG_DIR"] = str(self.config)
        try:
            main(["init", "--yes", "--root", str(self.repo), "--name", "demo"])
            reg = self.config / "registry.toml"
            reg.write_text(reg.read_text() + 'aws = { profile = "iam", account = "444444444444" }\n'
                           'claude = "work"\n\n[accounts.claude.work]\nconfig_dir = "' + str(self.home / ".claude") +
                           '"\nemail = "me@work.io"\norg_id = "org-work"\norg_name = "Work"\n'
                           '\n[settings]\nshared_aws_profiles = ["ci-worker"]\n')
            with mock.patch.object(sys, "platform", "darwin"):  # setup refuses elsewhere; the shims work anywhere
                main(["setup"])
        finally:
            if old is None:
                os.environ.pop("KEYFIT_CONFIG_DIR")
            else:
                os.environ["KEYFIT_CONFIG_DIR"] = old

    def run_tool(self, tool, *args, cwd=None, **extra):
        env = {"PATH": f"{self.config / 'bin'}{os.pathsep}{self.bin}{os.pathsep}/usr/bin:/bin",
               "HOME": str(self.home), "KEYFIT_CONFIG_DIR": str(self.config), "STS_COUNT": str(self.sts), **extra}
        out = subprocess.run([tool, *args], cwd=cwd or self.repo, env=env, capture_output=True, text=True, timeout=60)
        return out

    def run_in_terminal(self, tool, *args, answer: str, **extra):
        """Run the shim with a terminal on stdin and stderr, as when you type the command; answer the prompt."""
        import pty
        import select

        env = {"PATH": f"{self.config / 'bin'}{os.pathsep}{self.bin}{os.pathsep}/usr/bin:/bin",
               "HOME": str(self.home), "KEYFIT_CONFIG_DIR": str(self.config), "STS_COUNT": str(self.sts),
               "SIGNED_IN": str(self.tmp / "signed-in"), **extra}
        master, slave = pty.openpty()
        proc = subprocess.Popen([tool, *args], cwd=self.repo, env=env, stdin=slave, stdout=subprocess.PIPE,
                                stderr=slave)
        os.close(slave)
        seen, sent = b"", False
        while select.select([master], [], [], 30)[0]:
            try:
                chunk = os.read(master, 4096)
            except OSError:  # the other side closed: the shim and what it started are done
                break
            if not chunk:
                break
            seen += chunk
            if not sent and b"Sign in now? [Y/n] " in seen:
                os.write(master, answer.encode())
                sent = True
        proc.wait(timeout=30)
        os.close(master)
        return proc.returncode, proc.stdout.read().decode(), seen.decode().replace("\r\n", "\n")

    def test_expired_sign_in_is_offered_and_then_the_tool_starts(self):
        executable(self.bin / "claude", FAKE_CLAUDE_SIGNED_OUT)
        code, out, terminal = self.run_in_terminal("claude", "-p", "hi", answer="\n")
        self.assertEqual(code, 0, terminal)
        self.assertIn("✘ claude     Work   not signed in\n    → claude auth login\n  Sign in now? [Y/n] ", terminal)
        self.assertIn("fake sign-in done", out)  # the sign-in ran, with the terminal's stdin
        self.assertIn("keyfit: signed in, starting claude\n", terminal)
        self.assertEqual(json.loads(out.splitlines()[-1])["argv"][-2:], ["-p", "hi"])

    def test_declined_sign_in_starts_nothing(self):
        executable(self.bin / "claude", FAKE_CLAUDE_SIGNED_OUT)
        code, out, terminal = self.run_in_terminal("claude", "-p", "hi", answer="n\n")
        self.assertEqual((code, out), (1, ""))
        self.assertFalse((self.tmp / "signed-in").exists())
        self.assertIn("  (bypass once: KEYFIT_DISABLE=1 claude …)", terminal)

    def test_no_terminal_no_question(self):
        # agents and scripts: nothing may wait for an answer that cannot come
        executable(self.bin / "claude", FAKE_CLAUDE_SIGNED_OUT)
        out = self.run_tool("claude", "-p", "hi", SIGNED_IN=str(self.tmp / "signed-in"))
        self.assertEqual((out.returncode, out.stdout), (1, ""))
        self.assertNotIn("Sign in now?", out.stderr)
        self.assertTrue(out.stderr.endswith("    → claude auth login\n  (bypass once: KEYFIT_DISABLE=1 claude …)\n"))

    def test_no_question_inside_a_session(self):
        # agents run commands in a terminal of their own (KEYFIT_PROJECT set): nobody is there to answer
        with open(self.home / ".aws" / "config", "a") as fh:
            fh.write("[profile web]\nlogin_session = arn:aws:iam::444444444444:user/me\n")
        registry = self.config / "registry.toml"
        registry.write_text(registry.read_text().replace('profile = "iam"', 'profile = "web"'))
        executable(self.bin / "claude", FAKE_CLAUDE_SIGNED_OUT)
        for tool, args, fix in (("aws", ("s3", "ls"), "aws login --profile web"),
                                ("claude", ("-p", "hi"), "claude auth login")):
            with self.subTest(tool):
                code, out, terminal = self.run_in_terminal(tool, *args, answer="\n", KEYFIT_PROJECT="demo")
                self.assertEqual((code, out), (1, ""), terminal)
                self.assertNotIn("Sign in now?", terminal)
                self.assertIn(f"    → {fix}\n  (bypass once: KEYFIT_DISABLE=1 {tool} …)\n", terminal)
        self.assertFalse((self.tmp / "signed-in").exists())

    def test_only_plain_sign_ins_are_offered(self):
        for fix, words in [
                ("aws login --profile acme", ["aws", "login", "--profile", "acme"]),
                ("aws sso login --sso-session corp", ["aws", "sso", "login", "--sso-session", "corp"]),
                ("CLAUDE_CONFIG_DIR=/k/claude/w claude auth login", ["claude", "auth", "login"]),
                ("CODEX_HOME=/k/codex codex login", ["codex", "login"]),
                ('vercel login --global-config "/k/vercel/a b"', ["vercel", "login", "--global-config", "/k/vercel/a b"])]:
            self.assertEqual(sign_in_command(fix)[1], words, fix)
        for fix in ("gh auth login --hostname github.com --git-protocol ssh --web",  # switches gh's active account
                    "claude auth login  then: keyfit add claude --name w", "aws login --profile",
                    "aws login --profile --debug", "AWS_PROFILE=x aws login --profile y", "CODEX_HOME=/x claude auth login",
                    "rm -r .vercel, then link it again: vercel link", "aws configure --profile p", "keyfit add claude", ""):
            self.assertIsNone(sign_in_command(fix), fix)

    def test_registered_folder_gets_project_environment(self):
        out = self.run_tool("aws", "s3", "ls", AWS_ACCESS_KEY_ID="inherited", AWS_PROFILE="other")
        self.assertEqual(out.returncode, 0, out.stderr)
        seen = json.loads(out.stdout)
        self.assertEqual(seen["AWS_PROFILE"], "iam")
        self.assertIsNone(seen["AWS_ACCESS_KEY_ID"])
        self.assertEqual(seen["KEYFIT_PROJECT"], "demo")
        self.assertEqual(seen["argv"], ["s3", "ls"])

    def test_successful_check_is_cached(self):
        self.run_tool("aws", "s3", "ls")
        self.run_tool("aws", "s3", "ls")
        self.assertEqual(self.sts.read_text(), "x")  # one STS call for two launches

    def keyfit(self, *args, **extra):
        """`keyfit …` from this checkout in its own process, with the shims' environment."""
        env = {"PATH": f"{self.config / 'bin'}{os.pathsep}{self.bin}{os.pathsep}/usr/bin:/bin",
               "HOME": str(self.home), "KEYFIT_CONFIG_DIR": str(self.config), "STS_COUNT": str(self.sts), **extra}
        code = "import sys; sys.path.insert(0, sys.argv.pop(1)); from keyfit.cli import main; sys.exit(main(sys.argv[1:]))"
        return subprocess.run([sys.executable, "-I", "-c", code, str(Path(__file__).resolve().parent.parent), *args],
                              cwd=self.repo, env=env, capture_output=True, text=True, timeout=60)

    def test_a_failed_check_is_not_overruled_by_the_success_cached_before(self):
        self.assertEqual(self.run_tool("aws", "s3", "ls").returncode, 0)  # checked and cached
        status = self.keyfit("status", FAKE_ACCOUNT="999999999999")  # the profile now signs in to another account
        self.assertEqual(status.returncode, 1, status.stdout)
        self.assertIn("✘ aws", status.stdout)
        out = self.run_tool("aws", "s3", "ls", FAKE_ACCOUNT="999999999999")
        self.assertEqual((out.returncode, out.stdout), (1, ""))
        self.assertIn("signed in to account …9999, project expects …4444", out.stderr)

    def test_offline_fallback_is_said_for_every_tool(self):
        self.run_tool("aws", "s3", "ls")  # checked and cached
        cached = self.config / "cache" / "checks.json"
        entries = json.loads(cached.read_text())
        for entry in entries.values():
            entry["at"] -= 3600  # an hour ago: no longer fresh, trusted for a day when AWS cannot be reached
        cached.write_text(json.dumps(entries))
        executable(self.bin / "aws", FAKE_AWS.replace('print(os.environ.get("FAKE_ACCOUNT", "444444444444")); sys.exit(0)',
                                                      'sys.exit("Could not connect to the endpoint URL")'))
        out = self.run_tool("aws", "s3", "ls")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(json.loads(out.stdout)["AWS_PROFILE"], "iam")
        self.assertIn("keyfit: ! aws", out.stderr)
        self.assertIn("account confirmed 1h 0m ago · could not re-check now (Could not connect to the endpoint URL)",
                      out.stderr)

    def test_failing_git_stops_tools_in_a_repository_but_not_outside(self):
        # git broken by a macOS update (Xcode's licence not accepted): the repository may be a project's
        executable(self.bin / "git", "#!/bin/sh\necho 'You have not agreed to the Xcode license agreements.' >&2\n"
                                     "exit 69\n")
        for tool, args in (("claude", ("-p", "hi")), ("aws", ("s3", "ls"))):
            with self.subTest(tool):
                out = self.run_tool(tool, *args, AWS_PROFILE="other")
                self.assertEqual((out.returncode, out.stdout), (1, ""))
                self.assertIn(f"keyfit: {tool} not started: git failed in {self.repo} (You have not agreed to the "
                              f"Xcode license agreements.), so Keyfit cannot tell which project that folder belongs "
                              f"to.\n  Fix git (keyfit doctor shows how), or bypass once: KEYFIT_DISABLE=1 {tool} …\n",
                              out.stderr)
        out = self.run_tool("aws", "s3", "ls", cwd=self.outside, AWS_PROFILE="other")  # no .git here or above
        self.assertEqual(json.loads(out.stdout)["AWS_PROFILE"], "other")
        self.assertEqual(self.sts.read_text(), "")
        here = os.getcwd()
        os.chdir(self.repo)
        try:
            with mock.patch.dict(os.environ, {"PATH": f"{self.bin}{os.pathsep}/usr/bin:/bin", "HOME": str(self.home),
                                              "KEYFIT_CONFIG_DIR": str(self.config)}), \
                    mock.patch.object(secrets, "keychain_reachable", return_value=""), \
                    contextlib.redirect_stdout(io.StringIO()) as doctor:
                self.assertEqual(main(["doctor"]), 1)
        finally:
            os.chdir(here)
        self.assertIn(f"  ✘ git        {self.bin / 'git'}   You have not agreed to the Xcode license agreements. · "
                      f"tools do not start inside git repositories\n    → sudo xcodebuild -license accept\n",
                      doctor.getvalue())
        self.assertIn(f"  ✘ project    this folder   git failed in {self.repo} (You have not agreed", doctor.getvalue())
        self.assertNotIn("All good", doctor.getvalue())

    def test_unregistered_folder_passes_through(self):
        out = self.run_tool("aws", "s3", "ls", cwd=self.outside, AWS_PROFILE="other")
        self.assertEqual(json.loads(out.stdout)["AWS_PROFILE"], "other")
        self.assertEqual(self.sts.read_text(), "")

    def test_wrong_account_blocks_and_explains(self):
        out = self.run_tool("aws", "s3", "ls", FAKE_ACCOUNT="999999999999")
        self.assertEqual(out.returncode, 1)
        self.assertEqual(out.stdout, "")  # the real command never ran
        self.assertIn("aws not started, demo needs attention", out.stderr)
        self.assertIn("…9999", out.stderr)

    def test_option_value_that_looks_like_a_sign_in_command_still_runs_the_checks(self):
        out = self.run_tool("claude", "--add-dir", "auth", FAKE_ACCOUNT="999999999999")
        self.assertEqual(out.returncode, 1)
        self.assertEqual(out.stdout, "")  # the session never started
        self.assertIn("claude not started, demo needs attention", out.stderr)

    def test_other_profile_is_refused(self):
        out = self.run_tool("aws", "--profile", "other", "s3", "ls")
        self.assertEqual(out.returncode, 1)
        self.assertIn("refusing --profile other", out.stderr)

    def test_abbreviated_profile_option_is_refused_too(self):
        # the AWS CLI's argparse accepts any unambiguous prefix of --profile
        for args in (["--prof", "other", "s3", "ls"], ["s3", "ls", "--pro=other"], ["--p", "other", "s3", "ls"]):
            out = self.run_tool("aws", *args)
            self.assertEqual(out.returncode, 1, args)
            self.assertEqual(out.stdout, "")
            self.assertIn("refusing --profile other", out.stderr)
        self.assertEqual(self.run_tool("aws", "--prof", "iam", "s3", "ls").returncode, 0)

    def test_shared_profile_runs_unchanged(self):
        out = self.run_tool("aws", "--profile", "ci-worker", "s3", "ls", FAKE_ACCOUNT="999999999999")
        self.assertEqual(out.returncode, 0, out.stderr)
        seen = json.loads(out.stdout)
        self.assertEqual(seen["argv"], ["--profile", "ci-worker", "s3", "ls"])
        self.assertIsNone(seen["KEYFIT_PROJECT"])  # no project environment
        self.assertEqual(self.sts.read_text(), "")  # and no project check

    def test_shared_profile_does_not_cover_another(self):
        out = self.run_tool("aws", "--profile", "ci-worker", "--profile=other", "s3", "ls")
        self.assertEqual(out.returncode, 1)
        self.assertEqual(out.stdout, "")  # the real command never ran
        self.assertIn("refusing --profile", out.stderr)

    def test_dry_run_prints_a_shared_profile_instead_of_running_it(self):
        for cwd in (self.repo, self.outside):  # decided before the folder is looked at
            out = self.run_tool("aws", "--profile", "ci-worker", "s3", "ls", cwd=cwd, KEYFIT_DRY_RUN="1")
            self.assertEqual((out.returncode, out.stdout), (0, ""), out.stderr)  # the real command never ran
            self.assertEqual(out.stderr, "keyfit: AWS profile ci-worker is shared ([settings] shared_aws_profiles): "
                                         "aws runs without a project's accounts or checks\n"
                                         f"  would run: {self.bin / 'aws'} --profile ci-worker s3 ls\n"
                                         "  environment changes (names only): none\n")

    def test_dry_run_prints_a_checked_start_instead_of_running_it(self):
        out = self.run_tool("aws", "s3", "ls", KEYFIT_DRY_RUN="1")
        self.assertEqual((out.returncode, out.stdout), (0, ""), out.stderr)
        self.assertIn(f"keyfit: project demo, checks ok\n  would run: {self.bin / 'aws'} s3 ls\n"
                      "  environment changes (names only): ", out.stderr)
        self.assertIn("AWS_PROFILE", out.stderr)

    def test_dry_run_still_runs_sign_in_commands(self):
        seen = json.loads(self.run_tool("aws", "sso", "login", KEYFIT_DRY_RUN="1").stdout)
        self.assertEqual((seen["argv"], seen["AWS_PROFILE"]), (["sso", "login"], "iam"))

    def test_dry_run_still_runs_in_an_unregistered_folder(self):
        seen = json.loads(self.run_tool("aws", "s3", "ls", cwd=self.outside, KEYFIT_DRY_RUN="1").stdout)
        self.assertEqual((seen["argv"], seen["KEYFIT_PROJECT"]), (["s3", "ls"], None))

    def test_dry_run_still_runs_with_keyfit_disabled(self):
        out = self.run_tool("aws", "--profile", "other", "s3", "ls", KEYFIT_DRY_RUN="1", KEYFIT_DISABLE="1")
        self.assertEqual(json.loads(out.stdout)["argv"], ["--profile", "other", "s3", "ls"])
        self.assertEqual(self.sts.read_text(), "")  # unchecked

    def test_shared_profiles_come_only_from_settings(self):
        reg = self.config / "registry.toml"
        reg.write_text(reg.read_text().replace('shared_aws_profiles = ["ci-worker"]', "shared_aws_profiles = []"))
        out = self.run_tool("aws", "--profile", "ci-worker", "s3", "ls")
        self.assertEqual(out.returncode, 1)
        self.assertIn("refusing --profile ci-worker", out.stderr)

    def test_legacy_connections_are_ignored(self):
        # registries from earlier versions may still map connection kinds Keyfit no longer supports
        reg = self.config / "registry.toml"
        legacy = "".join(f'{kind} = {{ id = "x", sets = {{ testing = {{ token = "keychain:s/a" }} }} }}\n'
                         for kind in LEGACY_KINDS)
        reg.write_text(reg.read_text().replace('claude = "work"\n', 'claude = "work"\n' + legacy))
        out = self.run_tool("claude", "-p", "hello")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stderr, "")  # launched without warnings
        out = self.run_tool(sys.executable, "-m", "keyfit", "status", "--offline",
                            PYTHONPATH=str(Path(__file__).resolve().parents[1]))
        self.assertEqual(out.stdout.count("no longer supported"), 1, out.stdout)
        self.assertNotIn("not supported yet", out.stdout)
        self.assertIn(LEGACY_KINDS[0], reg.read_text())  # the user's entries are left for them to remove

    def test_registry_mistake_stops_the_tool_and_names_the_place(self):
        reg = self.config / "registry.toml"
        reg.write_text("[projects.demo\n" + reg.read_text())
        for cwd in (self.repo, self.outside):  # unreadable: no folder can be told apart, so nothing starts unchecked
            out = self.run_tool("claude", "-p", "hi", cwd=cwd)
            self.assertEqual((out.returncode, out.stdout), (1, ""))
            self.assertRegex(out.stderr, rf"^keyfit: claude not started, {re.escape(str(reg))} has a mistake\n  ✘ .*"
                                         rf"\(at line 1, column \d+\)\n    → fix that line in {re.escape(str(reg))}\n"
                                         r"  \(bypass once: KEYFIT_DISABLE=1 claude …\)\n$")

    def test_unusable_connection_fails_only_its_project(self):
        reg = self.config / "registry.toml"
        reg.write_text(reg.read_text().replace('claude = "work"', 'claude = "wrok"'))
        out = self.run_tool("claude", "-p", "hi")
        self.assertEqual(out.returncode, 1)
        self.assertIn("✘ claude     wrok   no account [accounts.claude.wrok]\n    → map it again: keyfit add claude …",
                      out.stderr)
        self.assertNotIn("internal error", out.stderr)
        self.assertEqual(self.run_tool("aws", "s3", "ls").returncode, 0)  # the project's aws has its own account
        self.assertEqual(self.run_tool("claude", "-p", "hi", cwd=self.outside).returncode, 0)  # other folders too
        reg.write_text(reg.read_text().replace("aws = {", "asw = {"))
        out = self.run_tool("claude", "-p", "hi")
        self.assertRegex(out.stderr, r"✘ asw\s+unknown connection \(a typo\? Keyfit knows aws, linear, claude, codex, opencode, copilot, "
                                     r"gemini, github, vercel, supabase, cloudflare, convex, google, gcloud, firebase, google-mcp\)\n    → rename or remove asw in ")

    def test_missing_tool_says_how_to_install_it(self):
        out = self.run_tool("gh", "pr", "list")
        self.assertEqual(out.returncode, 127)
        self.assertEqual(out.stderr, "keyfit: gh is not installed (no gh on PATH besides Keyfit's shim).\n"
                                     "  Install it: brew install gh   (keyfit doctor shows where each tool is found)\n")

    def test_unmapped_tool_names_the_command_that_maps_it(self):
        executable(self.bin / "gh", "#!/bin/sh\necho gh ran\n")
        out = self.run_tool("gh", "pr", "list")
        self.assertEqual(out.returncode, 1)
        self.assertEqual(out.stderr, "keyfit: project demo has no github account mapped, so gh was not started.\n"
                                     "  Map one: keyfit add github --login <login>   (bypass once: KEYFIT_DISABLE=1 gh …)\n")

    def test_login_commands_run_even_when_a_check_fails(self):
        out = self.run_tool("aws", "sso", "login", FAKE_ACCOUNT="999999999999")
        self.assertEqual(out.returncode, 0, out.stderr)
        seen = json.loads(out.stdout)
        self.assertEqual(seen["argv"], ["sso", "login"])
        self.assertEqual(seen["AWS_PROFILE"], "iam")  # signs in for the project's profile

    def test_disable_bypasses_once(self):
        out = self.run_tool("aws", "s3", "ls", KEYFIT_DISABLE="1", AWS_PROFILE="other")
        self.assertEqual(json.loads(out.stdout)["AWS_PROFILE"], "other")

    def test_session_pins_aws_to_its_project(self):
        out = self.run_tool("aws", "s3", "ls", cwd=self.outside, KEYFIT_PROJECT="demo", AWS_PROFILE="other")
        self.assertEqual(json.loads(out.stdout)["AWS_PROFILE"], "iam")

    def test_session_of_a_project_no_longer_registered_does_not_run_unchecked(self):
        out = self.run_tool("aws", "s3", "ls", KEYFIT_PROJECT="renamed", AWS_PROFILE="other")
        self.assertEqual(out.returncode, 1)
        self.assertEqual(out.stdout, "")
        self.assertIn("project renamed, which is not in the registry", out.stderr)

    def test_claude_session_hides_other_projects_linear_servers(self):
        out = self.run_tool("claude", "-p", "hello", ANTHROPIC_API_KEY="sk-ant-inherited")
        self.assertEqual(out.returncode, 0, out.stderr)
        seen = json.loads(out.stdout)
        self.assertIsNone(seen["ANTHROPIC_API_KEY"])
        self.assertEqual(seen["argv"][0], "--settings")
        settings = json.loads(seen["argv"][1])
        self.assertEqual(sorted(s["serverName"] for s in settings["deniedMcpServers"]), ["linear_a", "linear_b"])
        command = settings["statusLine"]["command"]  # Claude runs it through a shell
        self.assertTrue(command.endswith("keyfit/statusline.py"), command)
        line = subprocess.run(["/bin/sh", "-c", command], input="{}", capture_output=True, text=True,
                              env={"KEYFIT_SUMMARY": "demo · aws iam → …4444"}).stdout
        self.assertEqual(line, "keyfit · demo · aws iam → …4444\n")
        self.assertEqual(seen["argv"][2:], ["-p", "hello"])

    def test_claude_management_commands_are_untouched(self):
        out = self.run_tool("claude", "mcp", "list")
        self.assertEqual(json.loads(out.stdout)["argv"], ["mcp", "list"])

    def test_claude_session_starts_with_background_sessions_off(self):
        seen = json.loads(self.run_tool("claude", "-p", "hello").stdout)
        self.assertIs(json.loads(seen["argv"][1])["disableAgentView"], True)
        self.assertEqual(seen["CLAUDE_CODE_DISABLE_AGENT_VIEW"], "1")

    BACKGROUND = (["--bg", "x"], ["--background", "x"], ["agents"], ["respawn", "7c5dcf5d"], ["attach", "7c5dcf5d"],
                  ["daemon", "run"])

    def test_claude_background_commands_are_refused_in_a_project(self):
        for args in self.BACKGROUND:
            with self.subTest(args=args):
                out = self.run_tool("claude", *args)
                self.assertEqual((out.returncode, out.stdout), (1, ""))  # the real claude never started
                self.assertIn("keyfit: claude not started: `claude ", out.stderr)
                self.assertIn("background supervisor", out.stderr)
                self.assertIn("demo's accounts", out.stderr)
                self.assertIn("KEYFIT_DISABLE=1 claude", out.stderr)

    def test_claude_background_commands_are_refused_inside_a_session(self):
        for args in self.BACKGROUND:
            with self.subTest(args=args):
                out = self.run_tool("claude", *args, cwd=self.outside, KEYFIT_PROJECT="demo")
                self.assertEqual((out.returncode, out.stdout), (1, ""))
                self.assertIn("inside a session of demo", out.stderr)

    def test_claude_background_commands_run_unchanged_outside_projects(self):
        for args in self.BACKGROUND:
            with self.subTest(args=args):
                seen = json.loads(self.run_tool("claude", *args, cwd=self.outside).stdout)
                self.assertEqual(seen["argv"], args)
                self.assertIsNone(seen["CLAUDE_CODE_DISABLE_AGENT_VIEW"])
        seen = json.loads(self.run_tool("claude", "--bg", "x", KEYFIT_DISABLE="1").stdout)  # the bypass
        self.assertEqual(seen["argv"], ["--bg", "x"])

    def test_claude_daemon_status_and_stop_run_as_typed(self):
        for args in (["daemon", "status"], ["daemon", "logs"], ["daemon", "stop", "--any"]):
            with self.subTest(args=args):
                self.assertEqual(json.loads(self.run_tool("claude", *args).stdout)["argv"], args)

    CODEX_SESSIONS = ([], ["fix it"], ["resume", "--last"], ["fork", "--last"])
    CODEX_SHARED = (["agents"], ["queue", "--thread", "t", "--message", "m"], ["remote-control"], ["app-server"])

    def fake_codex(self):
        executable(self.bin / "codex", '#!/usr/bin/env python3\nimport json, sys\nprint(json.dumps(sys.argv[1:]))\n')

    def test_codex_inside_a_session_outside_projects_runs_without_daemon(self):
        self.fake_codex()
        for args in self.CODEX_SESSIONS:
            with self.subTest(args=args):
                out = self.run_tool("codex", *args, cwd=self.outside, KEYFIT_PROJECT="demo")
                self.assertEqual(json.loads(out.stdout), ["--no-daemon", *args])
        out = self.run_tool("codex", "exec", "x", cwd=self.outside, KEYFIT_PROJECT="demo")
        self.assertEqual(json.loads(out.stdout), ["exec", "x"])

    def test_codex_shared_server_commands_are_refused_inside_a_session(self):
        self.fake_codex()
        for args in self.CODEX_SHARED:
            with self.subTest(args=args):
                out = self.run_tool("codex", *args, cwd=self.outside, KEYFIT_PROJECT="demo")
                self.assertEqual((out.returncode, out.stdout), (1, ""))  # the real codex never started
                self.assertIn(f"inside a session of demo, `codex {args[0]}` could start Codex's shared app server",
                              out.stderr)
                self.assertIn("KEYFIT_DISABLE=1 codex", out.stderr)

    def test_codex_outside_projects_and_sessions_is_unchanged(self):
        self.fake_codex()
        for args in self.CODEX_SESSIONS + self.CODEX_SHARED:
            with self.subTest(args=args):
                self.assertEqual(json.loads(self.run_tool("codex", *args, cwd=self.outside).stdout), args)


class ArgumentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        (self.tmp / ".codex").mkdir()
        (self.tmp / ".codex" / "config.toml").write_text(
            '[mcp_servers.linear_a]\nurl = "https://mcp.linear.app/mcp"\nbearer_token_env_var = "LINEAR_A_API_KEY"\n'
            '[mcp_servers.linear_b]\nurl = "https://mcp.linear.app/mcp"\nbearer_token_env_var = "LINEAR_B_API_KEY"\n'
            '[mcp_servers.buffer]\nurl = "https://mcp.buffer.com/mcp"\n')
        self.project = Project("demo", [str(self.tmp)], {"linear": {"workspace": "a", "key": "env:LINEAR_A_API_KEY"}})
        self.env = {"HOME": str(self.tmp)}

    def test_subcommand_detection(self):
        self.assertEqual(subcommand(["-m", "o3", "exec", "x"], CODEX_SUBCOMMANDS), "exec")
        self.assertIsNone(subcommand(["-p", "update"], CLAUDE_SUBCOMMANDS))
        self.assertIsNone(subcommand(["fix the update script"], CLAUDE_SUBCOMMANDS))

    def test_codex_tui_disables_other_linear_and_runs_without_daemon(self):
        args = adjust_args("codex", self.project, ["fix it"], self.env, self.tmp)
        self.assertEqual(args, ["-c", "mcp_servers.linear_b.enabled=false", "--no-daemon", "fix it"])

    def test_codex_exec_has_no_daemon_flag(self):
        args = adjust_args("codex", self.project, ["exec", "x"], self.env, self.tmp)
        self.assertEqual(args, ["-c", "mcp_servers.linear_b.enabled=false", "exec", "x"])

    def test_codex_management_untouched(self):
        self.assertEqual(adjust_args("codex", self.project, ["login", "status"], self.env, self.tmp), ["login", "status"])

    def test_codex_resume_and_fork_run_without_daemon(self):
        for sub in ("resume", "fork"):
            args = adjust_args("codex", self.project, [sub, "--last"], self.env, self.tmp)
            self.assertEqual(args, ["-c", "mcp_servers.linear_b.enabled=false", "--no-daemon", sub, "--last"])

    def test_codex_shared_server_commands_are_refused(self):
        for args in (["agents"], ["queue", "--thread", "t", "--message", "m"], ["remote-control"], ["app-server"]):
            with self.subTest(args=args):
                with self.assertRaises(Refused) as refused:
                    adjust_args("codex", self.project, args, self.env, self.tmp)
                self.assertIn(f"`codex {args[0]}` works on Codex's shared app server", str(refused.exception))
                self.assertIn("KEYFIT_DISABLE=1 codex", str(refused.exception))

    def test_claude_daemon_commands_untouched(self):
        env = dict(self.env)
        self.assertEqual(adjust_args("claude", self.project, ["daemon", "status"], env, self.tmp), ["daemon", "status"])
        self.assertNotIn("CLAUDE_CODE_DISABLE_AGENT_VIEW", env)

    def test_unverified_mcp_json_linear_is_disabled(self):
        (self.tmp / ".mcp.json").write_text(json.dumps({"mcpServers": {"linear": {"url": "https://mcp.linear.app/mcp"}}}))
        settings = mcp.claude_settings(self.project, self.env, self.tmp)
        self.assertEqual(settings["disabledMcpjsonServers"], ["linear"])


class OwnedServerTests(unittest.TestCase):
    """Servers a project owns stay out of every other project; every other server stays visible everywhere."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        (self.tmp / ".codex").mkdir()
        (self.tmp / ".codex" / "config.toml").write_text(
            '[mcp_servers.auth_team]\nurl = "https://mcp.auth.example/mcp"\n'
            '[mcp_servers.globex_cloud_eu]\nurl = "https://globex.example/mcp"\n'
            '[mcp_servers.buffer]\nurl = "https://mcp.buffer.com/mcp"\n')
        self.env = {"HOME": str(self.tmp)}
        self.registry = Registry(self.tmp / "registry.toml")
        self.registry.add_project("globex", self.tmp / "globex")
        self.registry.add_mcp_servers("globex", ["globex_cloud_*"])
        self.registry.add_project("acme", self.tmp / "acme")

    def hidden_in(self, name):
        project = self.registry.get(name)
        args = adjust_args("codex", project, ["exec", "x"], self.env, self.tmp, self.registry.mcp_owners())
        return sorted(a.split(".")[1] for a in args if a.startswith("mcp_servers."))

    def test_owned_servers_only_in_their_project(self):
        self.assertEqual(self.hidden_in("globex"), [])
        self.assertEqual(self.hidden_in("acme"), ["globex_cloud_eu"])

    def test_server_owned_by_two_projects_is_visible_in_both(self):
        # a server signed in to a team that acme and initech share: both own it, globex stops seeing it
        self.registry.add_project("initech", self.tmp / "initech")
        for name in ("acme", "initech"):
            self.registry.add_mcp_servers(name, ["auth_team"])
        self.assertEqual(self.hidden_in("acme"), ["globex_cloud_eu"])
        self.assertEqual(self.hidden_in("initech"), ["globex_cloud_eu"])
        self.assertEqual(self.hidden_in("globex"), ["auth_team"])

    def test_codex_names_are_never_quoted(self):
        # Codex splits a -c key on every dot without quoting (tested with codex-cli 0.162.1): a quoted
        # name makes it refuse to start, while a bare name with an inner space is understood
        (self.tmp / ".codex" / "config.toml").write_text('[mcp_servers."globex cloud"]\nurl = "https://g.example/mcp"\n')
        self.registry.add_mcp_servers("globex", ["globex cloud"])
        args = adjust_args("codex", self.registry.get("acme"), ["exec", "x"], self.env, self.tmp, self.registry.mcp_owners())
        self.assertEqual(args[:2], ["-c", "mcp_servers.globex cloud.enabled=false"])

    def test_codex_refuses_when_a_server_cannot_be_hidden(self):
        for name in ("globex_cloud_e.u", "globex_cloud_e=u", "globex_cloud_eu "):  # all match globex_cloud_*
            with self.subTest(name=name):
                (self.tmp / ".codex" / "config.toml").write_text(f'[mcp_servers.{json.dumps(name)}]\nurl = "https://g.example/mcp"\n')
                with self.assertRaises(Refused) as refusal:
                    adjust_args("codex", self.registry.get("acme"), ["exec", "x"], self.env, self.tmp, self.registry.mcp_owners())
                self.assertIn("codex not started", str(refusal.exception))
        # the project that owns it starts normally: nothing has to be hidden there
        args = adjust_args("codex", self.registry.get("globex"), ["exec", "x"], self.env, self.tmp, self.registry.mcp_owners())
        self.assertEqual(args, ["exec", "x"])

    def test_registry_keeps_ownership_out_of_connections(self):
        self.registry.save()
        loaded = Registry.load(self.tmp / "registry.toml")
        self.assertEqual(loaded.get("globex").mcp_servers, ["globex_cloud_*"])
        self.assertNotIn("mcp_servers", loaded.get("globex").connections)
        self.assertEqual(loaded.mcp_owners(), {"globex": ["globex_cloud_*"]})
        self.assertEqual(loaded.add_mcp_servers("globex", ["globex_cloud_*", "globex_cognito_*"]),
                         ["globex_cloud_*", "globex_cognito_*"])

    def test_owned_server_in_another_projects_mcp_json_is_disabled(self):
        (self.tmp / ".mcp.json").write_text(json.dumps({"mcpServers": {
            "globex_cloud_eu": {"url": "https://globex.example/mcp"}, "docs": {"url": "https://docs.example/mcp"}}}))
        settings = mcp.claude_settings(self.registry.get("acme"), self.env, self.tmp, self.registry.mcp_owners())
        self.assertEqual(settings["disabledMcpjsonServers"], ["globex_cloud_eu"])


class GenericLinearServerTests(unittest.TestCase):
    """One Linear server reading LINEAR_API_KEY serves every project: Keyfit fills it with that project's key."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        (self.tmp / ".codex").mkdir()
        (self.tmp / ".codex" / "config.toml").write_text(
            '[mcp_servers.linear]\nurl = "https://mcp.linear.app/mcp"\nbearer_token_env_var = "LINEAR_API_KEY"\n'
            '[mcp_servers.linear_globex]\nurl = "https://mcp.linear.app/mcp"\nbearer_token_env_var = "LINEAR_GLOBEX_API_KEY"\n')
        (self.tmp / ".claude.json").write_text(json.dumps({"mcpServers": {
            "linear": {"type": "http", "url": "https://mcp.linear.app/mcp", "headers": {"Authorization": "Bearer ${LINEAR_API_KEY}"}}}}))
        self.env = {"HOME": str(self.tmp)}

    def hidden_in(self, project):
        codex = adjust_args("codex", project, ["exec", "x"], self.env, self.tmp)
        claude = mcp.claude_settings(project, self.env, self.tmp).get("deniedMcpServers", [])
        return (sorted(a.split(".")[1] for a in codex if a.startswith("mcp_servers.")),
                sorted(s["serverName"] for s in claude))

    def test_keychain_key_keeps_the_generic_server(self):
        acme = Project("acme", [str(self.tmp)], {"linear": {"workspace": "acme", "key": "keychain:keyfit/linear-acme"}})
        self.assertEqual(self.hidden_in(acme), (["linear_globex"], []))

    def test_env_key_keeps_both_its_own_and_the_generic_server(self):
        globex = Project("globex", [str(self.tmp)], {"linear": {"workspace": "globex", "key": "env:LINEAR_GLOBEX_API_KEY"}})
        self.assertEqual(self.hidden_in(globex), ([], []))

    def test_project_without_linear_sees_no_linear_server(self):
        plain = Project("plain", [str(self.tmp)], {})
        self.assertEqual(self.hidden_in(plain), (["linear", "linear_globex"], ["linear"]))


if __name__ == "__main__":
    unittest.main()
