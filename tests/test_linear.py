import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

from keyfit import secrets
from keyfit.adapters.linear import LinearAdapter, parse_envrc

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


if __name__ == "__main__":
    unittest.main()
