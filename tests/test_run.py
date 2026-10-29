"""`keyfit run` starts a command with the project's accounts only once they are checked; `keyfit status` always asks
again instead of trusting the cache."""

import contextlib
import io
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ["KEYFIT_CONFIG_DIR"] = tempfile.mkdtemp()  # tests never touch the real ~/.config/keyfit

from keyfit.adapters import ADAPTERS  # noqa: E402
from keyfit.adapters.base import Adapter  # noqa: E402
from keyfit.checks import Check  # noqa: E402
from keyfit.cli import main  # noqa: E402


class Counted(Adapter):
    """A connection whose online check answers what *answer* says and counts how often it was asked."""

    kind = "counted"

    def __init__(self):
        self.answer, self.asked = "ok", 0

    def inject(self, conn, env):
        env["COUNTED_ACCOUNT"] = conn["id"]

    def local(self, conn, env):
        return Check(self.kind, "ok", conn["id"], "", pending=True)

    def remote(self, conn, env):
        self.asked += 1
        if self.answer == "ok":
            return Check(self.kind, "ok", conn["id"], "account confirmed")
        return Check(self.kind, "fail", conn["id"], "belongs to another account", "sign in again")


class RunTests(unittest.TestCase):
    """Project demo (connection counted → acme) in a temporary repository; the command never really starts."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=self.repo, check=True)
        self.config = self.tmp / "keyfit"
        self.config.mkdir()
        (self.config / "registry.toml").write_text(f'[projects.demo]\nroots = ["{self.repo}"]\ncounted = {{ id = "acme" }}\n')
        self.adapter = Counted()
        self.started = []
        for patcher in (mock.patch.dict(os.environ, {"HOME": str(self.tmp), "PATH": "/usr/bin:/bin",
                                                     "KEYFIT_CONFIG_DIR": str(self.config)}, clear=True),
                        mock.patch.dict(ADAPTERS, counted=self.adapter),
                        mock.patch("os.execvpe", lambda file, args, env: self.started.append((file, args, env)))):
            patcher.start()
            self.addCleanup(patcher.stop)
        cwd = os.getcwd()
        os.chdir(self.repo)
        self.addCleanup(os.chdir, cwd)

    def keyfit(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_the_command_starts_with_the_projects_environment_once_checked(self):
        _, out, err = self.keyfit("run", "--", "terraform", "plan")  # the stand-in exec returns: the code means nothing
        self.assertEqual((out, err, self.adapter.asked), ("", "", 1))
        [(file, args, env)] = self.started
        self.assertEqual((file, args), ("terraform", ["terraform", "plan"]))
        self.assertEqual((env["KEYFIT_PROJECT"], env["COUNTED_ACCOUNT"]), ("demo", "acme"))

    def test_a_failed_check_starts_nothing(self):
        self.adapter.answer = "fail"
        code, out, err = self.keyfit("run", "--", "terraform", "plan")
        self.assertEqual((code, out, self.started), (1, "", []))
        self.assertEqual(err, "keyfit run: demo needs attention\n"
                              "  ✘ counted    acme   belongs to another account\n"
                              "    → sign in again\n")

    def test_without_a_command_it_only_checks(self):
        self.assertEqual(self.keyfit("run"), (0, "demo: checks ok\n", ""))
        self.assertEqual((self.adapter.asked, self.started), (1, []))

    def test_outside_a_registered_project_nothing_starts(self):
        os.chdir(self.tmp)
        code, _, err = self.keyfit("run", "--", "terraform", "plan")
        self.assertEqual((code, self.started, self.adapter.asked), (1, [], 0))
        self.assertIn("keyfit run: this folder is not part of a registered project", err)

    def test_status_asks_again_where_a_launch_trusts_the_cache(self):
        self.keyfit("run", "--", "terraform", "plan")  # a success, cached
        self.keyfit("run", "--", "terraform", "plan")
        self.assertEqual(self.adapter.asked, 1)  # a launch starts from the cache
        self.adapter.answer = "fail"  # now another account
        code, out, _ = self.keyfit("status")
        self.assertEqual((code, self.adapter.asked), (1, 2))
        self.assertIn("✘ counted    acme   belongs to another account", out)
        code, _, _ = self.keyfit("run", "--", "terraform", "plan")  # the failure forgot the cached success
        self.assertEqual((code, self.adapter.asked, len(self.started)), (1, 3, 2))


if __name__ == "__main__":
    unittest.main()
