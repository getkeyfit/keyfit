#!/usr/bin/env python3
"""Stand-in for the AWS CLI in the demo (build.sh installs it as bin/aws): answers the two commands Keyfit runs
(sts get-caller-identity, sso login) for the invented profiles lumen-prod and harbor, from the SSO token files in
the demo HOME. The sign-in opens no browser and finishes after two seconds."""

import hashlib
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

PROFILES = {"lumen-prod": ("lumen", "210987658023"), "harbor": ("harbor", "384756104417")}
args = sys.argv[1:]


def cache(session: str) -> str:
    name = hashlib.sha1(session.encode()).hexdigest() + ".json"
    return os.path.join(os.environ["HOME"], ".aws", "sso", "cache", name)


def option(name: str) -> str | None:
    return args[args.index(name) + 1] if name in args and args.index(name) + 1 < len(args) else None


now = datetime.now(timezone.utc)

if args[:2] == ["sts", "get-caller-identity"]:
    profile = option("--profile") or os.environ.get("AWS_PROFILE", "")
    if profile not in PROFILES:
        print(f"\nThe config profile ({profile}) could not be found", file=sys.stderr)
        sys.exit(255)
    session, account = PROFILES[profile]
    try:
        token = json.load(open(cache(session)))
        expires = datetime.fromisoformat(token["expiresAt"].replace("Z", "+00:00"))
    except (OSError, ValueError, KeyError):
        expires = now
    if expires <= now:
        print("\nError when retrieving token from sso: Token has expired and refresh failed", file=sys.stderr)
        sys.exit(255)
    time.sleep(0.3)
    print(account)
    sys.exit(0)

if args[:2] == ["sso", "login"]:
    session = option("--sso-session") or PROFILES.get(option("--profile") or "", ("",))[0]
    print("Attempting to automatically open the SSO authorization page in your default browser.", flush=True)
    time.sleep(2.2)
    token = {"startUrl": f"https://{session}.awsapps.com/start", "region": "eu-central-1",
             "accessToken": "stand-in", "refreshToken": "stand-in",
             "expiresAt": (now + timedelta(hours=8)).strftime("%Y-%m-%dT%H:%M:%SZ"),
             "registrationExpiresAt": (now + timedelta(days=90)).strftime("%Y-%m-%dT%H:%M:%SZ")}
    os.makedirs(os.path.dirname(cache(session)), exist_ok=True)
    with open(cache(session), "w") as f:
        json.dump(token, f)
    print(f"Successfully logged into Start URL: https://{session}.awsapps.com/start")
    sys.exit(0)

if args == ["--version"]:
    print("aws-cli/2.31.0 (stand-in for the Keyfit demo)")
    sys.exit(0)

print(f"stand-in aws: {' '.join(args)} is not part of the demo", file=sys.stderr)
sys.exit(1)
