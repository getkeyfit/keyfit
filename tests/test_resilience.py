import os
import tempfile
import time
import unittest
from unittest import mock

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
NOT_CONFIRMED = Check("flaky", "warn", "x", "account not confirmed")
SHELL_KEYS = Check("flaky", "warn", "x", "GROQ_API_KEY from your shell", scrub=("GROQ_API_KEY",))


class ResilienceTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(mock.patch.dict(os.environ, {"KEYFIT_CONFIG_DIR": tempfile.mkdtemp()}))  # restored after
        self.project = Project("demo", ["/tmp"], {"flaky": {"id": "1"}})

    def run_with(self, adapter, use_cache=False):
        ADAPTERS["flaky"] = adapter
        try:
            return environment.verify(self.project, {}, kinds=("flaky",), use_cache=use_cache)[0]
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
        self.assertTrue(check.transient)  # the shim prints it for every tool, not only the agents

    def test_offline_without_a_recent_success_fails(self):
        self.assertTrue(self.run_with(Flaky([DOWN, DOWN])).failed)

    def test_wrong_account_never_falls_back(self):
        self.run_with(Flaky([OK]))
        check = self.run_with(Flaky([WRONG]))
        self.assertTrue(check.failed)
        self.assertIn("another account", check.detail)

    def test_unknown_kind_fails_instead_of_going_unchecked(self):
        typo = Project("demo", ["/tmp"], {"githb": {"login": "octocat", "note": "lin_api_SECRETVALUE"}})
        check = environment.verify(typo, {}, remote=False)[0]
        self.assertTrue(check.failed)
        self.assertIn("unknown connection", check.detail)
        self.assertNotIn("SECRETVALUE", "\n".join(check.lines()))  # the table itself is never printed

    def test_success_older_than_a_day_is_not_trusted(self):
        k = cache.key("demo", "flaky", {"id": "1"}, "")
        cache.put(k, "ok", "account confirmed")
        self.assertIsNone(cache.get(k, now=time.time() + cache.STALE + 1, max_age=cache.STALE))

    def test_offline_launch_trusts_a_success_for_a_day_only(self):
        for hours, status in ((23, "warn"), (25, "fail")):
            with self.subTest(hours=hours):
                os.environ["KEYFIT_CONFIG_DIR"] = tempfile.mkdtemp()
                with mock.patch("keyfit.cache.time.time", return_value=time.time() - hours * 3600):
                    self.run_with(Flaky([OK]))  # the success, *hours* ago
                adapter = Flaky([DOWN, DOWN])
                self.assertEqual(self.run_with(adapter, use_cache=True).status, status)
                self.assertEqual(adapter.calls, 2)  # older than 30 minutes: asked again (and retried once)

    def test_failure_forgets_the_cached_success(self):
        self.run_with(Flaky([OK]))  # a launch, or keyfit status
        self.assertTrue(self.run_with(Flaky([WRONG])).failed)  # keyfit status: now another account
        adapter = Flaky([WRONG])
        self.assertTrue(self.run_with(adapter, use_cache=True).failed)  # the next launch asks again
        self.assertEqual(adapter.calls, 1)

    def test_offline_after_a_failure_does_not_fall_back_to_the_success_before_it(self):
        self.run_with(Flaky([OK]))
        self.run_with(Flaky([WRONG]))
        self.assertTrue(self.run_with(Flaky([DOWN, DOWN])).failed)

    def test_only_successes_are_cached_and_a_warning_forgets_one(self):
        self.run_with(Flaky([OK]))
        self.assertEqual(self.run_with(Flaky([NOT_CONFIRMED])).status, "warn")
        for _ in range(2):
            adapter = Flaky([NOT_CONFIRMED])
            self.assertEqual(self.run_with(adapter, use_cache=True).status, "warn")
            self.assertEqual(adapter.calls, 1)  # not served from the cache: asked on every launch

    def test_shell_key_names_are_still_cached(self):
        self.run_with(Flaky([SHELL_KEYS]))  # OpenCode's sessions start without the keys it named
        adapter = Flaky([])
        check = self.run_with(adapter, use_cache=True)
        self.assertEqual((check.status, check.scrub, adapter.calls), ("warn", ("GROQ_API_KEY",), 0))


class ClaudeIdentityTests(unittest.TestCase):
    def test_org_accounts_record_the_org_not_the_login(self):
        record = identity_from({"email": "a@x.io", "orgId": "org-1", "orgName": "X"}, "~/.claude")
        self.assertEqual(record, {"config_dir": "~/.claude", "org_id": "org-1", "org_name": "X"})

    def test_personal_accounts_note_the_login_without_pinning_it(self):
        self.assertEqual(identity_from({"email": "a@x.io"}, "~/.claude"), {"config_dir": "~/.claude", "email": "a@x.io"})


if __name__ == "__main__":
    unittest.main()
