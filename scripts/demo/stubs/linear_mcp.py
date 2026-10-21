"""Stand-in Linear MCP server (stdio, newline-delimited JSON-RPC) for the demo: lists tools, runs none."""

import json
import sys

TOOLS = ["list_issues", "get_issue", "save_issue", "list_comments", "save_comment", "list_projects", "get_project",
         "save_project", "list_teams", "get_team", "list_users", "get_user", "list_cycles", "list_documents",
         "get_document", "list_issue_labels", "list_issue_statuses", "search_documentation"]

for line in sys.stdin:
    try:
        msg = json.loads(line)
    except ValueError:
        continue
    if "id" not in msg:
        continue
    method, result = msg.get("method"), None
    if method == "initialize":
        version = (msg.get("params") or {}).get("protocolVersion") or "2025-06-18"
        result = {"protocolVersion": version, "capabilities": {"tools": {}},
                  "serverInfo": {"name": "linear", "version": "1.0.0"}}
    elif method == "tools/list":
        result = {"tools": [{"name": t, "description": t.replace("_", " "),
                             "inputSchema": {"type": "object", "properties": {}}} for t in TOOLS]}
    elif method == "ping":
        result = {}
    if result is None:
        reply = {"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32601, "message": "not part of the demo"}}
    else:
        reply = {"jsonrpc": "2.0", "id": msg["id"], "result": result}
    sys.stdout.write(json.dumps(reply) + "\n")
    sys.stdout.flush()
