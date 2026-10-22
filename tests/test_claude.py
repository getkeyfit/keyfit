import contextlib
import io
import json
import os
import stat
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

from keyfit import mcp
from keyfit.adapters import gemini
from keyfit.adapters.claude import ClaudeAdapter, login_command, seed
from keyfit.adapters.google_mcp import GoogleMcpAdapter
from keyfit.cli import main
from keyfit.realbin import find_real
from keyfit.registry import Project, Registry
from keyfit.shim import Refused, adjust_args

os.environ["KEYFIT_CONFIG_DIR"] = tempfile.mkdtemp()  # tests never touch the real ~/.config/keyfit

FAKE_CLAUDE = """#!/usr/bin/env python3
import json, os, sys
assert sys.argv[1:] == ["auth", "status", "--json"], sys.argv
folder = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.environ["HOME"], ".claude")
if os.environ.get("ANTHROPIC_API_KEY"):
    print(json.dumps({"loggedIn": True, "authMethod": "api_key", "configDirectory": folder})); sys.exit(0)
logins = json.loads(os.environ.get("FAKE_CLAUDE", "{}"))
login = logins.get(os.path.realpath(folder))
if not login:
    print(json.dumps({"loggedIn": False, "authMethod": "none", "configDirectory": folder})); sys.exit(1)
print(json.dumps(dict(login, loggedIn=True, configDirectory=folder)))
"""

WORK = {"authMethod": "claude.ai", "email": "me@work.io", "orgId": "org-work", "orgName": "Work", "subscriptionType": "team"}


class ClaudeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.home = self.tmp / "home"
        (self.home / ".claude").mkdir(parents=True)
        (self.home / ".claude" / "settings.json").write_text("{}")
        (self.home / ".claude" / "skills").mkdir()
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        fake = self.bin / "claude"
        fake.write_text(FAKE_CLAUDE)
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
        self.other = self.tmp / "accounts" / "client"
        self.other.mkdir(parents=True)
        self.claude = ClaudeAdapter(timeout=10)
        self.work = {"name": "work", "config_dir": str(self.home / ".claude"), "email": "me@work.io",
                     "org_id": "org-work", "org_name": "Work"}

    def env(self, logins=None, **extra):
        env = {"HOME": str(self.home), "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}", **extra}
        env["FAKE_CLAUDE"] = json.dumps(logins or {})
        return env

    def run_verify(self, conn, logins, **extra):
        env = self.env(logins, **extra)
        self.claude.inject(conn, env)
        return env, self.claude.verify(conn, env)

    def test_default_login_leaves_config_dir_unset(self):
        env = self.env(CLAUDE_CONFIG_DIR="/somewhere/else", ANTHROPIC_API_KEY="sk-x", CLAUDE_CODE_OAUTH_TOKEN="t",
                       ANTHROPIC_BASE_URL="https://proxy", CLAUDE_CODE_USE_BEDROCK="1")
        self.claude.inject(self.work, env)
        for var in ("CLAUDE_CONFIG_DIR", "ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN",
                    "ANTHROPIC_BASE_URL", "CLAUDE_CODE_USE_BEDROCK"):
            self.assertNotIn(var, env)

    def test_other_account_sets_config_dir(self):
        env = self.env()
        self.claude.inject(dict(self.work, config_dir=str(self.other)), env)
        self.assertEqual(env["CLAUDE_CONFIG_DIR"], str(self.other))

    def test_confirmed_login(self):
        _, check = self.run_verify(self.work, {str(self.home / ".claude"): WORK})
        self.assertEqual(check.status, "ok", check.detail)
        self.assertIn("Work (team) confirmed", check.detail)

    def test_inherited_api_key_cannot_change_billing(self):
        _, check = self.run_verify(self.work, {str(self.home / ".claude"): WORK}, ANTHROPIC_API_KEY="sk-ant-x")
        self.assertEqual(check.status, "ok")  # scrubbed before Claude ever saw it

    def test_api_key_login_is_rejected(self):
        login = dict(WORK, authMethod="api_key")
        _, check = self.run_verify(self.work, {str(self.home / ".claude"): login})
        self.assertTrue(check.failed)
        self.assertIn("billing", check.detail)

    def test_not_signed_in_gives_login_command(self):
        conn = dict(self.work, config_dir=str(self.other))
        _, check = self.run_verify(conn, {})
        self.assertTrue(check.failed)
        self.assertEqual(check.fix, f"CLAUDE_CONFIG_DIR={self.other} claude auth login")

    def test_wrong_org_fails(self):
        _, check = self.run_verify(self.work, {str(self.home / ".claude"): dict(WORK, orgId="org-other", orgName="Other")})
        self.assertTrue(check.failed)
        self.assertIn("'Other'", check.detail)

    def test_login_command_for_default(self):
        self.assertEqual(login_command(self.home / ".claude", {"HOME": str(self.home)}), "claude auth login")

    def test_seed_links_shared_settings_and_trusts_repo(self):
        repo = self.tmp / "repo"
        repo.mkdir()
        (self.home / ".claude.json").write_text(json.dumps({"projects": {str(repo): {"hasTrustDialogAccepted": True}}}))
        made = seed(self.other, {"HOME": str(self.home)}, [repo])
        self.assertTrue((self.other / "settings.json").is_symlink())
        self.assertTrue((self.other / "skills").is_symlink())
        self.assertFalse((self.other / ".credentials.json").exists())
        state = json.loads((self.other / ".claude.json").read_text())
        self.assertTrue(state["projects"][str(repo)]["hasTrustDialogAccepted"])
        self.assertIn("settings.json", made)

    def test_seed_trusts_only_what_the_default_account_trusts(self):
        # trust lets a repository's own hooks and settings run: a new login never trusts more than the old one
        parent, child, stranger = self.tmp / "work", self.tmp / "work" / "client", self.tmp / "stranger"
        for folder in (child, stranger):
            folder.mkdir(parents=True)
        (self.home / ".claude.json").write_text(json.dumps({"projects": {
            str(parent): {"hasTrustDialogAccepted": True}, str(stranger): {"hasTrustDialogAccepted": False}}}))
        seed(self.other, {"HOME": str(self.home)}, [child, stranger])
        state = json.loads((self.other / ".claude.json").read_text())
        self.assertEqual(list(state["projects"]), [str(child)])

    def test_seed_creates_an_owner_only_folder(self):
        # Claude keeps the account's transcripts and history in this folder
        fresh = self.tmp / "accounts" / "fresh"
        seed(fresh, {"HOME": str(self.home)}, [])
        self.assertEqual((fresh.stat().st_mode & 0o777, (fresh / ".claude.json").stat().st_mode & 0o777),
                         (0o700, 0o600))

    def test_registry_expands_account_reference(self):
        reg = Registry(self.tmp / "r.toml")
        reg.add_project("demo", self.tmp)
        reg.set_account("claude", "work", {"config_dir": "~/.claude", "org_name": "Work"})
        reg.set_connection("demo", "claude", "work")
        reg.save()
        conn = Registry.load(reg.path).get("demo").connections["claude"]
        self.assertEqual(conn, {"config_dir": "~/.claude", "org_name": "Work", "name": "work"})

    def test_checks_skip_superset_wrapper(self):
        wrapper = self.home / ".superset" / "bin"
        wrapper.mkdir(parents=True)
        (wrapper / "claude").write_text("#!/bin/sh\n")
        (wrapper / "claude").chmod(0o755)
        env = {"HOME": str(self.home), "PATH": f"{wrapper}{os.pathsep}{self.bin}"}
        self.assertEqual(find_real("claude", env, skip_wrappers=True), str(self.bin / "claude"))
        self.assertEqual(find_real("claude", env), str(wrapper / "claude"))


