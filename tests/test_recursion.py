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


if __name__ == "__main__":
    unittest.main()
