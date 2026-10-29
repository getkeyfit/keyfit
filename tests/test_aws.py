import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from keyfit.adapters.aws import AwsAdapter, config_path, credentials_path, parse_envrc, profile_names, read_profile
from keyfit.cli import main
from keyfit.realbin import find_real
from keyfit.registry import Registry

os.environ["KEYFIT_CONFIG_DIR"] = tempfile.mkdtemp()  # tests never touch the real ~/.config/keyfit

FAKE_AWS = """#!/usr/bin/env python3
import json, os, sys, time
args = sys.argv[1:]
profile = args[args.index("--profile") + 1]
if os.environ.get("AWS_ACCESS_KEY_ID"):
    print("LEAK: inherited AWS_ACCESS_KEY_ID reached aws", file=sys.stderr); sys.exit(99)
spec = json.loads(os.environ.get("FAKE_AWS", "{}")).get(profile, {})
time.sleep(spec.get("sleep", 0))
if "error" in spec:
    print(spec["error"], file=sys.stderr); sys.exit(255)
# an endpoint override (a variable, or the profile's endpoint_url unless ignored) is another host: it answers anything
if any(k.startswith("AWS_ENDPOINT_URL") for k in os.environ) or (
        spec.get("profile_endpoint") and os.environ.get("AWS_IGNORE_CONFIGURED_ENDPOINT_URLS") != "true"):
    print(spec.get("override_account", "000000000000")); sys.exit(0)
print(spec.get("account", "000000000000"))
"""

CONFIG = """
[profile iam]
credential_process = granted credential-process --profile=iam
region = eu-central-1

[profile autologin]
credential_process = granted credential-process --profile=autologin --auto-login

[profile sso]
sso_session = corp
sso_account_id = 111111111111
sso_role_name = Dev

[sso-session corp]
sso_start_url = https://corp.awsapps.com/start
sso_region = eu-central-1

[profile console]
login_session = arn:aws:iam::222222222222:user/dev

[profile role]
role_arn = arn:aws:iam::333333333333:role/Admin
source_profile = iam

[default]
credential_process = granted credential-process --profile=default
"""


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


class AwsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.home = self.tmp / "home"
        (self.home / ".aws" / "sso" / "cache").mkdir(parents=True)
        (self.home / ".aws" / "login" / "cache").mkdir(parents=True)
        (self.home / ".aws" / "config").write_text(CONFIG)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        fake = self.bin / "aws"
        fake.write_text(FAKE_AWS)
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
        self.env = {"HOME": str(self.home), "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}"}
        self.aws = AwsAdapter(timeout=5)

    def env_for(self, conn, fake=None, **extra):
        env = dict(self.env, **extra)
        if fake is not None:
            env["FAKE_AWS"] = json.dumps(fake)
        self.aws.inject(conn, env)
        return env

    def write_sso_token(self, expires_in: timedelta, refresh=True, stamp=iso):
        token = {"accessToken": "x", "expiresAt": stamp(datetime.now(timezone.utc) + expires_in),
                 "registrationExpiresAt": stamp(datetime.now(timezone.utc) + timedelta(days=60))}
        if refresh:
            token["refreshToken"] = "r"
        name = hashlib.sha1(b"corp").hexdigest() + ".json"
        (self.home / ".aws" / "sso" / "cache" / name).write_text(json.dumps(token))

    def write_login_token(self, expires_in: timedelta, account="222222222222", refresh=True, stamp=iso):
        token = {"accessToken": {"accountId": account, "expiresAt": stamp(datetime.now(timezone.utc) + expires_in)}}
        if refresh:
            token["refreshToken"] = "r"
        name = hashlib.sha256(b"arn:aws:iam::222222222222:user/dev").hexdigest() + ".json"
        (self.home / ".aws" / "login" / "cache" / name).write_text(json.dumps(token))

    # inject -------------------------------------------------------------------

    def test_inject_scrubs_inherited_credentials_and_sets_profile(self):
        env = dict(self.env, AWS_ACCESS_KEY_ID="stale", AWS_SECRET_ACCESS_KEY="stale", AWS_SESSION_TOKEN="stale",
                   AWS_PROFILE="other", AWS_DEFAULT_PROFILE="other", AWS_REGION="us-east-1", AWS_CONFIG_FILE="/x")
        self.aws.inject({"profile": "iam", "account": "444444444444"}, env)
        for var in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN",
                    "AWS_DEFAULT_PROFILE", "AWS_REGION", "AWS_CONFIG_FILE"):
            self.assertNotIn(var, env)
        self.assertEqual(env["AWS_PROFILE"], "iam")
        self.assertEqual(env["AWS_EXPECTED_ACCOUNT_ID"], "444444444444")

    def test_inject_sets_region_when_mapped(self):
        env = self.env_for({"profile": "iam", "account": "444444444444", "region": "eu-west-1"})
        self.assertEqual(env["AWS_REGION"], "eu-west-1")

    def test_inject_removes_what_the_docs_say(self):
        # docs/accounts.md: "Inherited AWS keys, tokens, profile, config-file and region variables are removed."
        # Bedrock's API key (AWS_BEARER_TOKEN_<signing name>) comes first in the CLI's token chain.
        documented = ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_SECURITY_TOKEN",
                      "AWS_WEB_IDENTITY_TOKEN_FILE", "AWS_ROLE_ARN", "AWS_ROLE_SESSION_NAME",
                      "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI", "AWS_CONTAINER_CREDENTIALS_FULL_URI",
                      "AWS_CONTAINER_AUTHORIZATION_TOKEN", "AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE",
                      "AWS_SESSION_EXPIRATION", "AWS_CREDENTIAL_EXPIRATION", "AWS_DEFAULT_PROFILE",
                      "AWS_CONFIG_FILE", "AWS_SHARED_CREDENTIALS_FILE", "AWS_DEFAULT_REGION",
                      "AWS_BEARER_TOKEN_BEDROCK", "AWS_BEARER_TOKEN_SOMESERVICE")
        env = dict(self.env, AWS_CLI_AUTO_PROMPT="off", **{var: "inherited" for var in documented})
        self.aws.inject({"profile": "iam", "account": "444444444444"}, env)
        self.assertEqual([var for var in documented if var in env], [])
        self.assertEqual(env["AWS_CLI_AUTO_PROMPT"], "off")  # everything else stays as it was

    def test_the_check_asks_awss_own_sts_not_an_endpoint_override(self):
        # an override host answers the expected account; AWS's own STS knows the profile is in another one
        fake = {"iam": {"account": "555555555555", "override_account": "444444444444"}}
        conn = {"profile": "iam", "account": "444444444444"}
        for override in ({"AWS_ENDPOINT_URL": "https://sts.example"}, {"AWS_ENDPOINT_URL_STS": "https://sts.example"}):
            env = self.env_for(conn, fake, **override)
            c = self.aws.remote(conn, env)
            self.assertTrue(c.failed, override)
            self.assertIn("is signed in to account", c.detail)
        # a profile's endpoint_url (or a services section) counts unless AWS_IGNORE_CONFIGURED_ENDPOINT_URLS is true
        fake["iam"]["profile_endpoint"] = True
        c = self.aws.remote(conn, self.env_for(conn, fake))
        self.assertTrue(c.failed)
        self.assertIn("is signed in to account", c.detail)
        fake["iam"]["account"] = "444444444444"
        self.assertEqual(self.aws.remote(conn, self.env_for(conn, fake, AWS_ENDPOINT_URL_STS="https://x")).detail,
                         "account confirmed")

    # local --------------------------------------------------------------------

    def test_refuses_default_profile_and_bad_account(self):
        self.assertTrue(self.aws.local({"profile": "default", "account": "444444444444"}, self.env).failed)
        self.assertTrue(self.aws.local({"profile": "iam", "account": "12"}, self.env).failed)

    def test_missing_profile_fails_with_fix(self):
        c = self.aws.local({"profile": "nope", "account": "444444444444"}, self.env)
        self.assertTrue(c.failed)
        self.assertIn("aws configure sso --profile nope", c.fix)

    def test_unparsable_credentials_file_fails_without_quoting_it(self):
        # a key pasted on a line of its own: configparser's error would quote it
        secret = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
        (self.home / ".aws" / "credentials").write_text(f"[static]\naws_access_key_id = AKIAEXAMPLE\n{secret}\n")
        check = self.aws.local({"profile": "static", "account": "444444444444"}, self.env)
        self.assertTrue(check.failed)
        self.assertIn("does not parse", check.detail)
        self.assertNotIn(secret, "\n".join(check.lines()))
        (self.home / ".aws" / "credentials").write_text("[static]\naws_access_key_id = AKIAEXAMPLE\n")
        self.assertFalse(self.aws.local({"profile": "static", "account": "444444444444"}, self.env).failed)

    def test_sso_account_mismatch_is_caught_without_network(self):
        c = self.aws.local({"profile": "sso", "account": "999999999999"}, self.env)
        self.assertTrue(c.failed)
        self.assertIn("…1111", c.detail)

    def test_sso_not_signed_in(self):
        c = self.aws.local({"profile": "sso", "account": "111111111111"}, self.env)
        self.assertTrue(c.failed)
        self.assertEqual(c.fix, "aws sso login --sso-session corp")

    def test_sso_valid_token(self):
        self.write_sso_token(timedelta(hours=3))
        c = self.aws.local({"profile": "sso", "account": "111111111111"}, self.env)
        self.assertEqual(c.status, "ok")
        self.assertIn("valid 2h", c.detail)

    def test_sso_expired_token_refreshable_is_pending(self):
        self.write_sso_token(-timedelta(hours=2), refresh=True)
        c = self.aws.local({"profile": "sso", "account": "111111111111"}, self.env)
        self.assertFalse(c.failed)
        self.assertTrue(c.pending)

    def test_sso_expired_token_without_refresh_fails(self):
        self.write_sso_token(-timedelta(hours=2), refresh=False)
        self.assertTrue(self.aws.local({"profile": "sso", "account": "111111111111"}, self.env).failed)

    def test_cache_times_without_a_zone_are_utc(self):
        naive = lambda dt: dt.strftime("%Y-%m-%dT%H:%M:%S")  # noqa: E731 — no Z, no offset
        sso, console = {"profile": "sso", "account": "111111111111"}, {"profile": "console", "account": "222222222222"}
        self.write_sso_token(timedelta(hours=3), stamp=naive)
        self.assertIn("valid 2h", self.aws.local(sso, self.env).detail)
        self.write_sso_token(-timedelta(hours=2), stamp=naive)
        self.assertEqual(self.aws.local(sso, self.env).detail, "SSO token due for refresh")
        self.write_login_token(timedelta(minutes=10), stamp=naive)
        self.assertEqual(self.aws.local(console, self.env).detail, "console sign-in active")
        self.write_login_token(-timedelta(hours=20), stamp=naive)
        self.assertEqual(self.aws.local(console, self.env).detail, "console sign-in expired (12 h limit)")

    def test_section_names_are_read_as_botocore_reads_them(self):
        # botocore splits section names like a shell: extra spaces and quotes still name the profile
        (self.home / ".aws" / "config").write_text(
            '[profile  spaced]\nsso_session = corp\nsso_account_id = 111111111111\n\n'
            '[profile "quoted"]\nsso_session = corp\nsso_account_id = 111111111111\n\n'
            '[sso-session  "corp"]\nsso_start_url = https://corp.awsapps.com/start\n\n'
            '[profile two words here]\naws_access_key_id = AKIAEXAMPLE\n')
        self.write_sso_token(timedelta(hours=3))
        self.assertEqual(profile_names(self.env), ["spaced", "quoted"])
        for name in ("spaced", "quoted"):
            with self.subTest(name):
                self.assertEqual(read_profile(name, self.env).sso_start_url, "https://corp.awsapps.com/start")
                self.assertEqual(self.aws.local({"profile": name, "account": "111111111111"}, self.env).status, "ok")
        check = self.aws.local({"profile": "two", "account": "111111111111"}, self.env)  # three words: no profile
        self.assertIn("no profile 'two'", check.detail)

    def test_variables_in_the_file_names_are_expanded(self):
        moved = self.tmp / "moved"
        moved.mkdir()
        (moved / "config").write_text("[profile elsewhere]\nsso_session = corp\nsso_account_id = 111111111111\n")
        (moved / "credentials").write_text("[static]\naws_access_key_id = AKIAEXAMPLE\n")
        env = dict(self.env, AWS_CONFIG_FILE="$AWS_DIR/config", AWS_SHARED_CREDENTIALS_FILE="${AWS_DIR}/credentials")
        with mock.patch.dict(os.environ, {"AWS_DIR": str(moved)}):  # as botocore expands them: from the environment
            self.assertEqual((config_path(env), credentials_path(env)), (moved / "config", moved / "credentials"))
            self.assertEqual(profile_names(env), ["elsewhere"])
            self.assertEqual(read_profile("static", env).kind, "static")

    def test_console_login_missing_and_expired(self):
        conn = {"profile": "console", "account": "222222222222"}
        self.assertEqual(self.aws.local(conn, self.env).fix, "aws login --profile console")
        self.write_login_token(-timedelta(hours=20))
        self.assertIn("12 h", self.aws.local(conn, self.env).detail)
        self.write_login_token(-timedelta(hours=2))
        self.assertTrue(self.aws.local(conn, self.env).pending)

    def test_console_login_cache_account_mismatch(self):
        self.write_login_token(timedelta(minutes=10), account="555555555555")
        c = self.aws.local({"profile": "console", "account": "222222222222"}, self.env)
        self.assertTrue(c.failed)

    def test_role_arn_account_mismatch(self):
        self.assertTrue(self.aws.local({"profile": "role", "account": "999999999999"}, self.env).failed)

    def test_auto_login_warns(self):
        c = self.aws.local({"profile": "autologin", "account": "444444444444"}, self.env)
        self.assertEqual(c.status, "warn")

    # remote -------------------------------------------------------------------

    def test_remote_confirms_account(self):
        conn = {"profile": "iam", "account": "444444444444"}
        env = self.env_for(conn, {"iam": {"account": "444444444444"}})
        c = self.aws.verify(conn, env)
        self.assertEqual(c.status, "ok", c.detail)
        self.assertIn("account confirmed", c.detail)

    def test_remote_mismatch_fails(self):
        conn = {"profile": "iam", "account": "444444444444"}
        env = self.env_for(conn, {"iam": {"account": "777777777777"}})
        c = self.aws.verify(conn, env)
        self.assertTrue(c.failed)
        self.assertIn("…7777", c.detail)

    def test_remote_error_gives_login_fix(self):
        self.write_sso_token(-timedelta(hours=2), refresh=True)
        conn = {"profile": "sso", "account": "111111111111"}
        env = self.env_for(conn, {"sso": {"error": "Error when retrieving token from sso: Token has expired and refresh failed"}})
        c = self.aws.verify(conn, env)
        self.assertTrue(c.failed)
        self.assertEqual(c.fix, "aws sso login --sso-session corp")

    def test_rejected_static_key_points_at_aws_configure(self):
        (self.home / ".aws" / "credentials").write_text("[static]\naws_access_key_id = AKIAFAKE\n")
        conn = {"profile": "static", "account": "444444444444"}
        env = self.env_for(conn, {"static": {"error": "An error occurred (InvalidClientTokenId) when calling the "
                                                      "GetCallerIdentity operation: The security token included in "
                                                      "the request is invalid."}})
        c = self.aws.verify(conn, env)
        self.assertTrue(c.failed)
        self.assertIn("InvalidClientTokenId", c.detail)
        # a sign-in command: the shim lets it through inside the project although the check fails
        self.assertEqual(c.fix, "aws configure --profile static   (enter a working access key)")

    def test_other_errors_point_at_a_command_the_shim_lets_through(self):
        conn = {"profile": "role", "account": "333333333333"}
        env = self.env_for(conn, {"role": {"error": "An error occurred (AccessDenied) when calling AssumeRole"}})
        self.assertEqual(self.aws.verify(conn, env).fix,
                         "KEYFIT_DISABLE=1 aws sts get-caller-identity --profile role   (shows the full error)")

    def test_the_check_skips_superset_wrapper(self):
        # docs/security.md: the aws Keyfit runs is the first on PATH after its shims and Superset's wrappers
        wrapper = self.home / ".superset" / "bin"
        wrapper.mkdir(parents=True)
        (wrapper / "aws").write_text("#!/bin/sh\necho 999999999999\n")
        (wrapper / "aws").chmod(0o755)
        conn = {"profile": "iam", "account": "444444444444"}
        env = self.env_for(conn, {"iam": {"account": "444444444444"}}, PATH=f"{wrapper}{os.pathsep}{self.env['PATH']}")
        self.assertEqual(self.aws.remote(conn, env).detail, "account confirmed")

    def test_missing_cli_says_how_to_install_it(self):
        conn = {"profile": "iam", "account": "444444444444"}
        c = self.aws.verify(conn, self.env_for(conn, PATH="/usr/bin:/bin"))
        self.assertEqual((c.detail, c.fix), ("aws CLI not found on PATH", "brew install awscli"))

    def test_inject_survives_a_hand_edited_connection(self):
        env = self.env_for({"profile": "iam"})  # no account: the local check says so instead of a KeyError
        self.assertEqual(env["AWS_EXPECTED_ACCOUNT_ID"], "")
        self.assertIn("12-digit", self.aws.local({"profile": "iam"}, env).detail)

    def test_inherited_key_never_reaches_aws(self):
        conn = {"profile": "iam", "account": "444444444444"}
        env = self.env_for(conn, {"iam": {"account": "444444444444"}}, AWS_ACCESS_KEY_ID="inherited")
        self.assertEqual(self.aws.verify(conn, env).status, "ok")

    def test_remote_timeout(self):
        slow = AwsAdapter(timeout=1)
        conn = {"profile": "iam", "account": "444444444444"}
        env = self.env_for(conn, {"iam": {"sleep": 3, "account": "444444444444"}})
        c = slow.verify(conn, env)
        self.assertTrue(c.failed)
        self.assertIn("Keychain prompt", c.detail)

    def test_offline_marks_pending_as_unchecked(self):
        conn = {"profile": "iam", "account": "444444444444"}
        c = self.aws.verify(conn, self.env_for(conn), remote=False)
        self.assertEqual(c.status, "warn")
        self.assertIn("not checked online", c.detail)

    # helpers ------------------------------------------------------------------

    def test_find_real_skips_shim_dir(self):
        shims = self.tmp / "keyfit" / "bin"
        shims.mkdir(parents=True)
        (shims / "aws").write_text("#!/bin/sh\n")
        (shims / "aws").chmod(0o755)
        old = os.environ.get("KEYFIT_CONFIG_DIR")
        os.environ["KEYFIT_CONFIG_DIR"] = str(self.tmp / "keyfit")
        try:
            found = find_real("aws", {"PATH": f"{shims}{os.pathsep}{self.bin}"})
        finally:
            if old is None:
                os.environ.pop("KEYFIT_CONFIG_DIR")
            else:
                os.environ["KEYFIT_CONFIG_DIR"] = old
        self.assertEqual(found, str(self.bin / "aws"))

    def test_parse_envrc(self):
        self.assertEqual(parse_envrc("export X=1\nuse aws acme 123456789012\n"),
                         {"profile": "acme", "account": "123456789012"})
        self.assertIsNone(parse_envrc("use node 20\n"))

    def test_add_aws_takes_account_from_sso_profile_without_network(self):
        repo = self.tmp / "addrepo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        reg = self.tmp / "add-registry.toml"
        main(["--registry", str(reg), "init", "--yes", "--root", str(repo), "--name", "demo"])
        self.write_sso_token(timedelta(hours=1))
        saved = {k: os.environ.get(k) for k in ("HOME", "PATH", "FAKE_AWS")}
        os.environ.update(HOME=str(self.home), PATH=self.env["PATH"],
                          FAKE_AWS=json.dumps({"sso": {"account": "111111111111"}}))
        try:
            code = main(["--registry", str(reg), "add", "aws", "--project", "demo", "--profile", "sso"])
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        self.assertEqual(code, 0)
        self.assertEqual(Registry.load(reg).get("demo").connections["aws"],
                         {"profile": "sso", "account": "111111111111"})

    def test_add_aws_keeps_the_expanded_config_file(self):
        repo = self.tmp / "addrepo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        reg = self.tmp / "add-registry.toml"
        main(["--registry", str(reg), "init", "--yes", "--root", str(repo), "--name", "demo"])
        (self.home / ".aws" / "config").rename(self.home / ".aws" / "config-acme")
        self.write_sso_token(timedelta(hours=1))
        with mock.patch.dict(os.environ, {"HOME": str(self.home), "PATH": self.env["PATH"],
                                          "AWS_CONFIG_FILE": "$HOME/.aws/config-acme",
                                          "FAKE_AWS": json.dumps({"sso": {"account": "111111111111"}})}):
            code = main(["--registry", str(reg), "add", "aws", "--project", "demo", "--profile", "sso"])
        self.assertEqual(code, 0)
        self.assertEqual(Registry.load(reg).get("demo").connections["aws"],
                         {"profile": "sso", "config_file": "~/.aws/config-acme", "account": "111111111111"})

    def test_init_imports_use_aws_from_envrc(self):
        repo = self.tmp / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        (repo / ".envrc").write_text("use aws iam 444444444444\n")
        reg = self.tmp / "registry.toml"
        self.assertEqual(main(["--registry", str(reg), "init", "--yes", "--root", str(repo), "--name", "demo"]), 0)
        self.assertEqual(Registry.load(reg).get("demo").connections["aws"],
                         {"profile": "iam", "account": "444444444444"})


if __name__ == "__main__":
    unittest.main()
