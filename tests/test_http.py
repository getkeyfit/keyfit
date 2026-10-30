import http.server
import io
import json
import os
import subprocess
import sys
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

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



class ChecksTests(unittest.TestCase):
    def test_every_online_check_goes_through_keyfit_http_to_its_own_address(self):
        """A check that opened its request with urllib's own urlopen would follow a redirect with the token."""
        from keyfit.adapters import cloudflare, convex, gemini, github, linear, supabase, vercel
        sent = []

        class Response(io.BytesIO):
            status = 200

        def opener(req, timeout):
            sent.append(req)
            return Response(json.dumps({}).encode())
        checks = {  # gcloud's (also Firebase's and the Google MCP servers') and google_mcp._renew have tests of their own
            "github": (lambda: github._fetch("tok-SECRET", 5), "https://api.github.com/user"),
            "linear": (lambda: linear._fetch("lin_api_SECRET", 5), "https://api.linear.app/graphql"),
            "vercel": (lambda: vercel._fetch("/v2/user", "tok-SECRET", 5), "https://api.vercel.com/v2/user"),
            "supabase": (lambda: supabase._fetch("/v1/projects", "tok-SECRET", 5),
                         "https://api.supabase.com/v1/projects"),
            "cloudflare": (lambda: cloudflare._fetch("/user/tokens/verify", "tok-SECRET", 5),
                           "https://api.cloudflare.com/client/v4/user/tokens/verify"),
            "convex": (lambda: convex._fetch("/v1/teams", "tok-SECRET", 5), "https://api.convex.dev/v1/teams"),
            "gemini": (lambda: gemini._models("AIzaSECRET", 5),
                       "https://generativelanguage.googleapis.com/v1beta/models?pageSize=1"),
        }
        with mock.patch("keyfit.http.urlopen", opener), \
                mock.patch.object(urllib.request.OpenerDirector, "open", side_effect=AssertionError("urllib's opener")):
            for name, (check, address) in checks.items():
                with self.subTest(name):
                    sent.clear()
                    check()
                    self.assertEqual([req.full_url for req in sent], [address])
                    self.assertIn("SECRET", json.dumps(dict(sent[0].header_items())))  # a header, never the address


if __name__ == "__main__":
    unittest.main()
