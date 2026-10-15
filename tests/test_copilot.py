"""Copilot CLI: the adapter, its MCP hiding, `keyfit add copilot` and the copilot shim, with a fake gh and a fake
copilot. Copilot's real config folder (~/.copilot), gh's logins, the Keychain and the network are never touched: GitHub
is a stub in-process, and the shims run with HTTPS_PROXY on a closed local port and a warmed check cache."""

import contextlib
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ["KEYFIT_CONFIG_DIR"] = tempfile.mkdtemp()  # tests never touch the real ~/.config/keyfit

from keyfit import cache, environment, mcp  # noqa: E402
from keyfit.adapters import ADAPTERS  # noqa: E402
from keyfit.adapters.copilot import CopilotAdapter  # noqa: E402
from keyfit.cli import main  # noqa: E402
from keyfit.registry import Project, Registry  # noqa: E402
from keyfit.shim import Refused, adjust_args, copilot_options, sign_in_command  # noqa: E402
from test_vercel import executable  # noqa: E402

# Fake gh: only `auth token --hostname github.com --user <login>` is allowed, as Keyfit only reads gh's tokens.
FAKE_GH = """#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
with open(os.path.join(os.environ["HOME"], "gh-calls.log"), "a") as fh: fh.write(json.dumps(args) + "\\n")
if args[:5] != ["auth", "token", "--hostname", "github.com", "--user"]:
    sys.exit(97)
token = json.loads(os.environ.get("FAKE_GH_TOKENS", "{}")).get(args[5])
if not token: sys.exit(1)
print(token)
"""

# Fake copilot: prints its argv and the variables that decide its account, host and provider.
FAKE_COPILOT = """#!/usr/bin/env python3
import json, os, sys
print(json.dumps({"argv": sys.argv[1:], "cwd": os.getcwd(), "env": {k: os.environ.get(k) for k in (
    "COPILOT_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN", "GH_HOST", "COPILOT_GH_HOST", "COPILOT_PROVIDER_API_KEY",
    "COPILOT_PROVIDER_BASE_URL", "GITHUB_COPILOT_AGENT_GITHUB_TOKEN", "KEYFIT_COPILOT_LOGIN", "KEYFIT_PROJECT")}}))
"""

TOKENS = {"octocat": "gho_octocat", "hubot": "gho_hubot"}
WHO = {"gho_octocat": "octocat", "gho_hubot": "hubot", "github_pat_octocat": "octocat"}
LINEAR = {"type": "http", "url": "https://mcp.linear.app/mcp"}


