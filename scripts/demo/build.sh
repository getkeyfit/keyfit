#!/bin/bash
# Builds the demo world from scratch: a HOME with two invented client repos (lumen-web, harbor-api), stand-in AWS
# and Linear accounts, Keyfit (a copy of this repo's package, with Linear's API pointed at the local stand-in) and a
# Claude Code config folder that holds no real login. Run by record.sh, which starts the stand-in APIs first.
set -euo pipefail
DEMO="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$DEMO/../.." && pwd)"
PY="${PYTHON:-$(command -v python3)}"
CLAUDE_BIN="${CLAUDE_BIN:-$(readlink -f "$HOME/.local/bin/claude")}"
H=/Users/Shared/you  # a neutral HOME: Claude Code shows paths under it
LUMEN_KEY=lin_api_StandInLumen7Qk2v9XwR4mT8pLzN3bY6cHd
HARBOR_KEY=lin_api_StandInHarbor5Jf8s2KqW7nB4xRvM9tGe
CLAUDE_KEY=sk-ant-api03-StandInKeyForTheKeyfitDemoOnlyNotARealKey0000000000000000000000000000000000000000AA

"$PY" -c 'import sys; sys.exit(sys.version_info < (3, 11))' || { echo "PYTHON must be Python 3.11 or newer" >&2; exit 1; }
test -x "$CLAUDE_BIN" || { echo "set CLAUDE_BIN to the real claude binary (not a wrapper or shim)" >&2; exit 1; }
CLAUDE_VERSION="$("$CLAUDE_BIN" --version | awk '{print $1}')"
if [ -e "$H" ] && [ ! -e "$H/.keyfit-demo" ]; then echo "$H exists and is not the demo's" >&2; exit 1; fi

rm -rf "$H" "$DEMO/src" "$DEMO/bin" "$DEMO/zdot"
mkdir -p "$H/code/lumen-web" "$H/code/harbor-api" "$H/.aws/sso/cache" "$H/.claude" "$DEMO/src" "$DEMO/bin" "$DEMO/zdot"
touch "$H/.keyfit-demo" "$DEMO/zdot/.zshrc" "$DEMO/zdot/.zshenv"

cp -R "$REPO/keyfit" "$DEMO/src/keyfit"
find "$DEMO/src" -name __pycache__ -prune -exec rm -rf {} +
sed -i '' 's|^GRAPHQL = "https://api.linear.app/graphql"$|GRAPHQL = "http://127.0.0.1:47123/graphql"  # demo: stand-in Linear|' \
  "$DEMO/src/keyfit/adapters/linear.py"
grep -q '127.0.0.1:47123' "$DEMO/src/keyfit/adapters/linear.py"
ln -s "$CLAUDE_BIN" "$DEMO/bin/claude"
sed "1s|.*|#!$PY -I|" "$DEMO/stubs/aws.py" > "$DEMO/bin/aws"
chmod +x "$DEMO/bin/aws"

cat > "$H/.aws/config" <<'EOF'
[profile lumen-prod]
sso_session = lumen
sso_account_id = 210987658023
sso_role_name = Developer
region = eu-central-1

[sso-session lumen]
sso_start_url = https://lumen.awsapps.com/start
sso_region = eu-central-1
sso_registration_scopes = sso:account:access

[profile harbor]
sso_session = harbor
sso_account_id = 384756104417
sso_role_name = Developer
region = eu-west-1

[sso-session harbor]
sso_start_url = https://harbor.awsapps.com/start
sso_region = eu-west-1
sso_registration_scopes = sso:account:access
EOF

