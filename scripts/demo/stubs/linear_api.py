"""Stand-in for Linear's GraphQL API in the demo: answers Keyfit's `{ organization { urlKey name } }` query
for the invented workspaces lumen and harbor, by the key's text. Listens on 127.0.0.1:47123."""

import json
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

WORKSPACES = {"Lumen": ("lumen", "Lumen"), "Harbor": ("harbor", "Harbor")}


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        key = self.headers.get("Authorization", "")
        found = next((v for k, v in WORKSPACES.items() if k in key), None)
        time.sleep(0.25)
        if found is None:
            body, status = {"errors": [{"message": "Authentication required"}]}, 401
        else:
            body, status = {"data": {"organization": {"urlKey": found[0], "name": found[1]}}}, 200
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


HTTPServer(("127.0.0.1", 47123), Handler).serve_forever()
