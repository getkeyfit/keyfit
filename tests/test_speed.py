"""Launch speed (KEY-43): what was made faster keeps every check."""

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

os.environ["KEYFIT_CONFIG_DIR"] = tempfile.mkdtemp()  # tests never touch the real ~/.config/keyfit

from keyfit import cache, environment  # noqa: E402
from keyfit.adapters import ADAPTERS  # noqa: E402
from keyfit.adapters.base import Adapter  # noqa: E402
from keyfit.adapters.github import git_identity, hosts_file  # noqa: E402
from keyfit.checks import Check  # noqa: E402
from keyfit.cli import _check_shims  # noqa: E402
from keyfit.realbin import MARKER  # noqa: E402
from keyfit.registry import Project  # noqa: E402
from keyfit.shim import TOOLS  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

# Fake gh: answers `auth token --hostname github.com --user <login>` and logs every call.
FAKE_GH = """#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
with open(os.environ["GH_CALLS"], "a") as fh: fh.write(json.dumps(args) + "\\n")
if args[:5] != ["auth", "token", "--hostname", "github.com", "--user"]:
    sys.exit(97)
token = json.loads(os.environ.get("FAKE_GH_TOKENS", "{}")).get(args[5])
if not token: sys.exit(1)
print(token)
"""
WHO = {"tok-octocat": "octocat", "tok-hubot": "hubot"}


def git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


class ImportTests(unittest.TestCase):
    def test_launch_path_imports_no_https_or_thread_pool(self):
        # urllib.request with TLS costs ~30 ms and concurrent.futures ~5 ms: only online checks need them
        code = ("import sys; sys.path.insert(0, %r); import keyfit.shim; print(sorted(m for m in "
                "('urllib.request', 'http.client', 'ssl', 'concurrent.futures') if m in sys.modules))" % str(ROOT))
        out = subprocess.run([sys.executable, "-I", "-S", "-c", code], capture_output=True, text=True, timeout=60)
        self.assertEqual(out.stdout.strip(), "[]", out.stderr)


class Fine(Adapter):
    kind = "fine"

    def inject(self, conn, env):
        pass

    def local(self, conn, env):
        return Check(self.kind, "ok", "x", "", pending=True)

    def remote(self, conn, env):
        return Check(self.kind, "ok", "x", "confirmed")


class Broken(Fine):
    kind = "broken"

    def local(self, conn, env):
        raise RuntimeError("a bug in a check")


class VerifyTests(unittest.TestCase):
    """One connection is checked without a thread pool; results and errors are the same as with one."""

    def setUp(self):
        os.environ["KEYFIT_CONFIG_DIR"] = tempfile.mkdtemp()
        ADAPTERS.update(fine=Fine(), broken=Broken())
        self.addCleanup(lambda: [ADAPTERS.pop(k) for k in ("fine", "broken")])

    def test_one_and_several_connections(self):
        one = Project("demo", ["/tmp"], {"fine": {"id": "1"}})
        two = Project("demo", ["/tmp"], {"fine": {"id": "1"}, "other": {}})
        ADAPTERS["other"] = Fine()
        self.addCleanup(ADAPTERS.pop, "other")
        self.assertEqual([c.status for c in environment.verify(one, {})], ["ok"])
        self.assertEqual([c.status for c in environment.verify(two, {})], ["ok", "ok"])
        self.assertEqual(environment.verify(Project("demo", ["/tmp"], {}), {}), [])

    def test_a_failing_check_still_stops_the_launch(self):
        for connections in ({"broken": {}}, {"fine": {}, "broken": {}}):
            with self.assertRaises(RuntimeError):  # the shim turns this into "internal error", tool not started
                environment.verify(Project("demo", ["/tmp"], connections), {})


