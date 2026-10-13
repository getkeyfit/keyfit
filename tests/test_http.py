import http.server
import os
import subprocess
import sys
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from keyfit import http as keyfit_http


class HttpTests(unittest.TestCase):
    def test_clean_environment_still_has_ca_certificates(self):
        """In a terminal without SSL_CERT_FILE (iTerm, VS Code), Keyfit must still verify TLS."""
        root = Path(__file__).resolve().parents[1]
        code = ("import sys; sys.path.insert(0, %r); from keyfit import http; "
                "print(http.context().cert_store_stats()['x509_ca'])" % str(root))
        out = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True,
                             env={"PATH": "/usr/bin:/bin", "HOME": os.environ.get("HOME", "/tmp")})
        self.assertGreater(int(out.stdout.strip() or 0), 0, out.stderr)


class RedirectTests(unittest.TestCase):
    def serve(self, handler):
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_address[1]}"

    def test_a_redirect_is_not_followed_with_the_token(self):
        seen = []

        class Elsewhere(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                seen.append(self.headers.get("Authorization"))
                self.send_response(200)
                self.end_headers()

            def log_message(self, *args):
                pass

        target = self.serve(Elsewhere)

        class Redirecting(Elsewhere):
            def do_GET(self):
                self.send_response(302)
                self.send_header("Location", target + "/collect")
                self.end_headers()

        start = self.serve(Redirecting)
        req = urllib.request.Request(start + "/user", headers={"Authorization": "Bearer tok-SECRET"})
        with self.assertRaises(urllib.error.HTTPError) as caught:
            keyfit_http.urlopen(req, 5)
        self.assertEqual(caught.exception.code, 302)
        self.assertEqual(seen, [])  # the token never reached the other address


if __name__ == "__main__":
    unittest.main()