# Both SSO sign-ins valid while registering; record.sh expires lumen's right before recording.
"$PY" -I - "$H" <<'EOF'
import hashlib, json, sys
from datetime import datetime, timedelta, timezone
home, now = sys.argv[1], datetime.now(timezone.utc)
for session, hours in (("lumen", 7), ("harbor", 6.2)):
    token = {"startUrl": f"https://{session}.awsapps.com/start", "region": "eu-central-1", "accessToken": "stand-in",
             "refreshToken": "stand-in", "expiresAt": (now + timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ"),
             "registrationExpiresAt": (now + timedelta(days=90)).strftime("%Y-%m-%dT%H:%M:%SZ")}
    path = f"{home}/.aws/sso/cache/{hashlib.sha1(session.encode()).hexdigest()}.json"
    open(path, "w").write(json.dumps(token))
EOF

for p in lumen-web harbor-api; do git -C "$H/code/$p" init -q; done

# Claude Code: its own config folder (so it never finds the Keychain login of ~/.claude), first start and onboarding
# done, both repos trusted, the fake API key approved, and one stand-in Linear MCP server per client.
"$PY" -I - "$H" "$DEMO" "$PY" "$CLAUDE_KEY" "$CLAUDE_VERSION" <<'EOF'
import json, sys
home, demo, py, key, version = sys.argv[1:]
mcp = lambda: {"type": "stdio", "command": py, "args": ["-I", f"{demo}/stubs/linear_mcp.py"], "env": {}}
state = {
    "hasCompletedOnboarding": True, "theme": "dark", "numStartups": 12,
    # what Claude Code records after its first start, so the recording shows no one-time notices
    "hasSeenAutoDefaultNotice": True, "hasResetAutoModeOptInForDefaultOffer": True, "migrationVersion": 14,
    "opusProMigrationComplete": True, "sonnet1m45MigrationComplete": True,
    "officialMarketplaceAutoInstallAttempted": True, "officialMarketplaceAutoInstalled": True,
    "lastReleaseNotesSeen": version, "lastClawdEntranceVersion": version,
    "customApiKeyResponses": {"approved": [key[-20:]], "rejected": []},
    "projects": {f"{home}/code/{p}": {"hasTrustDialogAccepted": True, "hasCompletedProjectOnboarding": True,
                                       "allowedTools": []} for p in ("lumen-web", "harbor-api")},
    "mcpServers": {"linear_lumen": mcp(), "linear_harbor": mcp()},
}
open(f"{home}/.claude/.claude.json", "w").write(json.dumps(state, indent=2))
EOF

cat > "$DEMO/env.zsh" <<EOF
export HOME="$H"
export PATH="$H/.config/keyfit/bin:$DEMO/bin:/usr/bin:/bin"
export LINEAR_LUMEN_API_KEY=$LUMEN_KEY
export LINEAR_HARBOR_API_KEY=$HARBOR_KEY
export ANTHROPIC_API_KEY=$CLAUDE_KEY
export CLAUDE_CONFIG_DIR="$H/.claude"
export ANTHROPIC_BASE_URL=http://127.0.0.1:47124
export CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 DISABLE_AUTOUPDATER=1
export TERM=xterm-256color COLORTERM=truecolor LANG=en_US.UTF-8
unset HISTFILE
PROMPT='%F{245}%~%f \$ '
cd "\$HOME"
EOF

kf() {  # keyfit from the copied package, inside a folder, with only the demo's environment
  local dir="$1"; shift
  (cd "$dir" && env -i HOME="$H" PATH="$DEMO/bin:/usr/bin:/bin" PYTHONPATH="$DEMO/src" \
     LINEAR_LUMEN_API_KEY=$LUMEN_KEY LINEAR_HARBOR_API_KEY=$HARBOR_KEY CLAUDE_CONFIG_DIR="$H/.claude" \
     "$PY" -s -m keyfit "$@" < /dev/null)
}
kf "$H" setup --shell zsh > /dev/null
test -x "$H/.config/keyfit/bin/claude"
kf "$H/code/lumen-web" init --yes > /dev/null
kf "$H/code/harbor-api" init --yes > /dev/null
kf "$H/code/lumen-web" add aws --profile lumen-prod --account 210987658023
kf "$H/code/harbor-api" add aws --profile harbor --account 384756104417
kf "$H/code/lumen-web" add linear --key env:LINEAR_LUMEN_API_KEY --workspace lumen
kf "$H/code/harbor-api" add linear --key env:LINEAR_HARBOR_API_KEY --workspace harbor
kf "$H/code/lumen-web" add mcp linear_lumen
kf "$H/code/harbor-api" add mcp linear_harbor
