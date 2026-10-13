"""End-to-end: shims written by `keyfit setup`, run as real processes in temporary folders."""

import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from keyfit import mcp
from keyfit.cli import main
from keyfit.registry import LEGACY_KINDS, Project, Registry
from keyfit.shim import adjust_args, subcommand, CLAUDE_SUBCOMMANDS, CODEX_SUBCOMMANDS

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
print(json.dumps({"argv": args, "ANTHROPIC_API_KEY": os.environ.get("ANTHROPIC_API_KEY")}))
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