class AddClaudeTests(unittest.TestCase):
    """`keyfit add claude` from two projects, with a fake claude that has a personal login in ~/.claude and another
    one in a second folder."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.home = self.tmp / "home"
        (self.home / ".claude").mkdir(parents=True)
        self.other = self.tmp / "accounts" / "globex"
        self.other.mkdir(parents=True)
        self.config = self.tmp / "keyfit"
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        (bin_dir / "claude").write_text(FAKE_CLAUDE)
        (bin_dir / "claude").chmod(0o755)
        logins = {str(self.home / ".claude"): {"authMethod": "claude.ai", "email": "me@home.io", "subscriptionType": "pro"},
                  str(self.other): {"authMethod": "claude.ai", "email": "me@globex.io", "subscriptionType": "max"}}
        for patcher in (mock.patch.dict(os.environ, {"HOME": str(self.home), "KEYFIT_CONFIG_DIR": str(self.config),
                                                     "PATH": f"{bin_dir}{os.pathsep}/usr/bin:/bin",
                                                     "FAKE_CLAUDE": json.dumps(logins)}, clear=True),
                        mock.patch.object(Path, "home", return_value=self.home)):
            patcher.start()
            self.addCleanup(patcher.stop)
        for name in ("acme", "globex"):
            (self.tmp / name).mkdir()
            self.run_main("init", "--yes", "--root", str(self.tmp / name), "--name", name)

    def run_main(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def registry(self):
        return tomllib.loads((self.config / "registry.toml").read_text())

    def test_two_personal_logins_keep_their_own_accounts(self):
        self.assertEqual(self.run_main("add", "claude", "--project", "acme"),
                         (0, "acme: claude account 'personal' → personal (pro) in ~/.claude\n", ""))
        self.assertEqual(self.run_main("add", "claude", "--project", "globex", "--config-dir", str(self.other)),
                         (0, f"globex: claude account 'personal-2' → personal (max) in {self.other}\n", ""))
        reg = self.registry()
        self.assertEqual(reg["accounts"]["claude"]["personal"],
                         {"config_dir": "~/.claude", "email": "me@home.io", "pin_email": True})
        self.assertEqual(reg["accounts"]["claude"]["personal-2"]["config_dir"], str(self.other))
        self.assertEqual((reg["projects"]["acme"]["claude"], reg["projects"]["globex"]["claude"]),
                         ("personal", "personal-2"))

    def test_new_with_a_shared_name_names_who_changed(self):
        self.run_main("add", "claude", "--project", "acme", "--name", "work")
        code, out, err = self.run_main("add", "claude", "--project", "globex", "--new", "--name", "work")
        self.assertEqual(code, 0, err)
        self.assertIn("  account 'work' was config_dir ~/.claude, email me@home.io; it changed for acme too\n", out)

    def test_new_refuses_a_name_that_is_not_a_name(self):
        for name in ("../x", "../../x", "a\nb"):
            self.assertEqual(self.run_main("add", "claude", "--project", "acme", "--new", "--name", name),
                             (1, "", "a name is letters, digits, '.', '_' and '-' (at most 64)\n"))
        self.assertFalse((self.config / "x").exists() or (self.tmp / "x").exists())
        self.assertNotIn("claude", self.registry().get("accounts", {}))


GLOBEX_LINEAR = {"type": "http", "url": "https://mcp.linear.app/mcp", "headers": {"Authorization": "Bearer ${LINEAR_GLOBEX_API_KEY}"}}
LINEAR = {"type": "http", "url": "https://mcp.linear.app/mcp", "headers": {"Authorization": "Bearer ${LINEAR_API_KEY}"}}


class McpTests(unittest.TestCase):
    """The servers a Claude session of acme must not see: from the user scope, the local scope (`claude mcp add` without
    --scope) of the folder and its repository, and a second login's ~/.claude.json, and the folder's .mcp.json."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.repo = self.tmp / "repo"
        (self.repo / ".git").mkdir(parents=True)
        self.web = self.repo / "web"
        self.web.mkdir()
        self.acme = Project("acme", [str(self.repo)], {"linear": {"workspace": "acme", "key": "env:LINEAR_ACME_API_KEY"}})
        self.owners = {"globex": ["globex_cloud_*"]}
        self.env = {"HOME": str(self.home)}
        self.system = mock.patch.object(gemini, "SYSTEM", self.tmp / "no-system")  # never this Mac's Gemini CLI settings
        self.system.start()
        self.addCleanup(self.system.stop)

    def state(self, data, path=None):
        (path or self.home / ".claude.json").write_text(json.dumps(data))

    def denied(self, cwd=None, env=None):
        settings = mcp.claude_settings(self.acme, env or self.env, cwd or self.repo, self.owners)
        return sorted(s["serverName"] for s in settings.get("deniedMcpServers", []))

    def test_local_scope_of_the_folder_and_the_repositorys_top(self):
        self.state({"mcpServers": {"linear": LINEAR}, "projects": {
            str(self.repo): {"mcpServers": {"linear_globex": GLOBEX_LINEAR}},
            str(self.web): {"mcpServers": {"globex_cloud_eu": {"command": "globex-mcp"}}},
            str(self.tmp / "elsewhere"): {"mcpServers": {"globex_cloud_us": {"command": "globex-mcp"}}}}})
        self.assertEqual(self.denied(self.web), ["globex_cloud_eu", "linear_globex"])
        self.assertEqual(self.denied(self.repo), ["linear_globex"])

    def test_a_name_is_denied_when_any_of_its_definitions_must_be_hidden(self):
        # the local server wins over the user server of its name, and the deny covers both
        self.state({"mcpServers": {"linear": LINEAR}, "projects": {str(self.repo): {"mcpServers": {"linear": GLOBEX_LINEAR}}}})
        self.assertEqual(self.denied(), ["linear"])

    def test_local_scope_of_a_linked_worktrees_main_checkout(self):
        worktree = self.tmp / "worktrees" / "feature"
        worktree.mkdir(parents=True)
        entry = self.repo / ".git" / "worktrees" / "feature"
        entry.mkdir(parents=True)
        (entry / "gitdir").write_text(f"{worktree / '.git'}\n")
        (worktree / ".git").write_text(f"gitdir: {entry}\n")
        self.state({"projects": {str(self.repo): {"mcpServers": {"linear_globex": GLOBEX_LINEAR}}}})
        self.assertEqual(self.denied(worktree), ["linear_globex"])
        (entry / "gitdir").write_text("/somewhere/else/.git\n")  # a .git file the repository doesn't list back
        self.assertEqual(self.denied(worktree), [])

    def test_a_second_logins_config_folder(self):
        client = self.tmp / "accounts" / "client"
        client.mkdir(parents=True)
        self.state({"mcpServers": {"globex_cloud_eu": {"command": "x"}}}, client / ".claude.json")
        self.state({"mcpServers": {"globex_cloud_home": {"command": "x"}}})
        self.assertEqual(self.denied(env={**self.env, "CLAUDE_CONFIG_DIR": str(client)}), ["globex_cloud_eu"])

    def test_mcp_json_with_a_byte_order_mark_or_comments(self):
        servers = json.dumps({"mcpServers": {"linear": LINEAR, "globex_cloud_eu": {"command": "x"}}})
        for text in ("\ufeff" + servers, "// the team's servers\n" + servers):
            (self.repo / ".mcp.json").write_text(text, encoding="utf-8")
            settings = mcp.claude_settings(self.acme, self.env, self.repo, self.owners)
            self.assertEqual(sorted(settings["disabledMcpjsonServers"]), ["globex_cloud_eu", "linear"])

    def test_a_file_claude_loads_that_cannot_be_parsed_stops_the_start(self):
        for path in (self.repo / ".mcp.json", self.home / ".claude.json"):
            path.write_text('{"mcpServers": {"linear": ')
            with self.assertRaises(Refused) as refused:
                adjust_args("claude", self.acme, ["-p", "hi"], dict(self.env), self.repo, self.owners)
            self.assertEqual(str(refused.exception),
                             f"keyfit: claude not started: Keyfit can't parse {path}, so it can't hide the MCP servers "
                             f"there that acme must not see.\n  Fix the file (bypass once: KEYFIT_DISABLE=1 claude …)")
            self.assertEqual(mcp.definitions(self.env, self.repo), [])  # the Google check reads on, without it
            path.unlink()
        self.assertEqual(adjust_args("claude", self.acme, ["-p", "hi"], dict(self.env), self.repo, self.owners)[2:],
                         ["-p", "hi"])

    def test_a_local_definition_of_a_claimed_google_server_is_checked(self):
        work, personal = self.home / ".gmail-mcp" / "work.json", self.home / ".gmail-mcp" / "personal.json"
        gmail = {"command": "npx", "args": ["-y", "@gongrzhe/server-gmail-autoauth-mcp"]}
        self.state({"mcpServers": {"gmail_work": {**gmail, "env": {"GMAIL_CREDENTIALS_PATH": str(work)}}},
                    "projects": {str(self.repo): {"mcpServers": {
                        "gmail_work": {**gmail, "env": {"GMAIL_CREDENTIALS_PATH": str(personal)}}}}}})
        conn = {"name": "work", "account": "me@acme.io", "servers": {"gmail_work": {"type": "gmail-autoauth", "login": str(work)}}}
        check = GoogleMcpAdapter().check_local(conn, self.env, self.repo)
        self.assertTrue(check.failed)
        self.assertEqual(check.detail, f"Claude's gmail_work reads {personal}, not {work}")


if __name__ == "__main__":
    unittest.main()
