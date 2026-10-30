import os
import signal
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ["KEYFIT_CONFIG_DIR"] = tempfile.mkdtemp()  # tests never touch the real ~/.config/keyfit

from keyfit import shim  # noqa: E402
from keyfit.realbin import MARKER, find_real  # noqa: E402


class RecursionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.enterContext(mock.patch.dict(os.environ, {"KEYFIT_CONFIG_DIR": str(self.tmp / "config")}))
        self.shims = self.tmp / "real-config" / "bin"
        self.shims.mkdir(parents=True)
        (self.shims / MARKER).write_text("")
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        for folder in (self.shims, self.bin):
            exe = folder / "aws"
            exe.write_text("#!/bin/sh\n")
            exe.chmod(exe.stat().st_mode | stat.S_IEXEC)

    def test_shim_folder_is_skipped_even_when_config_dir_points_elsewhere(self):
        # 2026-10-08: KEYFIT_CONFIG_DIR pointed at a temp dir, the real shim folder stayed on PATH,
        # a check ran the `aws` shim instead of aws, which ran the check again — fork exhaustion.
        os.environ["KEYFIT_CONFIG_DIR"] = str(self.tmp / "other-config")
        found = find_real("aws", {"PATH": f"{self.shims}{os.pathsep}{self.bin}"})
        self.assertEqual(found, str(self.bin / "aws"))

    def test_every_call_site_skips_the_superset_wrappers(self):
        # a check, sign-in or launch that found Superset's agent wrapper would run it instead of the tool
        import ast

        package = Path(shim.__file__).resolve().parent
        unskipped = []
        for path in sorted(package.rglob("*.py")):
            for node in ast.walk(ast.parse(path.read_text())):
                func = node.func if isinstance(node, ast.Call) else None
                if getattr(func, "id", getattr(func, "attr", None)) != "find_real":
                    continue
                if not any(k.arg == "skip_wrappers" and getattr(k.value, "value", None) is True for k in node.keywords):
                    unskipped.append(f"{path.relative_to(package)}: find_real({ast.unparse(node.args[0])})")
        self.assertEqual(unskipped, [])

    def test_deep_nesting_stops_instead_of_recursing(self):
        env = {"PATH": f"{self.bin}{os.pathsep}/usr/bin:/bin", "KEYFIT_SHIM_DEPTH": str(shim.MAX_DEPTH),
               "KEYFIT_CONFIG_DIR": os.environ["KEYFIT_CONFIG_DIR"]}
        # a shim that went on would exec over the test runner, which would then exit 0 halfway through the run
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch("os.execve", side_effect=AssertionError("the shim started the tool")):
            self.assertEqual(shim.main(["aws", "s3", "ls"]), 1)

    def test_a_wrapper_calling_the_tool_by_name_stops_in_an_unregistered_folder_too(self):
        # wrapper → shim → wrapper → …: pass-through launches count as well, or this never ends
        config = self.tmp / "config"
        bin_ = config / "bin"
        bin_.mkdir(parents=True)
        (bin_ / MARKER).write_text("")
        entry = Path(shim.__file__).resolve().parent / "_shim_entry.py"
        (bin_ / "aws").write_text(f'#!/bin/sh\nexec "{sys.executable}" -I "{entry}" aws "$@"\n')
        wrappers = self.tmp / "wrappers"
        wrappers.mkdir()
        (wrappers / "aws").write_text("#!/bin/sh\naws \"$@\"\n")  # calls `aws` by name, as a child
        for exe in (bin_ / "aws", wrappers / "aws"):
            exe.chmod(0o755)
        plain = self.tmp / "plain"
        plain.mkdir()
        env = {"PATH": f"{bin_}{os.pathsep}{wrappers}{os.pathsep}/usr/bin:/bin", "HOME": str(self.tmp),
               "KEYFIT_CONFIG_DIR": str(config)}
        # a group of its own: should the chain not stop, everything it started is killed, not left running
        proc = subprocess.Popen(["aws", "s3", "ls"], cwd=plain, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, start_new_session=True)
        try:
            _, stderr = proc.communicate(timeout=60)
        finally:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:  # all of it has exited
                pass
            proc.wait()
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn(f"nested {shim.MAX_DEPTH} levels deep", stderr)


class InternalErrorTests(unittest.TestCase):
    def test_internal_error_names_the_type_never_the_message(self):
        # exception messages can quote what was read (configparser quotes the bad line, which may be a key)
        import contextlib
        import io

        bin_ = Path(tempfile.mkdtemp()).resolve()
        exe = bin_ / "aws"
        exe.write_text("#!/bin/sh\n")
        exe.chmod(0o755)
        err = io.StringIO()
        with mock.patch.dict(os.environ, {"PATH": f"{bin_}{os.pathsep}/usr/bin:/bin", "HOME": str(bin_),
                                          "KEYFIT_CONFIG_DIR": str(bin_ / "config")}, clear=True), \
                mock.patch.object(shim.Registry, "load", side_effect=ValueError("lin_api_SECRETVALUE")), \
                contextlib.redirect_stderr(err):
            self.assertEqual(shim.main(["aws", "s3", "ls"]), 1)
        self.assertIn("internal error (ValueError in ", err.getvalue())
        self.assertNotIn("SECRETVALUE", err.getvalue())


if __name__ == "__main__":
    unittest.main()
