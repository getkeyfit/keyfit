#!/bin/bash
# Records out/demo.mp4 (and a screenshot of each step): runs the stand-in APIs, builds the demo world (build.sh),
# expires lumen's SSO sign-in, and starts VHS with a bare environment, so nothing of this Mac's shell (keys,
# logins, paths) reaches the recorded terminal. The demo HOME is deleted afterwards.
set -euo pipefail
DEMO="$(cd "$(dirname "$0")" && pwd)"
export PYTHON="${PYTHON:-$(command -v python3)}"
H=/Users/Shared/you

"$PYTHON" -I "$DEMO/stubs/linear_api.py" & LINEAR=$!
"$PYTHON" -I "$DEMO/stubs/anthropic_api.py" & ANTHROPIC=$!
cleanup() {
  kill "$LINEAR" "$ANTHROPIC" 2>/dev/null || true
  if [ -e "$H/.keyfit-demo" ]; then rm -rf "$H"; fi
}
trap cleanup EXIT
sleep 0.5

bash "$DEMO/build.sh"

"$PYTHON" -I - "$H" <<'EOF'
import hashlib, json, sys
from datetime import datetime, timedelta, timezone
home, now = sys.argv[1], datetime.now(timezone.utc)
token = {"startUrl": "https://lumen.awsapps.com/start", "region": "eu-central-1", "accessToken": "stand-in",
         "expiresAt": (now - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")}
open(f"{home}/.aws/sso/cache/{hashlib.sha1(b'lumen').hexdigest()}.json", "w").write(json.dumps(token))
EOF

rm -rf "$DEMO/out"
mkdir -p "$DEMO/out"
cd "$DEMO"
env -i HOME="$HOME" PATH="$(dirname "$(command -v vhs)"):/usr/bin:/bin" ZDOTDIR="$DEMO/zdot" \
  TERM=xterm-256color LANG=en_US.UTF-8 vhs demo.tape
