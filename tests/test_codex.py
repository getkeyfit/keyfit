import base64
import contextlib
import io
import json
import os
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

os.environ["KEYFIT_CONFIG_DIR"] = tempfile.mkdtemp()  # tests never touch the real ~/.config/keyfit

from keyfit.adapters.codex import CodexAdapter  # noqa: E402
from keyfit.cli import main  # noqa: E402
from keyfit.registry import Project  # noqa: E402
from keyfit.shim import Refused, adjust_args  # noqa: E402


def jwt(claims: dict) -> str:
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
    return f"{enc({'alg': 'none'})}.{enc(claims)}.sig"


class CodexTests(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp()).resolve()
        self.codex = CodexAdapter()
        self.conn = {"name": "pro", "codex_home": str(self.home), "plan": "pro"}

    def write(self, **auth):
        claims = {"https://api.openai.com/auth": {"chatgpt_plan_type": "pro", "chatgpt_account_id": auth.pop("account", "acct-1")}}
        data = {"auth_mode": "chatgpt", "OPENAI_API_KEY": None, "tokens": {"id_token": jwt(claims), "access_token": "x"}}
        data.update(auth)
        (self.home / "auth.json").write_text(json.dumps(data))

    def verify(self, conn=None, **env):
        conn = conn or self.conn
        e = {"HOME": str(self.home), **env}
        self.codex.inject(conn, e)
        return e, self.codex.verify(conn, e)

    def test_chatgpt_login_ok_and_api_key_env_removed(self):
        self.write()
        env, check = self.verify(CODEX_API_KEY="sk-inherited")
        self.assertNotIn("CODEX_API_KEY", env)
        self.assertEqual(env["CODEX_HOME"], str(self.home))
        self.assertEqual(check.status, "ok")
        self.assertIn("subscription login", check.detail)

    def test_not_signed_in(self):
        self.assertIn("not signed in", self.verify()[1].detail)

    def test_api_key_next_to_login_blocks(self):  # the "$758 overnight" failure mode
        self.write(OPENAI_API_KEY="sk-stored")
        check = self.verify()[1]
        self.assertTrue(check.failed)
        self.assertIn("bill the API", check.detail)

    def test_api_key_login_blocks(self):
        (self.home / "auth.json").write_text(json.dumps({"auth_mode": "apikey", "OPENAI_API_KEY": "sk-x"}))
        self.assertTrue(self.verify()[1].failed)

    def test_pool_rotation_allowed_unless_pinned(self):
        self.write(account="acct-2")
        self.assertEqual(self.verify()[1].status, "ok")
        pinned = dict(self.conn, account_id="acct-1", pin_account=True)
        self.assertTrue(self.verify(pinned)[1].failed)


