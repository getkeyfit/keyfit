"""Stand-in for Anthropic's API (ANTHROPIC_BASE_URL) in the demo: Claude Code's start-up requests get an empty
answer, so a session with the demo's fake key starts without warnings. No prompt is ever sent in the demo.
Listens on 127.0.0.1:47124."""

import json
from http.server import BaseHTTPRequestHandler, HTTPServer


class Handler(BaseHTTPRequestHandler):
    def answer(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        status = 404 if "settings" in self.path or "policy" in self.path else 200
        data = json.dumps({} if status == 200 else {"type": "error", "error": {"type": "not_found_error"}}).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = do_POST = do_PUT = do_HEAD = answer

    def log_message(self, *args):
        pass


HTTPServer(("127.0.0.1", 47124), Handler).serve_forever()
