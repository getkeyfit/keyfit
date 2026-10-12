import base64
import json
import os
import tempfile
import unittest
from pathlib import Path

os.environ["KEYFIT_CONFIG_DIR"] = tempfile.mkdtemp()  # tests never touch the real ~/.config/keyfit

from keyfit.adapters.codex import CodexAdapter  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
