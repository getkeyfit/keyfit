"""Launch speed (KEY-43): what was made faster keeps every check."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

os.environ["KEYFIT_CONFIG_DIR"] = tempfile.mkdtemp()  # tests never touch the real ~/.config/keyfit

from keyfit import environment  # noqa: E402
from keyfit.adapters import ADAPTERS  # noqa: E402
from keyfit.adapters.base import Adapter  # noqa: E402
from keyfit.checks import Check  # noqa: E402
from keyfit.registry import Project  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


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
        one = Project("demo", ["/tmp"], {"fine": {"id": 1}})
        two = Project("demo", ["/tmp"], {"fine": {"id": 1}, "other": {}})
        ADAPTERS["other"] = Fine()
        self.addCleanup(ADAPTERS.pop, "other")
        self.assertEqual([c.status for c in environment.verify(one, {})], ["ok"])
        self.assertEqual([c.status for c in environment.verify(two, {})], ["ok", "ok"])
        self.assertEqual(environment.verify(Project("demo", ["/tmp"], {}), {}), [])

    def test_a_failing_check_still_stops_the_launch(self):
        for connections in ({"broken": {}}, {"fine": {}, "broken": {}}):
            with self.assertRaises(RuntimeError):  # the shim turns this into "internal error", tool not started
                environment.verify(Project("demo", ["/tmp"], connections), {})


if __name__ == "__main__":
    unittest.main()
