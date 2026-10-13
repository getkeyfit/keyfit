import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

from keyfit.adapters.claude import ClaudeAdapter, login_command, seed
from keyfit.realbin import find_real
from keyfit.registry import Registry

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
        made = seed(self.other, {"HOME": str(self.home)}, [repo])
        self.assertTrue((self.other / "settings.json").is_symlink())
        self.assertTrue((self.other / "skills").is_symlink())
        self.assertFalse((self.other / ".credentials.json").exists())
        state = json.loads((self.other / ".claude.json").read_text())
        self.assertTrue(state["projects"][str(repo)]["hasTrustDialogAccepted"])
        self.assertIn("settings.json", made)

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


if __name__ == "__main__":
    unittest.main()
