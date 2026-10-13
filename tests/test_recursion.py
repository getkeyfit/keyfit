import os
import stat
import tempfile
import unittest
from pathlib import Path

os.environ["KEYFIT_CONFIG_DIR"] = tempfile.mkdtemp()  # tests never touch the real ~/.config/keyfit

from keyfit import shim  # noqa: E402
from keyfit.realbin import MARKER, find_real  # noqa: E402


class RecursionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
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

    def test_deep_nesting_stops_instead_of_recursing(self):
        saved = dict(os.environ)
        os.environ.update(PATH=f"{self.bin}{os.pathsep}/usr/bin:/bin", KEYFIT_SHIM_DEPTH=str(shim.MAX_DEPTH))
        try:
            self.assertEqual(shim.main(["aws", "s3", "ls"]), 1)
        finally:
            os.environ.clear()
            os.environ.update(saved)

    def test_a_wrapper_calling_the_tool_by_name_stops_in_an_unregistered_folder_too(self):
        # wrapper → shim → wrapper → …: pass-through launches count as well, or this never ends
        import subprocess
        import sys

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
        out = subprocess.run(["aws", "s3", "ls"], cwd=plain, env=env, capture_output=True, text=True, timeout=60)
        self.assertNotEqual(out.returncode, 0)
        self.assertIn(f"nested {shim.MAX_DEPTH} levels deep", out.stderr)


class InternalErrorTests(unittest.TestCase):
    def test_internal_error_names_the_type_never_the_message(self):
        # exception messages can quote what was read (configparser quotes the bad line, which may be a key)
        import contextlib
        import io
        from unittest import mock

        bin_ = Path(tempfile.mkdtemp()).resolve()
        exe = bin_ / "aws"
        exe.write_text("#!/bin/sh\n")
        exe.chmod(0o755)
        err = io.StringIO()
        with mock.patch.dict(os.environ, {"PATH": f"{bin_}{os.pathsep}/usr/bin:/bin"}), \
                mock.patch.object(shim.Registry, "load", side_effect=ValueError("lin_api_SECRETVALUE")), \
                contextlib.redirect_stderr(err):
            self.assertEqual(shim.main(["aws", "s3", "ls"]), 1)
        self.assertIn("internal error (ValueError in ", err.getvalue())
        self.assertNotIn("SECRETVALUE", err.getvalue())


if __name__ == "__main__":
    unittest.main()