def fetch(token, timeout):
    return (200, {"login": WHO[token]}) if token in WHO else (401, {})


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        executable(self.bin / "gh", FAKE_GH)
        self.adapter = CopilotAdapter(fetch=fetch)
        self.conn = {"name": "octocat", "login": "octocat"}
        config = mock.patch.dict(os.environ, {"KEYFIT_CONFIG_DIR": str(self.tmp / "keyfit")})  # a cache of its own
        config.start()
        self.addCleanup(config.stop)

    def env(self, tokens=None, **extra):
        return {"HOME": str(self.home), "PATH": f"{self.bin}{os.pathsep}/usr/bin:/bin",
                "FAKE_GH_TOKENS": json.dumps(TOKENS if tokens is None else tokens), **extra}

    def verify(self, conn=None, tokens=None, **extra):
        conn = conn or self.conn
        env = self.env(tokens, **extra)
        self.adapter.inject(conn, env)
        return env, self.adapter.verify(conn, env)

    def gh_calls(self):
        log = self.home / "gh-calls.log"
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    def test_inject_hands_copilot_ghs_token_for_the_login_and_scrubs_the_rest(self):
        inherited = {"COPILOT_GITHUB_TOKEN": "gho_other", "GITHUB_COPILOT_GITHUB_TOKEN": "x",
                     "GITHUB_COPILOT_AGENT_GITHUB_TOKEN": "x", "GITHUB_COPILOT_INFERENCE_TOKEN": "x",
                     "GITHUB_COPILOT_API_TOKEN": "x", "COPILOT_GH_HOST": "ghe.example", "GH_HOST": "ghe.example",
                     "COPILOT_PROVIDER_BASE_URL": "https://llm.example", "COPILOT_PROVIDER_API_KEY": "sk-x"}
        env = self.env(GH_TOKEN="gho_kept", GITHUB_TOKEN="kept", COPILOT_MODEL="gpt-5", **inherited)
        self.adapter.inject(self.conn, env)
        self.assertEqual((env["COPILOT_GITHUB_TOKEN"], env["KEYFIT_COPILOT_LOGIN"]), ("gho_octocat", "octocat"))
        self.assertFalse((set(inherited) - {"COPILOT_GITHUB_TOKEN"}) & set(env))
        # gh's own variables are the GitHub account's business; Copilot takes COPILOT_GITHUB_TOKEN before them
        self.assertEqual((env["GH_TOKEN"], env["GITHUB_TOKEN"], env["COPILOT_MODEL"]), ("gho_kept", "kept", "gpt-5"))
        self.assertTrue(all(call[:2] == ["auth", "token"] for call in self.gh_calls()))

    def test_confirmed(self):
        _, check = self.verify()
        self.assertEqual((check.status, check.label, check.detail), ("ok", "octocat", "login confirmed"))

    def test_a_fine_grained_token_is_fine(self):
        _, check = self.verify(tokens={"octocat": "github_pat_octocat"})
        self.assertEqual(check.status, "ok", check.detail)

    def test_no_login_in_gh_blocks_and_never_falls_back_to_the_inherited_token(self):
        env, check = self.verify(tokens={}, COPILOT_GITHUB_TOKEN="gho_hubot")
        self.assertNotIn("COPILOT_GITHUB_TOKEN", env)
        self.assertTrue(check.failed)
        self.assertEqual(check.detail, "gh has no stored login for octocat")
        self.assertIn("gh auth login --hostname github.com", check.fix)
        self.assertIsNone(sign_in_command(check.fix))  # gh's sign-in switches gh's account: printed, never run

    def test_a_classic_token_is_refused_before_github_is_asked(self):
        asked = []
        adapter = CopilotAdapter(fetch=lambda token, timeout: asked.append(token) or (200, {"login": "octocat"}))
        env = self.env({"octocat": "ghp_classic"})
        adapter.inject(self.conn, env)
        check = adapter.verify(self.conn, env)
        self.assertTrue(check.failed)
        self.assertEqual(check.detail, "gh's stored login for octocat is a classic personal access token, which "
                                       "Copilot CLI refuses")
        self.assertEqual(asked, [])

    def test_a_token_of_another_account_is_caught(self):
        _, check = self.verify(tokens={"octocat": "gho_hubot"})
        self.assertTrue(check.failed)
        self.assertEqual(check.detail, "gh's stored login for octocat actually authenticates as hubot")

    def test_revoked_token(self):
        _, check = self.verify(tokens={"octocat": "gho_revoked"})
        self.assertIn("was rejected", check.detail)

    def test_forget_removes_only_what_keyfit_set(self):
        env = self.env()
        self.adapter.inject(self.conn, env)
        self.adapter.forget(env)
        self.assertFalse({"COPILOT_GITHUB_TOKEN", "KEYFIT_COPILOT_LOGIN"} & set(env))
        own = {"COPILOT_GITHUB_TOKEN": "mine"}
        self.adapter.forget(own)
        self.assertEqual(own, {"COPILOT_GITHUB_TOKEN": "mine"})

    def test_a_project_without_a_copilot_account_forgets_anothers(self):
        env = self.env()
        self.adapter.inject(self.conn, env)
        with mock.patch.dict(ADAPTERS, {"copilot": self.adapter}):
            env = environment.build(Project("globex", [str(self.tmp)], {}), env)
        self.assertNotIn("COPILOT_GITHUB_TOKEN", env)

    def test_fingerprint_follows_the_token_and_ghs_sign_ins(self):
        env = self.env()
        self.adapter.inject(self.conn, env)
        first = self.adapter.fingerprint(self.conn, env)
        self.assertEqual(first, f"{hashlib.sha256(b'gho_octocat').hexdigest()[:16]}:")
        hosts = self.home / ".config" / "gh" / "hosts.yml"
        hosts.parent.mkdir(parents=True)
        hosts.write_text("github.com: {}\n")
        self.assertNotEqual(first, self.adapter.fingerprint(self.conn, env))

    def test_a_confirmed_token_is_reused_without_asking_gh(self):
        project = Project("demo", [str(self.tmp)], {"github": {"name": "octocat", "login": "octocat"},
                                                    "copilot": self.conn})
        github = mock.patch.object(ADAPTERS["github"], "fetch", fetch)
        copilot = mock.patch.object(ADAPTERS["copilot"], "fetch", fetch)
        with github, copilot:
            env = environment.build(project, self.env())
            self.assertEqual(len(self.gh_calls()), 2)  # the GitHub account's token, then Copilot's
            checks = environment.verify(project, env, use_cache=True)
            self.assertEqual([c.status for c in checks], ["ok", "ok"], [c.detail for c in checks])
            env = environment.build(project, self.env())
            self.assertEqual(len(self.gh_calls()), 3)  # Copilot's: the GitHub account's token, which it confirmed
            self.assertEqual(env["COPILOT_GITHUB_TOKEN"], "gho_octocat")
            session = environment.build(project, self.env(tokens={}, **{k: env[k] for k in (
                "GH_TOKEN", "COPILOT_GITHUB_TOKEN", "KEYFIT_GITHUB_LOGIN", "KEYFIT_COPILOT_LOGIN")}))
            self.assertEqual((len(self.gh_calls()), session["COPILOT_GITHUB_TOKEN"]), (3, "gho_octocat"))
            # a token this project's check never confirmed is never taken
            other = environment.build(project, self.env(COPILOT_GITHUB_TOKEN="gho_hubot", GH_TOKEN="gho_hubot",
                                                        KEYFIT_GITHUB_LOGIN="octocat"))
            self.assertEqual((len(self.gh_calls()), other["COPILOT_GITHUB_TOKEN"]), (4, "gho_octocat"))
            # mapped before the GitHub account (`keyfit add copilot --login` first): still one question to gh
            reordered = Project("demo", project.roots, dict(reversed(project.connections.items())))
            self.assertEqual(list(reordered.connections), ["copilot", "github"])
            environment.build(reordered, self.env())
            self.assertEqual(len(self.gh_calls()), 5)

    def test_other_agents_sessions_get_the_projects_copilot_token(self):
        # npx @github/copilot inside a Claude session does not go through the shim: the session has the account
        project = Project("demo", [str(self.tmp)], {"copilot": self.conn})
        env = environment.build(project, self.env(COPILOT_PROVIDER_API_KEY="sk-x"))
        self.assertEqual(env["COPILOT_GITHUB_TOKEN"], "gho_octocat")
        self.assertNotIn("COPILOT_PROVIDER_API_KEY", env)


