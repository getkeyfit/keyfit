import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

from keyfit import mcp, secrets
from keyfit.adapters.linear import LinearAdapter, parse_envrc
from keyfit.registry import Project

os.environ["KEYFIT_CONFIG_DIR"] = tempfile.mkdtemp()  # tests never touch the real ~/.config/keyfit

GOOD = "lin_api_" + "A" * 40
OTHER = "lin_api_" + "B" * 40


def fake_fetch(table):
    """Return a fetch function that maps a key to (status, body)."""
    def fetch(key, timeout):
        if key not in table:
            return 401, {"errors": [{"message": "Authentication required"}]}
        result = table[key]
        if isinstance(result, Exception):
            raise result
        return 200, {"data": {"organization": {"urlKey": result, "name": result.title()}}}
    return fetch


class LinearTests(unittest.TestCase):
    def setUp(self):
        self.linear = LinearAdapter(fetch=fake_fetch({GOOD: "acme", OTHER: "globex"}))
        self.conn = {"workspace": "acme", "key": "env:LINEAR_ACME_API_KEY"}

    def shell(self, **extra):
        return dict({"PATH": os.environ["PATH"], "LINEAR_ACME_API_KEY": GOOD, "LINEAR_GLOBEX_API_KEY": OTHER,
                     "ACME_LINEAR_API_KEY": GOOD, "LINEAR_API_KEY": OTHER, "INITECH_LINEAR_API_KEY": OTHER}, **extra)

    def test_inject_exposes_only_the_project_key(self):
        env = self.shell()
        self.linear.inject(self.conn, env)
        self.assertEqual(env["LINEAR_API_KEY"], GOOD)
        linear_vars = sorted(n for n in env if "LINEAR" in n and n.endswith("API_KEY"))
        # the project's own variable stays (its MCP server reads it); every other workspace's key is gone
        self.assertEqual(linear_vars, ["LINEAR_ACME_API_KEY", "LINEAR_API_KEY"])
        self.assertEqual(env["KEYFIT_LINEAR_WORKSPACE"], "acme")

    def test_missing_key_never_falls_back_to_inherited_one(self):
        env = self.shell()
        del env["LINEAR_ACME_API_KEY"]
        self.linear.inject(self.conn, env)
        self.assertNotIn("LINEAR_API_KEY", env)  # the inherited generic key was removed, not reused
        check = self.linear.verify(self.conn, env)
        self.assertTrue(check.failed)
        self.assertIn("$LINEAR_ACME_API_KEY", check.detail)

    def test_key_pasted_twice_is_caught_locally(self):
        env = self.shell(LINEAR_ACME_API_KEY=GOOD + GOOD)
        self.linear.inject(self.conn, env)
        check = self.linear.local(self.conn, env)
        self.assertTrue(check.failed)
        self.assertIn("pasted twice", check.detail)

    def test_key_with_trailing_newline_is_caught_locally(self):
        env = self.shell(LINEAR_ACME_API_KEY=GOOD + "\n")
        self.linear.inject(self.conn, env)
        check = self.linear.local(self.conn, env)
        self.assertTrue(check.failed)
        self.assertIn("does not look like a Linear key", check.detail)

    def test_workspace_confirmed(self):
        env = self.shell()
        self.linear.inject(self.conn, env)
        check = self.linear.verify(self.conn, env)
        self.assertEqual(check.status, "ok")
        self.assertIn("workspace confirmed", check.detail)

    def test_key_from_another_workspace_fails(self):
        env = self.shell(LINEAR_ACME_API_KEY=OTHER)
        self.linear.inject(self.conn, env)
        check = self.linear.verify(self.conn, env)
        self.assertTrue(check.failed)
        self.assertIn("'globex'", check.detail)

    def test_revoked_key_fails_with_fix(self):
        env = self.shell(LINEAR_ACME_API_KEY="lin_api_" + "C" * 40)
        self.linear.inject(self.conn, env)
        check = self.linear.verify(self.conn, env)
        self.assertTrue(check.failed)
        self.assertIn("Personal API keys", check.fix)

    def test_network_error_fails(self):
        linear = LinearAdapter(fetch=fake_fetch({GOOD: OSError("down")}))
        env = self.shell()
        linear.inject(self.conn, env)
        self.assertIn("did not answer", linear.verify(self.conn, env).detail)

    def test_redirect_or_server_error_says_so(self):
        linear = LinearAdapter(fetch=lambda key, timeout: (302, {}))  # http.urlopen never follows a redirect
        env = self.shell()
        linear.inject(self.conn, env)
        check = linear.verify(self.conn, env)
        self.assertTrue(check.failed)
        self.assertIn("Linear answered 302", check.detail)

    def test_mcp_config_holds_no_secret(self):
        servers = json.dumps(self.linear.mcp_servers(self.conn))
        self.assertNotIn("lin_api_", servers)
        self.assertIn("${LINEAR_API_KEY}", servers)
        self.assertIn("/mcp/readonly", json.dumps(self.linear.mcp_servers(dict(self.conn, readonly=True))))

    def test_keychain_reference(self):
        tmp = Path(tempfile.mkdtemp())
        fake = tmp / "security"
        fake.write_text("#!/bin/sh\n[ \"$3\" = keyfit ] && [ \"$5\" = linear:acme ] && echo '%s' && exit 0\nexit 44\n" % GOOD)
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
        old, secrets.SECURITY = secrets.SECURITY, str(fake)
        try:
            self.assertEqual(secrets.resolve("keychain:keyfit/linear:acme", {}), GOOD)
            self.assertIsNone(secrets.resolve("keychain:keyfit/linear:other", {}))
        finally:
            secrets.SECURITY = old

    def test_parse_envrc_follows_a_renamed_variable(self):
        text = "export LINEAR_API_KEY=$ACME_LINEAR_API_KEY\nuse aws acme 123456789012\n"
        self.assertEqual(parse_envrc(text, {"ACME_LINEAR_API_KEY": GOOD}), "env:ACME_LINEAR_API_KEY")
        self.assertEqual(parse_envrc(text, {"LINEAR_ACME_API_KEY": GOOD}), "env:LINEAR_ACME_API_KEY")
        self.assertIsNone(parse_envrc("use aws x 123456789012\n", {}))