class AddCodexTests(unittest.TestCase):
    """`keyfit add codex` from two projects, with two ChatGPT pro logins (~/.codex and ~/codex-globex) in a temporary
    HOME."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.home = self.tmp / "home"
        self.config = self.tmp / "keyfit"
        claims = {"https://api.openai.com/auth": {"chatgpt_plan_type": "pro", "chatgpt_account_id": "acct-1"}}
        for folder in (self.home / ".codex", self.home / "codex-globex"):
            folder.mkdir(parents=True)
            (folder / "auth.json").write_text(json.dumps(
                {"auth_mode": "chatgpt", "OPENAI_API_KEY": None, "tokens": {"id_token": jwt(claims), "access_token": "x"}}))
        for patcher in (mock.patch.dict(os.environ, {"HOME": str(self.home), "KEYFIT_CONFIG_DIR": str(self.config),
                                                     "PATH": "/usr/bin:/bin"}, clear=True),
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

    def test_another_login_on_the_same_plan_never_takes_over_the_first_projects_account(self):
        self.assertEqual(self.run_main("add", "codex", "--project", "acme"),
                         (0, "acme: codex account 'pro' → ChatGPT pro in ~/.codex\n", ""))
        self.assertEqual(self.run_main("add", "codex", "--project", "globex", "--codex-home", "~/codex-globex"),
                         (0, "globex: codex account 'pro-2' → ChatGPT pro in ~/codex-globex\n", ""))
        reg = self.registry()
        self.assertEqual(reg["accounts"]["codex"], {"pro": {"codex_home": "~/.codex", "plan": "pro"},
                                                    "pro-2": {"codex_home": "~/codex-globex", "plan": "pro"}})
        self.assertEqual((reg["projects"]["acme"]["codex"], reg["projects"]["globex"]["codex"]), ("pro", "pro-2"))
        # the same login again is the same account
        self.assertEqual(self.run_main("add", "codex", "--project", "globex", "--codex-home", "~/codex-globex")[1],
                         "globex: codex account 'pro-2' → ChatGPT pro in ~/codex-globex\n")
        self.assertEqual(len(self.registry()["accounts"]["codex"]), 2)

    def test_a_pinned_account_is_never_unpinned_by_another_project(self):
        self.run_main("add", "codex", "--project", "acme", "--pin-account")
        self.assertEqual(self.run_main("add", "codex", "--project", "globex")[1],
                         "globex: codex account 'pro-2' → ChatGPT pro in ~/.codex\n")
        self.assertEqual(self.registry()["accounts"]["codex"]["pro"],
                         {"codex_home": "~/.codex", "plan": "pro", "account_id": "acct-1", "pin_account": True})

    def test_a_shared_name_is_shared_and_another_login_under_it_names_who_changed(self):
        self.run_main("add", "codex", "--project", "acme", "--name", "work")
        self.assertEqual(self.run_main("add", "codex", "--project", "globex", "--name", "work"),
                         (0, "globex: codex account 'work' → ChatGPT pro in ~/.codex\n", ""))  # same login: shared
        self.assertEqual(self.run_main("add", "codex", "--project", "globex", "--name", "work", "--codex-home",
                                       "~/codex-globex"),
                         (0, "globex: codex account 'work' → ChatGPT pro in ~/codex-globex\n"
                             "  account 'work' was codex_home ~/.codex; it changed for acme too\n", ""))

    def test_a_name_that_is_not_a_name_is_refused(self):
        for name in ("../x", "a\nb", ""):
            self.assertEqual(self.run_main("add", "codex", "--project", "acme", "--name", name),
                             (1, "", "a name is letters, digits, '.', '_' and '-' (at most 64)\n"))
        self.assertNotIn("codex", self.registry().get("accounts", {}))


class McpConfigTests(unittest.TestCase):
    """The servers a Codex session loads, and so may have to hide: a second Codex home's config.toml, the profile file
    --profile names, and the .codex/config.toml of the folders Codex trusts. A server Codex doesn't load is never
    named: `-c mcp_servers.NAME.enabled=false` alone would make Codex refuse its config."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.home = self.tmp / "home"
        self.repo = self.tmp / "repo"
        (self.repo / ".git").mkdir(parents=True)
        self.acme = Project("acme", [str(self.repo)], {"linear": {"workspace": "acme", "key": "env:LINEAR_ACME_API_KEY"}})
        self.env = {"HOME": str(self.home)}
        self.write(self.home / ".codex" / "config.toml", '[mcp_servers.paper]\nurl = "http://127.0.0.1:29979/mcp"\n')

    def write(self, path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def trust(self, folder, level="trusted"):
        with open(self.home / ".codex" / "config.toml", "a") as config:
            config.write(f'[projects.{json.dumps(str(folder))}]\ntrust_level = "{level}"\n')

    def hidden(self, *args, cwd=None, env=None):
        out = adjust_args("codex", self.acme, ["exec", *args, "x"], dict(env or self.env), cwd or self.repo,
                          {"globex": ["globex_cloud_*"]})
        return sorted(a.split(".")[1] for a in out if a.startswith("mcp_servers."))

    def test_a_second_codex_home(self):
        work = self.tmp / "accounts" / "work-codex"
        self.write(work / "config.toml", '[mcp_servers.globex_cloud_eu]\ncommand = "globex-mcp"\n')
        self.write(self.home / ".codex" / "config.toml", '[mcp_servers.globex_cloud_home]\ncommand = "globex-mcp"\n')
        self.assertEqual(self.hidden(env={**self.env, "CODEX_HOME": str(work)}), ["globex_cloud_eu"])

    def test_the_profile_file_named_on_the_command_line(self):
        self.write(self.home / ".codex" / "work.config.toml", '[mcp_servers.globex_cloud_eu]\ncommand = "globex-mcp"\n')
        self.assertEqual(self.hidden(), [])
        for given in (["-p", "work"], ["--profile", "work"], ["--profile=work"], ["-pwork"]):
            self.assertEqual(self.hidden(*given), ["globex_cloud_eu"], given)
        self.assertEqual(self.hidden("--", "-p", "work"), [])  # a prompt
        self.write(self.tmp / "work.config.toml", '[mcp_servers.globex_cloud_up]\ncommand = "globex-mcp"\n')
        self.assertEqual(self.hidden("-p", "../../work"), [])  # not a profile name Codex takes

    def test_project_config_only_where_codex_trusts_it(self):
        self.write(self.repo / ".codex" / "config.toml", '[mcp_servers.globex_cloud_eu]\ncommand = "globex-mcp"\n')
        web = self.repo / "web"
        self.write(web / ".codex" / "config.toml", '[mcp_servers.globex_cloud_web]\ncommand = "globex-mcp"\n')
        self.assertEqual(self.hidden(), [])  # untrusted: Codex skips it
        self.trust(self.repo)
        self.assertEqual(self.hidden(), ["globex_cloud_eu"])
        self.assertEqual(self.hidden(cwd=web), ["globex_cloud_eu", "globex_cloud_web"])  # the root's trust covers it
        self.assertEqual(self.hidden("-C", "web"), ["globex_cloud_eu", "globex_cloud_web"])  # the folder Codex works in
        self.trust(web, "untrusted")  # the folder's own entry comes first
        self.assertEqual(self.hidden(cwd=web), ["globex_cloud_eu"])

    def test_project_config_adds_to_your_servers_the_closest_winning(self):
        self.write(self.home / ".codex" / "config.toml",
                   '[mcp_servers.linear]\nurl = "https://mcp.linear.app/mcp"\nbearer_token_env_var = "LINEAR_API_KEY"\n')
        self.trust(self.repo)
        self.assertEqual(self.hidden(), [])
        self.write(self.repo / ".codex" / "config.toml", '[mcp_servers.linear]\nbearer_token_env_var = "LINEAR_GLOBEX_API_KEY"\n')
        self.assertEqual(self.hidden(), ["linear"])

    def test_a_linked_worktree_is_trusted_through_its_main_checkout(self):
        worktree = self.tmp / "worktrees" / "feature"
        entry = self.repo / ".git" / "worktrees" / "feature"
        self.write(entry / "gitdir", f"{worktree / '.git'}\n")
        self.write(worktree / ".git", f"gitdir: {entry}\n")
        self.write(worktree / ".codex" / "config.toml", '[mcp_servers.globex_cloud_eu]\ncommand = "globex-mcp"\n')
        self.assertEqual(self.hidden(cwd=worktree), [])
        self.trust(self.repo)
        self.assertEqual(self.hidden(cwd=worktree), ["globex_cloud_eu"])

    def test_config_files_with_a_byte_order_mark_and_ones_that_cannot_be_parsed(self):
        config = self.home / ".codex" / "config.toml"
        config.write_text('\ufeff[mcp_servers.globex_cloud_eu]\ncommand = "globex-mcp"\n', encoding="utf-8")
        self.assertEqual(self.hidden(), ["globex_cloud_eu"])
        self.write(self.repo / ".codex" / "config.toml", "[mcp_servers.broken\n")
        self.assertEqual(self.hidden(), ["globex_cloud_eu"])  # untrusted: Codex doesn't read it either
        self.trust(self.repo)
        for path in (self.repo / ".codex" / "config.toml", config):
            with self.assertRaises(Refused) as refused:
                self.hidden()
            self.assertIn(f"keyfit: codex not started: Keyfit can't parse {path}, so it can't hide the MCP servers "
                          f"there that acme must not see.", str(refused.exception))
            self.write(self.repo / ".codex" / "config.toml", "")
            config.write_text("[mcp_servers.broken\n")


if __name__ == "__main__":
    unittest.main()