class McpTests(unittest.TestCase):
    """Which servers a Copilot CLI session sees: the same rules as Claude's, Codex's and OpenCode's."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.home = self.tmp / "home"
        (self.home / ".copilot").mkdir(parents=True)
        self.user({
            "linear_a": {**LINEAR, "headers": {"Authorization": "Bearer ${LINEAR_A_API_KEY}"}},
            "linear_b": {**LINEAR, "headers": {"Authorization": "Bearer ${LINEAR_B_API_KEY}"}},
            "globex_cloud_eu": {"type": "local", "command": "globex-mcp", "args": []},
            "paper": {"type": "http", "url": "http://127.0.0.1:29979/mcp"},
        })
        self.repo = self.tmp / "code" / "repo"
        (self.repo / ".git").mkdir(parents=True)
        self.env = {"HOME": str(self.home)}
        self.registry = Registry(self.tmp / "registry.toml")
        self.registry.add_project("globex", self.tmp / "globex")
        self.registry.add_mcp_servers("globex", ["globex_cloud_*"])
        self.registry.add_project("acme", self.repo)
        self.registry.set_connection("acme", "linear", {"workspace": "a", "key": "env:LINEAR_A_API_KEY"})

    def user(self, servers, folder=None):
        folder = folder or self.home / ".copilot"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "mcp-config.json").write_text(json.dumps({"mcpServers": servers}))

    def hidden(self, project="acme", cwd=None, env=None, **kwargs):
        return mcp.copilot_hidden(self.registry.get(project), self.env if env is None else env, cwd or self.repo,
                                  self.registry.mcp_owners(), **kwargs)

    def test_other_projects_servers_are_hidden(self):
        self.assertEqual(self.hidden(), ["globex_cloud_eu", "linear_b"])

    def test_owner_sees_its_servers(self):
        self.assertEqual(self.hidden("globex"), ["linear_a", "linear_b"])  # globex has no Linear account

    def test_copilot_home_and_config_dir_move_the_users_file(self):
        self.user({"globex_cloud_us": {"type": "local", "command": "x"}}, self.tmp / "copilot-home")
        self.assertEqual(self.hidden(env={**self.env, "COPILOT_HOME": str(self.tmp / "copilot-home")}),
                         ["globex_cloud_us"])
        self.user({"globex_cloud_ap": {"type": "local", "command": "x"}}, self.tmp / "flag")
        self.assertEqual(self.hidden(env={**self.env, "COPILOT_HOME": str(self.tmp / "copilot-home")},
                                     config_dir=str(self.tmp / "flag")), ["globex_cloud_ap"])

    def test_workspace_files_count_up_to_the_repositorys_top(self):
        sub = self.repo / "web"
        (sub / ".github").mkdir(parents=True)
        (sub / ".github" / "mcp.json").write_text(json.dumps({"mcpServers": {"globex_cloud_web": {"command": "x"}}}))
        (self.repo / ".mcp.json").write_text(json.dumps({"mcpServers": {
            "linear": {**LINEAR, "headers": {"Authorization": "Bearer ${LINEAR_A_API_KEY}"}}}}))
        (self.tmp / "code" / ".mcp.json").write_text(json.dumps({"mcpServers": {"globex_cloud_above": {"command": "x"}}}))
        # the folder's Linear server is unverified, like .mcp.json for Claude; above the repository Copilot never looks
        self.assertEqual(self.hidden(cwd=sub), ["globex_cloud_eu", "globex_cloud_web", "linear", "linear_b"])

    def test_outside_a_repository_only_the_folder_counts(self):
        folder = self.tmp / "loose" / "sub"
        folder.mkdir(parents=True)
        (folder.parent / ".mcp.json").write_text(json.dumps({"mcpServers": {"globex_cloud_up": {"command": "x"}}}))
        (folder / ".mcp.json").write_text(json.dumps({"mcpServers": {"globex_cloud_here": {"command": "x"}}}))
        self.assertEqual(self.hidden(cwd=folder), ["globex_cloud_eu", "globex_cloud_here", "linear_b"])

    def test_additional_mcp_config_from_the_command_line(self):
        extra = self.repo / "extra.json"
        extra.write_text(json.dumps({"mcpServers": {"globex_cloud_file": {"command": "x"}}}))
        given = [json.dumps({"mcpServers": {"globex_cloud_json": {"command": "x"}}}), "@extra.json", "not json"]
        self.assertEqual(self.hidden(additional=given),
                         ["globex_cloud_eu", "globex_cloud_file", "globex_cloud_json", "linear_b"])

    def test_unreadable_files_hide_nothing_from_them(self):
        (self.home / ".copilot" / "mcp-config.json").write_text("{not json")
        (self.repo / ".mcp.json").write_text(json.dumps({"mcpServers": ["a"]}))
        self.assertEqual(self.hidden(), [])


class ArgumentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.home = self.tmp / "home"
        (self.home / ".copilot").mkdir(parents=True)
        (self.home / ".copilot" / "mcp-config.json").write_text(json.dumps({"mcpServers": {
            "globex_cloud_eu": {"command": "x"}, "-odd name": {"command": "x"}, "paper": {"command": "x"}}}))
        self.repo = self.tmp / "repo"
        (self.repo / ".git").mkdir(parents=True)
        self.registry = Registry(self.tmp / "registry.toml")
        self.registry.add_project("globex", self.tmp / "globex")
        self.registry.add_mcp_servers("globex", ["globex_cloud_*", "-odd*"])
        self.registry.add_project("demo", self.repo)

    def args(self, *given, cwd=None):
        return adjust_args("copilot", self.registry.get("demo"), list(given), {"HOME": str(self.home)},
                           cwd or self.repo, self.registry.mcp_owners())

    def test_sessions_start_with_other_projects_servers_switched_off(self):
        flags = ["--disable-mcp-server=-odd name", "--disable-mcp-server=globex_cloud_eu"]
        self.assertEqual(self.args(), flags)
        self.assertEqual(self.args("-p", "fix it", "--allow-all-tools"), [*flags, "-p", "fix it", "--allow-all-tools"])
        self.assertEqual(self.args("init"), [*flags, "init"])  # it runs the agent
        self.assertEqual(self.args("--acp"), [*flags, "--acp"])

    def test_management_commands_run_as_typed(self):
        for given in (["mcp", "list"], ["config", "--list"], ["update"], ["help", "config"], ["plugin", "list"]):
            self.assertEqual(self.args(*given), given)

    def test_servers_of_the_folder_copilot_works_in(self):
        web = self.repo / "web"
        web.mkdir()
        (web / ".mcp.json").write_text(json.dumps({"mcpServers": {"globex_cloud_web": {"command": "x"}}}))
        self.assertIn("--disable-mcp-server=globex_cloud_web", self.args("-C", "web"))
        self.assertIn("--disable-mcp-server=globex_cloud_web", self.args(f"-C{web}", cwd=self.tmp))
        self.assertNotIn("--disable-mcp-server=globex_cloud_web", self.args())

    def test_another_projects_server_cannot_be_enabled(self):
        for given in (["--enable-mcp-server", "globex_cloud_eu"], ["--enable-mcp-server=globex_cloud_eu"]):
            with self.assertRaises(Refused) as caught:
                self.args(*given)
            self.assertIn("refusing --enable-mcp-server globex_cloud_eu", str(caught.exception))
        self.assertEqual(self.args("--enable-mcp-server", "paper")[-2:], ["--enable-mcp-server", "paper"])

    def test_a_sign_in_behind_an_option_is_still_a_sign_in(self):
        env = {"HOME": str(self.home), "COPILOT_GITHUB_TOKEN": "gho_x", "GH_TOKEN": "gho_x", "GITHUB_TOKEN": "gho_x"}
        given = ["-C", str(self.repo), "login"]
        self.assertEqual(adjust_args("copilot", self.registry.get("demo"), list(given), env, self.repo), given)
        self.assertEqual(env, {"HOME": str(self.home)})  # Copilot signs in itself, it takes no token from here
        self.registry.set_connection("demo", "copilot", {"login": "octocat"})
        with self.assertRaises(Refused):
            adjust_args("copilot", self.registry.get("demo"), list(given), dict(env), self.repo)

    def test_config_dir_with_a_tilde(self):
        (self.home / "work-copilot").mkdir()
        (self.home / "work-copilot" / "mcp-config.json").write_text(json.dumps({"mcpServers": {
            "globex_cloud_work": {"command": "x"}}}))
        with mock.patch.dict(os.environ, {"HOME": str(self.home)}):
            self.assertEqual(self.args("--config-dir", "~/work-copilot")[0], "--disable-mcp-server=globex_cloud_work")

    def test_options_in_every_form_copilot_takes(self):
        given = ["-C", "a", "-Cb", "-C=c", "--config-dir=d", "--additional-mcp-config", "@e", "--", "-C", "f"]
        self.assertEqual(copilot_options(given), [("-C", "a"), ("-C", "b"), ("-C", "c"), ("--config-dir", "d"),
                                                  ("--additional-mcp-config", "@e")])


def confirm(config: Path, project: str, login: str, token: str, home: Path) -> None:
    """What a passing online check leaves behind: the shims start from the cache, never asking GitHub."""
    with mock.patch.dict(os.environ, {"KEYFIT_CONFIG_DIR": str(config)}):
        fingerprint = ADAPTERS["copilot"]._fingerprint(token, {"HOME": str(home)})
        cache.put(cache.key(project, "copilot", {"login": login, "name": login}, fingerprint), "ok", "login confirmed")


class ShimTests(unittest.TestCase):
    """The copilot shim written by `keyfit setup`, run as a real process in temporary folders."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.home = self.tmp / "home"
        (self.home / ".copilot").mkdir(parents=True)
        (self.home / ".copilot" / "mcp-config.json").write_text(json.dumps({"mcpServers": {
            "globex_cloud_eu": {"type": "local", "command": "globex-mcp"}, "paper": {"type": "http", "url": "http://p"}}}))
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        executable(self.bin / "copilot", FAKE_COPILOT)
        executable(self.bin / "gh", FAKE_GH)
        self.config = self.tmp / "keyfit"
        self.repo = self.tmp / "repo"
        self.other = self.tmp / "other"
        self.outside = self.tmp / "outside"
        for folder in (self.repo, self.other, self.outside):
            folder.mkdir()
        for folder in (self.repo, self.other):
            subprocess.run(["git", "init", "-q"], cwd=folder, check=True)
        with mock.patch.dict(os.environ, {"KEYFIT_CONFIG_DIR": str(self.config)}):
            with contextlib.redirect_stdout(io.StringIO()):
                main(["init", "--yes", "--root", str(self.repo), "--name", "demo"])
                main(["init", "--yes", "--root", str(self.other), "--name", "globex"])
                with mock.patch.object(sys, "platform", "darwin"):
                    main(["setup"])
        reg = self.config / "registry.toml"
        text = reg.read_text().replace(
            '[projects.demo]\nroots = ["' + str(self.repo) + '"]\n',
            '[projects.demo]\nroots = ["' + str(self.repo) + '"]\ncopilot = "octocat"\n')
        text = text.replace('[projects.globex]\nroots = ["' + str(self.other) + '"]\n',
                            '[projects.globex]\nroots = ["' + str(self.other) + '"]\nmcp_servers = ["globex_cloud_*"]\n')
        reg.write_text(text + '\n[accounts.copilot.octocat]\nlogin = "octocat"\n')

    def run_tool(self, *args, cwd=None, tokens=None, **extra):
        env = {"PATH": f"{self.config / 'bin'}{os.pathsep}{self.bin}{os.pathsep}/usr/bin:/bin",
               "HOME": str(self.home), "KEYFIT_CONFIG_DIR": str(self.config),
               "FAKE_GH_TOKENS": json.dumps(TOKENS if tokens is None else tokens),
               "HTTPS_PROXY": "http://127.0.0.1:9", **extra}  # should a check ever go online: a closed port
        return subprocess.run(["copilot", *args], cwd=cwd or self.repo, env=env, capture_output=True, text=True,
                              timeout=60)

    def started(self, out):
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout.splitlines()[-1])

    def test_session_starts_as_the_projects_github_account_with_its_mcp_servers(self):
        confirm(self.config, "demo", "octocat", "gho_octocat", self.home)
        seen = self.started(self.run_tool("-p", "hi", COPILOT_GITHUB_TOKEN="gho_hubot", GH_HOST="ghe.example",
                                          COPILOT_PROVIDER_API_KEY="sk-x", GITHUB_TOKEN="gho_kept"))
        self.assertEqual(seen["argv"], ["--disable-mcp-server=globex_cloud_eu", "-p", "hi"])
        env = seen["env"]
        self.assertEqual((env["COPILOT_GITHUB_TOKEN"], env["KEYFIT_COPILOT_LOGIN"], env["KEYFIT_PROJECT"]),
                         ("gho_octocat", "octocat", "demo"))
        self.assertEqual((env["GH_HOST"], env["COPILOT_PROVIDER_API_KEY"], env["GITHUB_TOKEN"]),
                         (None, None, "gho_kept"))

    def test_a_mismatch_blocks_with_the_fix(self):
        out = self.run_tool("-p", "hi", tokens={})
        self.assertEqual((out.returncode, out.stdout), (1, ""))
        self.assertIn("keyfit: copilot not started, demo needs attention\n"
                      "  ✘ copilot    octocat   gh has no stored login for octocat\n"
                      "    → gh auth login --hostname github.com --git-protocol ssh --web   (sign in as octocat), "
                      "then switch back: gh auth switch --user <your usual account>\n", out.stderr)
        self.assertNotIn("Sign in now?", out.stderr)
        out = self.run_tool(tokens={"octocat": "ghp_classic"})
        self.assertEqual(out.returncode, 1)
        self.assertIn("is a classic personal access token, which Copilot CLI refuses", out.stderr)

    def test_an_unconfirmed_token_is_checked_online_and_never_trusted(self):
        out = self.run_tool("-p", "hi")  # no cached check, and GitHub cannot be reached
        self.assertEqual((out.returncode, out.stdout), (1, ""))
        self.assertIn("GitHub did not answer", out.stderr)

    def test_unregistered_folder_runs_unchanged(self):
        seen = self.started(self.run_tool("-p", "hi", cwd=self.outside, COPILOT_GITHUB_TOKEN="mine", GH_HOST="ghe"))
        self.assertEqual(seen["argv"], ["-p", "hi"])
        self.assertEqual((seen["env"]["COPILOT_GITHUB_TOKEN"], seen["env"]["GH_HOST"]), ("mine", "ghe"))
        self.assertFalse((self.home / "gh-calls.log").exists())

    def test_copilot_login_is_refused_where_the_project_has_an_account(self):
        out = self.run_tool("login")
        self.assertEqual((out.returncode, out.stdout), (1, ""))
        self.assertIn("`copilot login` would change only Copilot's own login, which demo does not use.\n"
                      "  Sign gh in instead: gh auth login --hostname github.com", out.stderr)

    def test_copilot_login_elsewhere_signs_in_without_inherited_tokens(self):
        seen = self.started(self.run_tool("login", cwd=self.other, COPILOT_GITHUB_TOKEN="gho_hubot",
                                          GH_TOKEN="gho_hubot", KEYFIT_COPILOT_LOGIN="octocat"))
        self.assertEqual(seen["argv"], ["login"])
        self.assertEqual((seen["env"]["COPILOT_GITHUB_TOKEN"], seen["env"]["GH_TOKEN"]), (None, None))

    def test_project_without_a_copilot_account_forgets_another_projects(self):
        seen = self.started(self.run_tool(cwd=self.other, COPILOT_GITHUB_TOKEN="gho_octocat",
                                          KEYFIT_COPILOT_LOGIN="octocat"))
        self.assertEqual(seen["argv"], [])  # globex owns its server: nothing to hide
        self.assertIsNone(seen["env"]["COPILOT_GITHUB_TOKEN"])

    def test_a_folder_of_another_project_is_refused(self):
        confirm(self.config, "demo", "octocat", "gho_octocat", self.home)
        for args, cwd in ((["-C", str(self.other)], self.repo), ([f"-C{self.repo}"], self.outside)):
            out = self.run_tool(*args, cwd=cwd)
            self.assertEqual((out.returncode, out.stdout), (1, ""), args)
            self.assertIn("&& copilot   (bypass once: KEYFIT_DISABLE=1 copilot …)", out.stderr)
        sub = self.repo / "src"
        sub.mkdir()
        self.assertEqual(self.started(self.run_tool("-C", "src"))["argv"][-2:], ["-C", "src"])

    def test_dry_run_names_the_variables_only(self):
        confirm(self.config, "demo", "octocat", "gho_octocat", self.home)
        out = self.run_tool("-p", "hi", KEYFIT_DRY_RUN="1")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("COPILOT_GITHUB_TOKEN", out.stderr)
        self.assertNotIn("gho_octocat", out.stderr + out.stdout)


class AddTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        executable(self.bin / "gh", FAKE_GH)
        self.repo = self.tmp / "acme"
        self.repo.mkdir()
        self.config = self.tmp / "keyfit"
        for patcher in (mock.patch.dict(os.environ, {"HOME": str(self.home), "KEYFIT_CONFIG_DIR": str(self.config),
                                                     "PATH": f"{self.bin}{os.pathsep}/usr/bin:/bin",
                                                     "FAKE_GH_TOKENS": json.dumps(TOKENS)}, clear=True),
                        mock.patch.object(Path, "home", return_value=self.home),
                        mock.patch.object(ADAPTERS["copilot"], "fetch", fetch)):
            patcher.start()
            self.addCleanup(patcher.stop)
        cwd = os.getcwd()
        os.chdir(self.repo)
        self.addCleanup(os.chdir, cwd)
        with contextlib.redirect_stdout(io.StringIO()):
            main(["init", "--yes", "--name", "acme"])

    def run_main(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def registry(self):
        import tomllib

        return tomllib.loads((self.config / "registry.toml").read_text())

    def map_github(self, login="octocat"):
        reg = Registry.load()
        reg.set_account("github", login, {"login": login})
        reg.set_connection("acme", "github", login)
        reg.save()

    def test_takes_the_projects_github_account(self):
        self.map_github()
        code, out, err = self.run_main("add", "copilot")
        self.assertEqual(code, 0, err)
        self.assertEqual(out, "acme: copilot account 'octocat' → GitHub octocat, the project's GitHub account\n")
        reg = self.registry()
        self.assertEqual(reg["projects"]["acme"]["copilot"], "octocat")
        self.assertEqual(reg["accounts"]["copilot"]["octocat"], {"login": "octocat"})
        self.assertNotIn("gho_octocat", (self.config / "registry.toml").read_text())

    def test_another_login(self):
        self.map_github()
        code, out, err = self.run_main("add", "copilot", "--login", "hubot")
        self.assertEqual(code, 0, err)
        self.assertEqual(out, "acme: copilot account 'hubot' → GitHub hubot\n")

    def test_no_github_account_and_no_login(self):
        code, out, err = self.run_main("add", "copilot")
        self.assertEqual((code, out), (1, ""))
        self.assertIn("acme has no GitHub account to take the login from — map one (keyfit add github --login "
                      "<login>), or name it: keyfit add copilot --login <login>", err)

    def test_a_login_gh_does_not_hold(self):
        code, _, err = self.run_main("add", "copilot", "--login", "nobody")
        self.assertEqual(code, 1)
        self.assertIn("gh has no stored login for nobody — sign in first as nobody", err)
        self.assertNotIn("copilot", self.registry()["projects"]["acme"])

    def test_a_classic_token_is_refused(self):
        os.environ["FAKE_GH_TOKENS"] = json.dumps({"octocat": "ghp_classic"})
        code, _, err = self.run_main("add", "copilot", "--login", "octocat")
        self.assertEqual(code, 1)
        self.assertIn("is a classic personal access token, which Copilot CLI refuses", err)
        self.assertNotIn("ghp_classic", err)

    def test_a_token_of_another_account_is_refused(self):
        os.environ["FAKE_GH_TOKENS"] = json.dumps({"octocat": "gho_hubot"})
        code, _, err = self.run_main("add", "copilot", "--login", "octocat")
        self.assertEqual(code, 1)
        self.assertIn("gh's stored login for octocat actually authenticates as hubot", err)

    def test_init_offers_copilot_as_the_projects_github_account(self):
        from keyfit import cli

        base = {"registry": None, "project": "acme"}
        with mock.patch.object(cli, "_ask") as ask, contextlib.redirect_stdout(io.StringIO()) as out:
            cli._offer_copilot("acme", base)
        self.assertEqual(out.getvalue(), "Copilot CLI: copilot not found on PATH — skipped\n")
        executable(self.bin / "copilot", "#!/bin/sh\nexit 97\n")
        with mock.patch.object(cli, "_ask") as ask, contextlib.redirect_stdout(io.StringIO()) as out:
            cli._offer_copilot("acme", base)
        self.assertIn("acme has no GitHub account, which Copilot CLI signs in with", out.getvalue())
        ask.assert_not_called()
        self.map_github()
        with mock.patch.object(cli, "_ask", return_value="y") as ask, contextlib.redirect_stdout(io.StringIO()):
            cli._offer_copilot("acme", base)
        ask.assert_called_once_with("Start Copilot CLI in acme as its GitHub account octocat? [y/N] ")
        self.assertEqual(self.registry()["projects"]["acme"]["copilot"], "octocat")

    def test_init_offers_it_right_after_github(self):
        from keyfit import cli

        kinds = [kind for kind, _, _ in cli.GUIDED]
        self.assertEqual(kinds.index("copilot"), kinds.index("opencode") + 1)
        self.assertLess(kinds.index("github"), kinds.index("copilot"))

    def test_doctor_shows_the_account_and_the_config_folder(self):
        self.map_github()
        self.run_main("add", "copilot")
        _, out, _ = self.run_main("doctor")
        self.assertIn("  ✔ copilot    octocat   GitHub octocat, gh's token · settings and MCP servers in ~/.copilot\n", out)
        self.assertIn("  ! copilot    not found on PATH   needed only if you use it\n"
                      "    → npm install -g @github/copilot\n", out)
        self.run_main("add", "copilot", "--login", "hubot")
        _, out, _ = self.run_main("doctor")
        self.assertIn("  ! copilot    hubot   GitHub hubot, gh's token, while the project's GitHub account is octocat", out)


if __name__ == "__main__":
    unittest.main()