class McpServerTests(unittest.TestCase):
    """Which Linear MCP servers a session of acme sees: found by their address, their command or their env, and kept
    only when every key they read or carry is acme's."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.home = self.tmp / "home"
        (self.home / ".codex").mkdir(parents=True)
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        self.acme = Project("acme", [str(self.repo)], {"linear": {"workspace": "acme", "key": "env:LINEAR_ACME_API_KEY"}})
        # the session's environment once Keyfit put acme's checked key in it
        self.env = {"HOME": str(self.home), "LINEAR_API_KEY": GOOD, "LINEAR_ACME_API_KEY": GOOD}

    def claude(self, servers, project=None, owners=None):
        (self.home / ".claude.json").write_text(json.dumps({"mcpServers": servers}))
        settings = json.dumps(mcp.claude_settings(project or self.acme, self.env, self.repo, owners))
        self.assertNotIn("lin_api_", settings)  # a key is compared, never copied
        return sorted(s["serverName"] for s in json.loads(settings).get("deniedMcpServers", []))

    def codex(self, toml):
        (self.home / ".codex" / "config.toml").write_text(toml)
        args = mcp.codex_overrides(self.acme, self.env, cwd=self.repo)
        self.assertNotIn("lin_api_", json.dumps(args))
        return sorted(a.split(".")[1] for a in args if a.startswith("mcp_servers."))

    def test_a_key_in_a_local_servers_env_must_be_the_projects(self):
        # the server's own env beats the session's LINEAR_API_KEY
        local = {"command": "npx", "args": ["-y", "linear-mcp-server"]}
        self.assertEqual(self.claude({
            "linear_globex": {**local, "env": {"LINEAR_API_KEY": OTHER}},
            "linear_renamed": {**local, "env": {"TOKEN": OTHER}},
            "linear_var": {**local, "env": {"LINEAR_API_KEY": "${LINEAR_GLOBEX_API_KEY}"}},
            "linear_mine": {**local, "env": {"LINEAR_API_KEY": GOOD}},
            "linear_session": {**local, "env": {"LINEAR_TEAM": "ENG"}},  # reads LINEAR_API_KEY: acme's key
        }), ["linear_globex", "linear_renamed", "linear_var"])

    def test_codex_definitions_name_their_variables_bare(self):
        local = 'command = "npx"\nargs = ["-y", "linear-mcp-server"]\n'
        self.assertEqual(self.codex(
            f'[mcp_servers.linear_globex]\n{local}env = {{ LINEAR_API_KEY = "{OTHER}" }}\n'
            f'[mcp_servers.linear_mine]\n{local}env = {{ LINEAR_API_KEY = "{GOOD}" }}\n'
            f'[mcp_servers.linear_forwarded]\n{local}env_vars = ["LINEAR_GLOBEX_API_KEY"]\nenv = {{ LINEAR_TEAM = "ENG" }}\n'
            '[mcp_servers.linear_headers]\nurl = "https://mcp.linear.app/mcp"\n'
            'env_http_headers = { Authorization = "LINEAR_GLOBEX_API_KEY" }\n'
            f'[mcp_servers.linear_literal]\nurl = "https://mcp.linear.app/mcp"\nhttp_headers = {{ Authorization = "{OTHER}" }}\n'
            '[mcp_servers.linear]\nurl = "https://mcp.linear.app/mcp"\nbearer_token_env_var = "LINEAR_API_KEY"\n'),
            ["linear_forwarded", "linear_globex", "linear_headers", "linear_literal"])

    def test_mcp_remote_reaching_linear(self):
        remote = ["-y", "mcp-remote", "https://mcp.linear.app/mcp"]
        self.assertEqual(self.claude({
            "linear_globex": {"command": "npx", "args": [*remote, "--header", "Authorization: Bearer ${LINEAR_GLOBEX_API_KEY}"]},
            "linear_oauth": {"command": "npx", "args": remote},
            "linear": {"command": "npx", "args": [*remote, "--header", "Authorization: Bearer ${LINEAR_API_KEY}"]},
        }), ["linear_globex", "linear_oauth"])

    def test_the_address_in_any_case_and_with_a_port(self):
        globex = {"Authorization": "Bearer ${LINEAR_GLOBEX_API_KEY}"}
        self.assertEqual(self.claude({
            "upper": {"type": "http", "url": "https://MCP.LINEAR.APP/mcp", "headers": globex},
            "port": {"type": "http", "url": "https://mcp.linear.app:443/mcp", "headers": globex},
            "linear": {"type": "http", "url": "https://mcp.linear.app/mcp", "headers": {"Authorization": "Bearer ${LINEAR_API_KEY}"}},
        }), ["port", "upper"])

    def test_every_variable_counts_not_only_the_first(self):
        self.assertEqual(self.claude({"linear": {"type": "http", "url": "https://mcp.linear.app/mcp", "headers": {
            "X-Note": "${LINEAR_API_KEY}", "Authorization": "Bearer ${LINEAR_GLOBEX_API_KEY:-}"}}}), ["linear"])

    def test_a_server_signed_in_inside_the_agent_is_visible_only_where_claimed(self):
        servers = {"linear_oauth": {"type": "http", "url": "https://mcp.linear.app/mcp"}}
        self.assertEqual(self.claude(servers), ["linear_oauth"])  # which workspace it acts on, only the agent knows
        acme = Project("acme", [str(self.repo)], self.acme.connections, ["linear_oauth"])  # keyfit add mcp linear_oauth
        globex = Project("globex", [str(self.tmp / "globex")],
                         {"linear": {"workspace": "globex", "key": "env:LINEAR_GLOBEX_API_KEY"}})
        owners = {"acme": ["linear_oauth"]}
        self.assertEqual(self.claude(servers, acme, owners), [])
        self.assertEqual(self.claude(servers, globex, owners), ["linear_oauth"])

    def test_without_a_linear_account_every_linear_server_is_hidden(self):
        plain = Project("plain", [str(self.repo)], {})
        self.assertEqual(self.claude({
            "linear": {"type": "http", "url": "https://mcp.linear.app/mcp", "headers": {"Authorization": "Bearer ${LINEAR_API_KEY}"}},
            "linear_local": {"command": "npx", "args": ["linear-mcp-server"], "env": {"LINEAR_API_KEY": GOOD}},
            "paper": {"type": "http", "url": "http://127.0.0.1:29979/mcp"},
        }, plain), ["linear", "linear_local"])


if __name__ == "__main__":
    unittest.main()
