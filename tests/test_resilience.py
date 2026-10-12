import os
import tempfile
import time
import unittest

os.environ["KEYFIT_CONFIG_DIR"] = tempfile.mkdtemp()  # tests never touch the real ~/.config/keyfit

from keyfit import cache, environment  # noqa: E402
from keyfit.adapters import ADAPTERS  # noqa: E402
from keyfit.adapters.base import Adapter  # noqa: E402
from keyfit.adapters.claude import identity_from  # noqa: E402
from keyfit.checks import Check  # noqa: E402
from keyfit.registry import Project  # noqa: E402


class Flaky(Adapter):
    kind = "flaky"

    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = 0

    def inject(self, conn, env):
        pass

    def local(self, conn, env):
        return Check(self.kind, "ok", "x", "", pending=True)

    def remote(self, conn, env):
        self.calls += 1
        return self.answers.pop(0)


OK = Check("flaky", "ok", "x", "account confirmed")
DOWN = Check("flaky", "fail", "x", "did not answer (timed out)", "check the network", transient=True)
WRONG = Check("flaky", "fail", "x", "belongs to another account", "log in again")


class ResilienceTests(unittest.TestCase):
    def setUp(self):
        os.environ["KEYFIT_CONFIG_DIR"] = tempfile.mkdtemp()
        self.project = Project("demo", ["/tmp"], {"flaky": {"id": 1}})

    def run_with(self, adapter):
        ADAPTERS["flaky"] = adapter
        try:
            return environment.verify(self.project, {}, kinds=("flaky",), use_cache=False)[0]
        finally:
            del ADAPTERS["flaky"]

    def test_blip_is_retried_once(self):
        adapter = Flaky([DOWN, OK])
        self.assertEqual(self.run_with(adapter).status, "ok")
        self.assertEqual(adapter.calls, 2)

    def test_offline_falls_back_to_recent_success_as_warning(self):
        self.run_with(Flaky([OK]))  # cached success
        check = self.run_with(Flaky([DOWN, DOWN]))
        self.assertEqual(check.status, "warn")
        self.assertIn("could not re-check now", check.detail)

    def test_offline_without_a_recent_success_fails(self):
        self.assertTrue(self.run_with(Flaky([DOWN, DOWN])).failed)

    def test_wrong_account_never_falls_back(self):
        self.run_with(Flaky([OK]))
        check = self.run_with(Flaky([WRONG]))
        self.assertTrue(check.failed)
        self.assertIn("another account", check.detail)

    def test_success_older_than_a_day_is_not_trusted(self):
        k = cache.key("demo", "flaky", {"id": 1}, "")
        cache.put(k, "ok", "account confirmed")
        self.assertIsNone(cache.get(k, now=time.time() + cache.STALE + 1, max_age=cache.STALE))


class ClaudeIdentityTests(unittest.TestCase):
    def test_org_accounts_pin_the_org_not_the_login(self):
        record = identity_from({"email": "a@x.io", "orgId": "org-1", "orgName": "X"}, "~/.claude")
        self.assertNotIn("email", record)
        self.assertNotIn("pin_email", record)

    def test_personal_accounts_and_explicit_pins_keep_the_login(self):
        self.assertTrue(identity_from({"email": "a@x.io"}, "~/.claude")["pin_email"])
        self.assertTrue(identity_from({"email": "a@x.io", "orgId": "o"}, "~/.claude", pin_email=True)["pin_email"])


if __name__ == "__main__":
    unittest.main()
