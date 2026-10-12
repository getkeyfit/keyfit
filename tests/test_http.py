import os
import subprocess
import sys
import unittest
from pathlib import Path


class HttpTests(unittest.TestCase):
    def test_clean_environment_still_has_ca_certificates(self):
        """In a terminal without SSL_CERT_FILE (iTerm, VS Code), Keyfit must still verify TLS."""
        root = Path(__file__).resolve().parents[1]
        code = ("import sys; sys.path.insert(0, %r); from keyfit import http; "
                "print(http.context().cert_store_stats()['x509_ca'])" % str(root))
        out = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True,
                             env={"PATH": "/usr/bin:/bin", "HOME": os.environ.get("HOME", "/tmp")})
        self.assertGreater(int(out.stdout.strip() or 0), 0, out.stderr)


if __name__ == "__main__":
    unittest.main()