class SessionTokenTests(unittest.TestCase):
    """Inside a session Keyfit started, gh keeps the GH_TOKEN the project's check confirmed instead of asking
    gh for it again, but only that exact token, only for that project and while gh's sign-ins are unchanged."""

    def setUp(self):
        os.environ["KEYFIT_CONFIG_DIR"] = tempfile.mkdtemp()
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        (self.tmp / "bin").mkdir()
        gh = self.tmp / "bin" / "gh"
        gh.write_text(FAKE_GH)
        gh.chmod(0o755)
        self.calls = self.tmp / "calls"
        self.calls.write_text("")
        conn = {"login": "octocat", "name": "octocat"}
        self.project = Project("demo", [str(self.tmp)], {"github": conn})
        self.other = Project("other", [str(self.tmp)], {"github": dict(conn)})
        fetch = mock.patch.object(ADAPTERS["github"], "fetch",
                                  lambda token, timeout: (200, {"login": WHO[token]}) if token in WHO else (401, {}))
        fetch.start()
        self.addCleanup(fetch.stop)
        cwd = os.getcwd()
        os.chdir(self.tmp)
        self.addCleanup(os.chdir, cwd)

    def launch(self, project=None, **inherited):
        """What the shim does for `gh`: build the environment, then run the checks with the cache."""
        project = project or self.project
        env = {"HOME": str(self.tmp), "PATH": f"{self.tmp / 'bin'}{os.pathsep}{os.environ['PATH']}",
               "GH_CALLS": str(self.calls), "FAKE_GH_TOKENS": json.dumps({"octocat": "tok-octocat"}), **inherited}
        env = environment.build(project, env, kinds=("github",))
        return env, environment.verify(project, env, kinds=("github",), use_cache=True)

    def asked_gh(self) -> int:
        return len(self.calls.read_text().splitlines())

    def test_session_keeps_the_token_its_check_confirmed(self):
        first, _ = self.launch()
        self.assertEqual(self.asked_gh(), 1)
        env, checks = self.launch(GH_TOKEN=first["GH_TOKEN"], KEYFIT_PROJECT="demo")
        self.assertEqual(self.asked_gh(), 1)
        self.assertEqual(env["GH_TOKEN"], "tok-octocat")
        self.assertEqual([c.status for c in checks], ["ok"])

    def test_a_token_no_check_confirmed_is_replaced(self):
        self.launch()
        env, checks = self.launch(GH_TOKEN="tok-hubot")  # another account's token, valid at GitHub
        self.assertEqual(self.asked_gh(), 2)
        self.assertEqual(env["GH_TOKEN"], "tok-octocat")
        self.assertEqual([c.status for c in checks], ["ok"])

    def test_a_cached_warning_does_not_vouch_for_a_token(self):
        first, _ = self.launch()
        path = cache._path()
        data = json.loads(path.read_text())
        path.write_text(json.dumps({k: {**v, "status": "warn"} for k, v in data.items()}))
        self.launch(GH_TOKEN=first["GH_TOKEN"], KEYFIT_PROJECT="demo")
        self.assertEqual(self.asked_gh(), 2)

    def test_confirmation_is_per_project(self):
        first, _ = self.launch()
        self.launch(self.other, GH_TOKEN=first["GH_TOKEN"])
        self.assertEqual(self.asked_gh(), 2)

    def test_a_gh_sign_in_change_asks_gh_again(self):
        first, _ = self.launch()
        hosts = Path(hosts_file(first))  # `gh auth login/logout/switch` rewrite it
        hosts.parent.mkdir(parents=True)
        hosts.write_text("github.com: {}\n")
        self.launch(GH_TOKEN=first["GH_TOKEN"])
        self.assertEqual(self.asked_gh(), 2)

    def test_after_the_cache_window_gh_is_asked_again(self):
        first, _ = self.launch()
        later = time.time() + cache.TTL + 1
        with mock.patch("time.time", return_value=later):
            self.launch(GH_TOKEN=first["GH_TOKEN"])
        self.assertEqual(self.asked_gh(), 2)

    def test_hosts_file_follows_gh(self):
        self.assertEqual(hosts_file({"HOME": "/h"}), "/h/.config/gh/hosts.yml")
        self.assertEqual(hosts_file({"HOME": "/h", "XDG_CONFIG_HOME": "/x"}), "/x/gh/hosts.yml")


class GitIdentityTests(unittest.TestCase):
    def test_email_and_ssh_host_asked_together(self):
        tmp = Path(tempfile.mkdtemp()).resolve()
        git("init", "-q", cwd=tmp)
        git("config", "user.email", "me@work.io", cwd=tmp)
        self.assertEqual(git_identity(str(tmp))[1], None)  # no origin
        git("remote", "add", "origin", "git@github.com-work:org/repo.git", cwd=tmp)
        self.assertEqual(git_identity(str(tmp)), ("me@work.io", "github.com-work"))
        plain = tmp / "missing"
        self.assertEqual(git_identity(str(plain)), ("", None))


class ShimScriptTests(unittest.TestCase):
    def test_doctor_reads_both_shim_forms(self):
        folder = Path(tempfile.mkdtemp()).resolve()
        (folder / MARKER).write_text("")
        for flags in ("-I", "-I -S"):
            for tool in TOOLS:
                (folder / tool).write_text(f'#!/bin/sh\nexec "/nowhere/python3" {flags} "/nowhere/_shim_entry.py" {tool} "$@"\n')
                (folder / tool).chmod(0o755)
            check = _check_shims(folder)
            self.assertTrue(check.failed, flags)
            self.assertIn("runs /nowhere/python3, which no longer exists", check.detail)


if __name__ == "__main__":
    unittest.main()
