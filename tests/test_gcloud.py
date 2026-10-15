"""Google Cloud: the project's Google account, the gcloud adapter, `keyfit add google|gcloud` and the gcloud shim, with
a stand-in for gcloud (configurations and sign-ins in a temporary gcloud folder, `auth print-access-token`) and for
Google's tokeninfo and Cloud Resource Manager. The real ~/.config/gcloud, the Keychain and the network are never
touched."""

import contextlib
import io
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

os.environ["KEYFIT_CONFIG_DIR"] = tempfile.mkdtemp()  # tests never touch the real ~/.config/keyfit

from keyfit import cache, cli, environment, secrets, shim  # noqa: E402
from keyfit.adapters import ADAPTERS, gcloud, google  # noqa: E402
from keyfit.adapters.gcloud import GcloudAdapter  # noqa: E402
from keyfit.cli import main  # noqa: E402
from keyfit.registry import Registry  # noqa: E402
from test_vercel import Executed, executable  # noqa: E402

# Fake people and projects; never real ones.
ACME, GLOBEX, PERSONAL = "me@acme.io", "me@globex.io", "someone@gmail.com"
DEPLOYER = "deployer@acme-prod.iam.gserviceaccount.com"
REACH = {ACME: ["acme-prod", "acme-staging"], GLOBEX: ["globex-app"], PERSONAL: ["side-project-1"],
         DEPLOYER: ["acme-prod"]}
CONFIGS = {"default": (PERSONAL, "side-project-1"), "acme": (ACME, "acme-prod"), "globex": (GLOBEX, "globex-app")}

EXPIRED = ("ERROR: (gcloud.auth.print-access-token) There was a problem refreshing your current auth tokens: "
           "('invalid_grant: Bad Request', {'error': 'invalid_grant', 'error_description': 'Bad Request'})\n"
           "Please run:\n\n  $ gcloud auth login\n\nto obtain new credentials.\n")
UNREACHABLE = ("ERROR: (gcloud.auth.print-access-token) There was a problem refreshing your current auth tokens: "
               "HTTPSConnectionPool(host='oauth2.googleapis.com', port=443): Max retries exceeded with url: /token "
               "(Caused by NameResolutionError(\"Failed to resolve 'oauth2.googleapis.com' ([Errno 8] nodename nor "
               "servname provided, or not known)\"))\n")
NO_CREDENTIALS = ("ERROR: (gcloud.auth.print-access-token) Your current active account [{}] does not have any valid "
                  "credentials\nPlease run:\n\n  $ gcloud auth login\n\nto obtain new credentials.\n")

# A gcloud on PATH for the shim to start; Keyfit's own calls go to FakeGcloud (or, in one test, to a script).
FAKE_GCLOUD = """#!/bin/sh
echo "gcloud ran" >&2
exit 0
"""


def token_of(email: str) -> str:
    return f"ya29.fake-{email}"


class FakeGcloud:
    """Stands in for `gcloud auth print-access-token`: the account comes from CLOUDSDK_CORE_ACCOUNT, else the
    configuration CLOUDSDK_ACTIVE_CONFIG_NAME names (as gcloud reads it); impersonation hands out the service
    account's token. Each account has a state: ok, expired, unreachable, hang."""

    def __init__(self, store: Path):
        self.store = store
        self.state = {}
        self.calls = []

    def __call__(self, argv, env, timeout):
        self.calls.append({"argv": argv[1:], "env": dict(env)})
        if argv[1:] != ["auth", "print-access-token"]:
            return 97, "", f"unexpected: {argv[1:]}"
        settings = gcloud.read(env, env.get(gcloud.ACTIVE) or "default") or {}
        account = env.get(gcloud.ACCOUNT) or settings.get("account")
        state = self.state.get(account, "ok")
        if state == "hang":
            raise subprocess.TimeoutExpired(argv, timeout)
        if state == "expired":
            return 1, "", EXPIRED
        if state == "unreachable":
            return 1, "", UNREACHABLE
        if account not in (gcloud.signed_in(env) or []):
            return 1, "", NO_CREDENTIALS.format(account)
        acting = (settings.get("auth") or {}).get("impersonate_service_account")
        return 0, token_of(acting or account) + "\n", ""


def api(url, token, timeout):
    """Stands in for oauth2.googleapis.com/tokeninfo and cloudresourcemanager.googleapis.com/v1/projects/ID."""
    who = next((email for email in REACH if token == token_of(email)), None)
    if url == gcloud.TOKENINFO:
        if who is None:
            return 400, {"error": "invalid_token", "error_description": "Invalid Value"}
        return 200, {"email": who, "email_verified": "true", "expires_in": "3599", "scope": "openid"}
    if url.startswith(gcloud.PROJECTS):
        if who is None:
            return 401, None
        project = url[len(gcloud.PROJECTS):]
        if project not in REACH[who]:
            return 403, None
        state = "DELETE_REQUESTED" if project == "acme-staging" else "ACTIVE"
        return 200, {"projectId": project, "projectNumber": "1", "lifecycleState": state, "name": project}
    return 404, None


class GcloudCase(unittest.TestCase):
    """A temporary HOME with gcloud's folder (configurations default, acme and globex; sign-ins for all three
    people), a gcloud on PATH and three projects: client (Google account acme, configuration acme), mine (globex)
    and plain (none)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.home = self.tmp / "home"
        self.store = self.home / ".config" / "gcloud"
        (self.store / "configurations").mkdir(parents=True)
        (self.store / "active_config").write_text("default")
        for name, (account, project) in CONFIGS.items():
            self.configure(name, account, project)
        self.sign_in(ACME, GLOBEX, PERSONAL, DEPLOYER)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        executable(self.bin / "gcloud", FAKE_GCLOUD)
        self.config = self.tmp / "config"
        self.repos = {}
        for name in ("client", "mine", "plain"):
            repo = self.tmp / name
            repo.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            self.repos[name] = repo
        self.outside = self.tmp / "outside"
        self.outside.mkdir()
        self.env = {"HOME": str(self.home), "PATH": f"{self.bin}{os.pathsep}/usr/bin:/bin",
                    "KEYFIT_CONFIG_DIR": str(self.config)}
        self.gcloud = FakeGcloud(self.store)
        self.fetched = []

        def fetch(url, token, timeout):
            self.fetched.append(url)
            return api(url, token, timeout)
        adapter = ADAPTERS["gcloud"]
        for patcher in (mock.patch("urllib.request.urlopen", side_effect=AssertionError("no network in tests")),
                        mock.patch.object(adapter, "run", self.gcloud), mock.patch.object(adapter, "fetch", fetch),
                        mock.patch.object(secrets, "SECURITY", "/nonexistent/security"),
                        mock.patch.dict(os.environ, self.env, clear=True)):
            patcher.start()
            self.addCleanup(patcher.stop)
        old = os.getcwd()
        self.addCleanup(os.chdir, old)
        os.chdir(self.repos["client"])

    def configure(self, name, account=None, project=None, **auth):
        lines = ["[core]"] + [f"{k} = {v}" for k, v in (("account", account), ("project", project)) if v]
        if auth:
            lines += ["", "[auth]"] + [f"{k} = {v}" for k, v in auth.items()]
        (self.store / "configurations" / f"config_{name}").write_text("\n".join(lines) + "\n")

    def sign_in(self, *accounts):
        """gcloud's credentials.db, as gcloud writes it: one row per account (the value is never read by Keyfit)."""
        with contextlib.closing(sqlite3.connect(self.store / "credentials.db")) as db:
            db.execute('CREATE TABLE IF NOT EXISTS "credentials" (account_id TEXT PRIMARY KEY, value BLOB)')
            db.execute("DELETE FROM credentials")
            db.executemany("INSERT INTO credentials VALUES (?, ?)",
                           [(a, '{"refresh_token": "not a real credential"}') for a in accounts])
            db.commit()

    def register(self):
        reg = Registry(self.config / "registry.toml")
        for name, repo in self.repos.items():
            reg.add_project(name, repo)
        reg.set_account("google", "acme", {"email": ACME})
        reg.set_account("google", "globex", {"email": GLOBEX})
        reg.set_account("gcloud", "acme", {"configuration": "acme", "project": "acme-prod"})
        reg.set_account("gcloud", "globex", {"configuration": "globex", "project": "globex-app"})
        for project, name in (("client", "acme"), ("mine", "globex")):
            reg.set_connection(project, "google", name)
            reg.set_connection(project, "gcloud", name)
        reg.save()
        return reg

    def conn(self, **changes):
        return dict({"name": "acme", "configuration": "acme", "project": "acme-prod", "account": ACME}, **changes)


class AdapterTests(GcloudCase):
    def setUp(self):
        super().setUp()
        self.gc = GcloudAdapter(run=self.gcloud, fetch=api, cli_timeout=5)

    def check(self, conn=None, **extra):
        conn = conn or self.conn()
        env = dict(self.env, **extra)
        self.gc.inject(conn, env)
        return self.gc.verify(conn, env)

    def test_inject_scrubs_what_picks_another_account_and_pins_the_configuration(self):
        env = dict(self.env, CLOUDSDK_ACTIVE_CONFIG_NAME="globex", CLOUDSDK_CORE_ACCOUNT=GLOBEX,
                   CLOUDSDK_CORE_PROJECT="globex-app", CLOUDSDK_CONFIG="/elsewhere", CLOUDSDK_AUTH_ACCESS_TOKEN_FILE="/t",
                   CLOUDSDK_AUTH_IMPERSONATE_SERVICE_ACCOUNT="x@y.iam.gserviceaccount.com",
                   CLOUDSDK_API_ENDPOINT_OVERRIDES_STORAGE="https://evil.example/", CLOUDSDK_COMPUTE_REGION="us-east1",
                   CLOUDSDK_CORE_UNIVERSE_DOMAIN="example.com", GOOGLE_APPLICATION_CREDENTIALS="/keys/globex.json",
                   CLOUDSDK_PYTHON="/opt/python3", CLOUDSDK_PYTHON_SITEPACKAGES="1", CLOUDSDK_PROXY_ADDRESS="proxy",
                   CLOUDSDK_CORE_DISABLE_PROMPTS="1", GOOGLE_CLOUD_PROJECT="kept")
        self.gc.inject(self.conn(), env)
        for var in ("CLOUDSDK_CONFIG", "CLOUDSDK_AUTH_ACCESS_TOKEN_FILE", "CLOUDSDK_AUTH_IMPERSONATE_SERVICE_ACCOUNT",
                    "CLOUDSDK_API_ENDPOINT_OVERRIDES_STORAGE", "CLOUDSDK_COMPUTE_REGION", "CLOUDSDK_CORE_UNIVERSE_DOMAIN",
                    "GOOGLE_APPLICATION_CREDENTIALS"):
            self.assertNotIn(var, env)
        self.assertEqual((env["CLOUDSDK_ACTIVE_CONFIG_NAME"], env["CLOUDSDK_CORE_ACCOUNT"], env["CLOUDSDK_CORE_PROJECT"],
                          env["KEYFIT_GCLOUD"]), ("acme", ACME, "acme-prod", "acme"))
        for var in ("CLOUDSDK_PYTHON", "CLOUDSDK_PYTHON_SITEPACKAGES", "CLOUDSDK_PROXY_ADDRESS",
                    "CLOUDSDK_CORE_DISABLE_PROMPTS", "GOOGLE_CLOUD_PROJECT"):
            self.assertIn(var, env)  # how gcloud runs, and what other tools read, stays
        self.gc.inject(self.conn(config_dir="~/gcloud-acme"), env)
        self.assertEqual(env["CLOUDSDK_CONFIG"], str(Path("~/gcloud-acme").expanduser()))

    def test_the_project_google_account_reaches_gcloud_through_the_registry(self):
        reg = self.register()
        self.assertEqual(reg.get("client").connections["gcloud"],
                         {"configuration": "acme", "project": "acme-prod", "name": "acme", "account": ACME})
        self.assertEqual(reg.get("client").connections["google"], {"email": ACME, "name": "acme"})
        reg.data["projects"]["client"].pop("google")
        self.assertIsNone(reg.get("client").connections["gcloud"]["account"])
        self.assertNotIn("account", reg.data["accounts"]["gcloud"]["acme"])  # stored once, on the Google account

    def test_agent_sessions_get_the_configuration_and_nested_projects_lose_it(self):
        reg = self.register()
        session = environment.build(reg.get("client"), dict(self.env, GOOGLE_APPLICATION_CREDENTIALS="/keys/x.json"))
        self.assertEqual((session["CLOUDSDK_ACTIVE_CONFIG_NAME"], session["CLOUDSDK_CORE_ACCOUNT"],
                          session["CLOUDSDK_CORE_PROJECT"]), ("acme", ACME, "acme-prod"))
        self.assertNotIn("GOOGLE_APPLICATION_CREDENTIALS", session)
        self.assertIn(f"google {ACME} · gcloud acme → acme-prod", session["KEYFIT_SUMMARY"])
        nested = environment.build(reg.get("plain"), session)  # a tool of a project without Google Cloud, from inside
        for var in ("CLOUDSDK_ACTIVE_CONFIG_NAME", "CLOUDSDK_CORE_ACCOUNT", "CLOUDSDK_CORE_PROJECT", "KEYFIT_GCLOUD"):
            self.assertNotIn(var, nested)
        own = environment.build(reg.get("plain"), dict(self.env, CLOUDSDK_ACTIVE_CONFIG_NAME="default",
                                                       GOOGLE_APPLICATION_CREDENTIALS="/keys/mine.json"))
        self.assertEqual((own["CLOUDSDK_ACTIVE_CONFIG_NAME"], own["GOOGLE_APPLICATION_CREDENTIALS"]),
                         ("default", "/keys/mine.json"))  # the user's own, in a project without Google Cloud: kept

    def test_configuration_account_and_project_are_confirmed(self):
        check = self.check()
        self.assertEqual((check.status, check.label), ("ok", "acme → acme-prod"))
        self.assertEqual(check.detail, f"{ACME} · account and project confirmed")
        call = self.gcloud.calls[-1]
        self.assertEqual(call["argv"], ["auth", "print-access-token"])
        self.assertEqual((call["env"]["CLOUDSDK_ACTIVE_CONFIG_NAME"], call["env"]["CLOUDSDK_CORE_DISABLE_PROMPTS"]),
                         ("acme", "1"))
        self.assertNotIn(token_of(ACME), check.detail + check.label + check.fix)

    def test_missing_configuration_fails_with_the_create_command(self):
        (self.store / "configurations" / "config_acme").unlink()
        check = self.check()
        self.assertEqual((check.detail, check.fix), ("gcloud has no configuration acme in ~/.config/gcloud",
                                                     "gcloud config configurations create acme --no-activate"))
        self.assertEqual(self.gcloud.calls, [])  # local checks decide without running gcloud

    def test_configuration_of_another_account(self):
        self.configure("acme", GLOBEX, "acme-prod")
        check = self.check()
        self.assertTrue(check.failed)
        self.assertEqual(check.detail, f"configuration acme uses {GLOBEX}, the project's Google account is {ACME}")
        self.assertEqual(check.fix, f"gcloud config set account {ACME} --configuration acme")  # gcloud has the sign-in
        self.sign_in(GLOBEX)
        self.assertEqual(self.check().fix, f"gcloud auth login {ACME}")  # it has not
        self.sign_in(ACME)
        self.configure("acme", ACME, "acme-prod")
        self.assertEqual(self.check(self.conn(account="Me@Acme.IO")).status, "ok")  # Google addresses ignore case
        self.assertEqual(self.gcloud.calls[-1]["env"]["CLOUDSDK_CORE_ACCOUNT"], ACME)  # as gcloud keeps it

    def test_configuration_without_account_or_sign_in(self):
        self.configure("acme", None, "acme-prod")
        self.assertEqual((self.check().detail, self.check().fix),
                         ("configuration acme has no account", f"gcloud config set account {ACME} --configuration acme"))
        self.configure("acme", ACME, "acme-prod")
        self.sign_in(GLOBEX, PERSONAL)
        check = self.check()
        self.assertEqual((check.detail, check.fix), (f"gcloud is not signed in as {ACME}", f"gcloud auth login {ACME}"))
        (self.store / "credentials.db").unlink()  # never signed in at all
        self.assertEqual(self.check().fix, f"gcloud auth login {ACME}")

    def test_configuration_on_another_project(self):
        self.configure("acme", ACME, "acme-staging")
        check = self.check()
        self.assertEqual((check.detail, check.fix), ("configuration acme works on project acme-staging, the project "
                                                     "expects acme-prod",
                                                     "gcloud config set project acme-prod --configuration acme"))
        self.configure("acme", ACME)
        self.assertEqual(self.check().detail, "configuration acme has no project, the project expects acme-prod")

    def test_credentials_from_a_file_are_not_the_account(self):
        self.configure("acme", ACME, "acme-prod", access_token_file="/tmp/token")
        check = self.check()
        self.assertEqual((check.detail, check.fix), (f"configuration acme sets auth/access_token_file, so gcloud would "
                                                     f"not use {ACME}'s sign-in",
                                                     "gcloud config unset auth/access_token_file --configuration acme"))

    def test_impersonation_is_checked_as_the_service_account(self):
        self.configure("acme", ACME, "acme-prod", impersonate_service_account=DEPLOYER)
        check = self.check()
        self.assertEqual((check.status, check.detail), ("ok", f"{ACME} as {DEPLOYER} · account and project confirmed"))

    def test_project_without_a_google_account(self):
        check = self.check(self.conn(account=None))
        self.assertEqual((check.detail, check.fix), ("the project has no Google account",
                                                     "keyfit add google --email <the project's Google account>"))

    def test_broken_records_fail_before_anything_runs(self):
        self.assertEqual(self.check(self.conn(configuration="Acme_1")).detail, "no gcloud configuration recorded")
        self.assertEqual(self.check(self.conn(project="x")).detail,
                         "the expected project must be a Google Cloud project ID")
        self.assertEqual(self.check(self.conn(project="example.com:acme-prod")).detail,
                         "configuration acme works on project acme-prod, the project expects example.com:acme-prod")
        self.assertEqual(self.gcloud.calls, [])

    def test_expired_or_revoked_sign_in_fails_with_the_sign_in(self):
        self.gcloud.state[ACME] = "expired"
        check = self.check()
        self.assertEqual((check.detail, check.fix, check.transient),
                         (f"gcloud has no valid sign-in for {ACME} (signed out, expired or revoked)",
                          f"gcloud auth login {ACME}", False))

    def test_no_answer_is_transient(self):
        for state in ("unreachable", "hang"):
            self.gcloud.state[ACME] = state
            check = self.check()
            self.assertTrue(check.failed and check.transient, state)
            self.assertEqual(check.fix, "check the network and retry")

    def test_token_of_another_person(self):
        self.gc.fetch = lambda url, token, timeout: api(url, token_of(GLOBEX), timeout)
        check = self.check()
        self.assertEqual(check.detail, f"gcloud's token for configuration acme belongs to {GLOBEX}, the project's Google "
                                       f"account is {ACME}")
        self.gc.fetch = lambda url, token, timeout: api(url, "garbage", timeout)
        self.assertEqual((self.check().detail, self.check().fix),
                         ("Google does not accept the token gcloud printed", f"gcloud auth login {ACME}"))

    def test_project_the_account_cannot_reach(self):
        self.configure("acme", ACME, "globex-app")
        check = self.check(self.conn(project="globex-app"))
        self.assertEqual(check.detail, f"{ACME} cannot reach project globex-app (it does not exist, or the account has "
                                       f"no access)")
        self.assertEqual(check.fix, f"keyfit add gcloud --project-id <a project {ACME} can reach>")
        self.configure("acme", ACME, "acme-staging")
        self.assertEqual(self.check(self.conn(project="acme-staging")).detail,
                         "project acme-staging is not active (DELETE_REQUESTED)")

    def test_network_errors_from_google_are_transient(self):
        def down(url, token, timeout):
            raise OSError("no route")
        self.gc.fetch = down
        check = self.check()
        self.assertTrue(check.failed and check.transient)
        self.gc.fetch = lambda url, token, timeout: (503, None) if url.startswith(gcloud.PROJECTS) else api(url, token, 1)
        self.assertTrue(self.check().transient)

    def test_no_gcloud_to_check_with_is_a_warning(self):
        (self.bin / "gcloud").unlink()
        check = self.check()
        self.assertEqual((check.status, check.detail), ("warn", f"{ACME} · not checked online: gcloud not found on PATH"))

    def test_fingerprint_follows_sign_ins_and_the_configuration(self):
        before = self.gc.fingerprint(self.conn(), self.env)
        self.sign_in(ACME, GLOBEX)
        after = self.gc.fingerprint(self.conn(), self.env)
        self.assertNotEqual(after, before)
        self.configure("acme", ACME, "acme-prod", impersonate_service_account=DEPLOYER)
        self.assertNotEqual(self.gc.fingerprint(self.conn(), self.env), after)

    def test_store_is_read_like_gcloud(self):
        self.assertEqual(gcloud.folder({"HOME": "/h"}), Path("/h/.config/gcloud"))
        self.assertEqual(gcloud.folder({"HOME": "/h", "CLOUDSDK_CONFIG": "/x"}), Path("/x"))
        (self.store / "configurations" / "config_Bad_Name").write_text("[core]\n")
        self.assertEqual(gcloud.configurations(self.env), ["default", "acme", "globex"])
        self.assertEqual(sorted(gcloud.signed_in(self.env)), sorted([ACME, GLOBEX, PERSONAL, DEPLOYER]))
        self.assertEqual(gcloud.active(self.env), "default")
        self.assertEqual(gcloud.active(dict(self.env, CLOUDSDK_ACTIVE_CONFIG_NAME="globex")), "globex")
        files = sorted(p.name for p in self.store.iterdir())
        gcloud.signed_in(self.env)
        self.assertEqual(sorted(p.name for p in self.store.iterdir()), files)  # read-only: no journal, nothing new

    def test_options_and_words_are_read_like_gcloud(self):
        args = ["--project", "p", "compute", "--account=a@b.io", "instances", "list", "--configuration", "c", "--",
                "--project", "x"]
        self.assertEqual(gcloud.options(args), [("--project", "p"), ("--account", "a@b.io"), ("--configuration", "c")])
        self.assertEqual(gcloud.words(args), ["compute", "instances", "list"])
        self.assertEqual(gcloud.words(["auth", "login", "--no-launch-browser", ACME]), ["auth", "login", ACME])
        self.assertEqual(gcloud.words(["--some-option", "auth", "login"]), [])  # unclear: never a sign-in command
        self.assertEqual(gcloud.words(["config", "set", "core/project", "p", "--configuration", "acme"]),
                         ["config", "set", "core/project", "p"])

    def test_access_token_of_a_real_process(self):
        script = self.tmp / "gcloud-script"
        executable(script, f"#!{sys.executable}\nimport json, os, sys\n"
                           "open(os.environ['SEEN'], 'w').write(json.dumps([sys.argv[1:], sorted(os.environ)]))\n"
                           f"print({token_of(ACME)!r})\n")
        seen = self.tmp / "seen.json"
        env = dict(self.env, SEEN=str(seen), GOOGLE_APPLICATION_CREDENTIALS="/keys/x.json")
        GcloudAdapter().inject(self.conn(), env)
        token, reason, state = GcloudAdapter(cli_timeout=30).access_token(str(script), env)
        self.assertEqual((token, state), (token_of(ACME), "ok"), reason)
        argv, names = json.loads(seen.read_text())
        self.assertEqual(argv, ["auth", "print-access-token"])
        self.assertNotIn("GOOGLE_APPLICATION_CREDENTIALS", names)
        self.assertIn("CLOUDSDK_CORE_DISABLE_PROMPTS", names)

    def test_tokeninfo_gets_the_token_in_the_body_never_in_the_url(self):
        sent = []

        class Response(io.BytesIO):
            status = 200

        def opener(req, timeout):
            sent.append(req)
            return Response(json.dumps({"email": ACME}).encode())
        with mock.patch("keyfit.http.urlopen", opener):
            self.assertEqual(gcloud._fetch(gcloud.TOKENINFO, "ya29.secret", 5), (200, {"email": ACME}))
            gcloud._fetch(gcloud.PROJECTS + "acme-prod", "ya29.secret", 5)
        self.assertEqual((sent[0].get_method(), sent[0].full_url, sent[0].data),
                         ("POST", gcloud.TOKENINFO, b"access_token=ya29.secret"))
        self.assertNotIn("Authorization", sent[0].headers)
        self.assertEqual((sent[1].get_method(), sent[1].headers["Authorization"]), ("GET", "Bearer ya29.secret"))
        self.assertNotIn("ya29", sent[1].full_url)

    def test_google_account(self):
        adapter = ADAPTERS["google"]
        check = adapter.verify({"email": ACME, "name": "acme"}, {})
        self.assertEqual((check.status, check.label), ("ok", ACME))
        check = adapter.verify({"email": "not-an-email", "name": "acme"}, {})
        self.assertEqual((check.detail, check.fix), ("not an email address",
                                                     "keyfit add google --email <the project's Google account>"))
        self.assertEqual((google.default_name(PERSONAL), google.default_name("me@Acme-Corp.co.uk"),
                          google.default_name(ACME)), ("personal", "acme-corp-co", "acme"))


class ShimTests(GcloudCase):
    """The gcloud shim, run in-process with os.execve caught."""

    def setUp(self):
        super().setUp()
        self.register()
        cache.clear()

    def run_shim(self, *args, tool="gcloud", cwd=None, **extra):
        seen = {}

        def execve(path, argv, env):
            seen.update(path=path, argv=argv, env=env)
            raise Executed()
        os.chdir(cwd or self.repos["client"])
        err = io.StringIO()
        with mock.patch.dict(os.environ, extra), mock.patch("os.execve", execve), contextlib.redirect_stderr(err):
            try:
                code = shim.main([tool, *args])
            except Executed:
                code = None
        self.stderr = err.getvalue()
        return code, seen.get("argv"), seen.get("env")

    def test_each_project_gets_its_configuration_account_and_project(self):
        code, argv, env = self.run_shim("compute", "instances", "list", CLOUDSDK_ACTIVE_CONFIG_NAME="globex",
                                        GOOGLE_APPLICATION_CREDENTIALS="/keys/globex.json")
        self.assertIsNone(code, self.stderr)
        self.assertEqual(argv, ["gcloud", "compute", "instances", "list"])
        self.assertEqual((env["CLOUDSDK_ACTIVE_CONFIG_NAME"], env["CLOUDSDK_CORE_ACCOUNT"], env["CLOUDSDK_CORE_PROJECT"]),
                         ("acme", ACME, "acme-prod"))
        self.assertNotIn("GOOGLE_APPLICATION_CREDENTIALS", env)
        code, argv, env = self.run_shim("run", "deploy", cwd=self.repos["mine"])
        self.assertEqual((env["CLOUDSDK_ACTIVE_CONFIG_NAME"], env["CLOUDSDK_CORE_ACCOUNT"]), ("globex", GLOBEX))
        self.assertEqual(self.stderr, "")

    def test_project_without_gcloud_runs_as_before(self):
        code, argv, env = self.run_shim("components", "list", cwd=self.repos["plain"],
                                        CLOUDSDK_ACTIVE_CONFIG_NAME="default", GOOGLE_APPLICATION_CREDENTIALS="/k.json")
        self.assertIsNone(code, self.stderr)
        self.assertEqual((argv, env["CLOUDSDK_ACTIVE_CONFIG_NAME"], env["GOOGLE_APPLICATION_CREDENTIALS"]),
                         (["gcloud", "components", "list"], "default", "/k.json"))
        self.assertEqual((self.stderr, self.gcloud.calls), ("", []))

    def test_dry_run_prints_a_project_without_gcloud_instead_of_running_it(self):
        code, argv, _ = self.run_shim("run", "deploy", cwd=self.repos["plain"], KEYFIT_DRY_RUN="1")
        self.assertEqual((code, argv), (0, None), self.stderr)  # never started
        self.assertIn("keyfit: project plain has no gcloud account mapped: gcloud runs without checks, as before "
                      "Keyfit\n  would run: ", self.stderr)
        self.assertIn("gcloud run deploy\n  environment changes (names only): ", self.stderr)

    def test_unregistered_folder_passes_through(self):
        code, argv, env = self.run_shim("--project", "globex-app", "--account", GLOBEX, "projects", "list",
                                        cwd=self.outside, CLOUDSDK_CORE_ACCOUNT=PERSONAL)
        self.assertEqual(argv, ["gcloud", "--project", "globex-app", "--account", GLOBEX, "projects", "list"])
        self.assertEqual(env["CLOUDSDK_CORE_ACCOUNT"], PERSONAL)
        self.assertEqual(self.gcloud.calls, [])

    def test_mismatch_blocks_with_the_fix(self):
        self.configure("acme", PERSONAL, "acme-prod")
        code, argv, _ = self.run_shim("storage", "ls")
        self.assertEqual((code, argv), (1, None))
        self.assertIn("keyfit: gcloud not started, client needs attention", self.stderr)
        self.assertIn(f"✘ gcloud    acme → acme-prod   configuration acme uses {PERSONAL}, the project's Google account "
                      f"is {ACME}\n    → gcloud config set account {ACME} --configuration acme", self.stderr)

    def test_another_configuration_account_or_project_is_refused(self):
        for args, shown in ((["--project", "globex-app", "storage", "ls"], "refusing --project globex-app"),
                            (["storage", "ls", "--project=side-project-1"], "refusing --project side-project-1"),
                            (["--account", GLOBEX, "projects", "list"], f"refusing --account {GLOBEX}"),
                            (["--configuration=globex", "info"], "refusing --configuration globex"),
                            (["--access-token-file", "/tmp/t", "storage", "ls"], "refusing --access-token-file"),
                            (["--flags-file", "flags.yaml", "storage", "ls"], "refusing --flags-file")):
            code, argv, _ = self.run_shim(*args)
            self.assertEqual((code, argv), (1, None), args)
            self.assertIn(shown, self.stderr)
        self.assertIn(f"keyfit: this folder belongs to client (gcloud configuration acme, Google account {ACME}, project "
                      f"acme-prod); refusing --flags-file", self.stderr)
        code, argv, _ = self.run_shim("--project", "acme-prod", "--account", "Me@Acme.io", "storage", "ls")
        self.assertEqual(argv, ["gcloud", "--project", "acme-prod", "--account", "Me@Acme.io", "storage", "ls"])

    def test_sign_in_and_setup_commands_run_without_checks(self):
        self.gcloud.state[ACME] = "expired"
        (self.store / "configurations" / "config_acme").unlink()
        for args in (["config", "configurations", "create", "acme", "--no-activate"],
                     ["config", "set", "account", ACME, "--configuration", "acme"], ["auth", "login", ACME],
                     ["config", "set", "core/project", "acme-prod"], ["auth", "list"], ["config", "configurations", "list"]):
            code, argv, env = self.run_shim(*args, CLOUDSDK_CORE_ACCOUNT=GLOBEX)
            self.assertEqual(argv, ["gcloud", *args], self.stderr)
            self.assertEqual(env["CLOUDSDK_ACTIVE_CONFIG_NAME"], "acme")  # they act on the project's configuration
            self.assertNotIn("CLOUDSDK_CORE_ACCOUNT", env)  # and write into it, not beside it
            self.assertNotIn("CLOUDSDK_CORE_PROJECT", env)
        self.assertEqual(self.gcloud.calls, [])

    def test_signing_in_as_someone_else_is_refused(self):
        code, argv, _ = self.run_shim("auth", "login", GLOBEX)
        self.assertEqual((code, argv), (1, None))
        self.assertIn(f"keyfit: this folder belongs to client, whose Google account is {ACME}; refusing `gcloud auth "
                      f"login {GLOBEX}`: gcloud would make configuration acme use that account.", self.stderr)
        code, argv, _ = self.run_shim("auth", "login", "--no-launch-browser")  # the browser decides: checked afterwards
        self.assertEqual(argv, ["gcloud", "auth", "login", "--no-launch-browser"])

    def test_changing_a_projects_configuration_is_refused(self):
        for args, cwd, shown in (
                (["config", "set", "project", "globex-app"], "client",
                 "refusing `gcloud config set project globex-app` for configuration acme: client uses it with project "
                 "acme-prod"),
                (["config", "set", "account", PERSONAL, "--configuration", "globex"], "plain",
                 f"refusing `gcloud config set account {PERSONAL}` for configuration globex: mine uses it with account "
                 f"{GLOBEX}"),
                (["config", "unset", "core/project"], "client", "for configuration acme: client uses it"),
                (["config", "configurations", "delete", "default", "globex"], "client",
                 "refusing `gcloud config configurations delete globex`: mine uses that configuration"),
                (["config", "configurations", "rename", "acme", "--new-name", "x"], "plain",
                 "refusing `gcloud config configurations rename acme`: client uses that configuration")):
            code, argv, _ = self.run_shim(*args, cwd=self.repos[cwd])
            self.assertEqual((code, argv), (1, None), args)
            self.assertIn(shown, self.stderr)
        (self.store / "active_config").write_text("globex")
        code, argv, _ = self.run_shim("config", "set", "project", "x-project", cwd=self.repos["plain"])
        self.assertEqual(code, 1)  # the active configuration is mine's
        self.assertIn("for configuration globex: mine uses it with project globex-app", self.stderr)
        code, argv, _ = self.run_shim("config", "set", "project", "x-project", "--configuration", "default",
                                      cwd=self.repos["plain"])
        self.assertEqual(argv, ["gcloud", "config", "set", "project", "x-project", "--configuration", "default"])

    def test_session_keeps_gcloud_on_its_project(self):
        code, argv, env = self.run_shim("storage", "ls", cwd=self.repos["plain"], KEYFIT_PROJECT="client")
        self.assertIsNone(code, self.stderr)
        self.assertEqual(env["CLOUDSDK_ACTIVE_CONFIG_NAME"], "acme")
        code, argv, _ = self.run_shim("--project", "globex-app", "storage", "ls", cwd=self.repos["mine"],
                                      KEYFIT_PROJECT="client")
        self.assertEqual(code, 1)

    def test_agents_start_with_the_project_google_account_checked(self):
        executable(self.bin / "claude", "#!/bin/sh\nexit 0\n")
        code, argv, env = self.run_shim("-p", "hi", tool="claude")
        self.assertIsNone(code, self.stderr)
        self.assertEqual((env["CLOUDSDK_ACTIVE_CONFIG_NAME"], env["CLOUDSDK_CORE_ACCOUNT"]), ("acme", ACME))
        self.configure("acme", GLOBEX, "acme-prod")
        cache.clear()
        code, argv, _ = self.run_shim("-p", "hi", tool="claude")
        self.assertEqual((code, argv), (1, None))
        self.assertIn("keyfit: claude not started, client needs attention", self.stderr)
        self.assertIn(f"configuration acme uses {GLOBEX}, the project's Google account is {ACME}", self.stderr)

    def test_sign_in_now_runs_gcloud_auth_login_in_the_projects_configuration(self):
        self.gcloud.state[ACME] = "expired"
        ran = []
        real_run = subprocess.run

        def run(argv, *args, **kwargs):
            if kwargs.get("executable") == str(self.bin / "gcloud"):
                env = kwargs["env"]
                ran.append((list(argv), env.get("CLOUDSDK_ACTIVE_CONFIG_NAME"), "CLOUDSDK_CORE_ACCOUNT" in env))
                self.gcloud.state[ACME] = "ok"
                return subprocess.CompletedProcess(argv, 0)
            return real_run(argv, *args, **kwargs)

        class Terminal(io.StringIO):
            def isatty(self):
                return True
        err = Terminal()
        with mock.patch.object(sys, "stdin", Terminal("\n")), mock.patch("subprocess.run", run), \
                contextlib.redirect_stderr(err), mock.patch("os.execve", side_effect=Executed()):
            with self.assertRaises(Executed):
                shim.main(["gcloud", "storage", "ls"])
        self.assertIn(f"✘ gcloud    acme → acme-prod   gcloud has no valid sign-in for {ACME} (signed out, expired or "
                      f"revoked)\n    → gcloud auth login {ACME}\n  Sign in now? [Y/n] ", err.getvalue())
        self.assertEqual(ran, [(["gcloud", "auth", "login", ACME], "acme", False)])
        self.assertIn("keyfit: signed in, starting gcloud", err.getvalue())

    def test_dry_run_names_only_the_variables(self):
        code, _, _ = self.run_shim("storage", "ls", KEYFIT_DRY_RUN="1", GOOGLE_APPLICATION_CREDENTIALS="/keys/x.json")
        self.assertEqual(code, 0)
        self.assertIn("environment changes (names only): CLOUDSDK_ACTIVE_CONFIG_NAME, CLOUDSDK_CORE_ACCOUNT, "
                      "CLOUDSDK_CORE_PROJECT, GOOGLE_APPLICATION_CREDENTIALS, KEYFIT_GCLOUD", self.stderr)
        self.assertNotIn("/keys/x.json", self.stderr)

    def test_no_token_is_kept_or_shown(self):
        self.run_shim("storage", "ls")
        files = [p for p in self.config.rglob("*") if p.is_file()]
        self.assertTrue(files)
        for path in files:
            self.assertNotIn("ya29", path.read_text())
        self.assertNotIn("ya29", self.stderr)


class AddTests(GcloudCase):
    def setUp(self):
        super().setUp()
        with contextlib.redirect_stdout(io.StringIO()):
            main(["init", "--yes", "--name", "client"])

    def keyfit(self, *args, answers=None):
        out, err = io.StringIO(), io.StringIO()
        stdin = io.StringIO("")
        if answers is not None:
            stdin.isatty = lambda: True
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), mock.patch.object(sys, "stdin", stdin), \
                mock.patch.object(cli, "_ask", side_effect=list(answers or [])):
            code = main(list(args))
        return code, out.getvalue(), err.getvalue()

    def registry(self):
        return tomllib.loads((self.config / "registry.toml").read_text())

    def test_google_account_by_email(self):
        code, out, err = self.keyfit("add", "google", "--email", ACME)
        self.assertEqual((code, out), (0, f"client: google account 'acme' → {ACME}\n"), err)
        reg = self.registry()
        self.assertEqual((reg["projects"]["client"]["google"], reg["accounts"]["google"]["acme"]), ("acme", {"email": ACME}))
        code, out, err = self.keyfit("add", "google", "--email", "Someone@Gmail.com", "--project", "client")
        self.assertEqual(self.registry()["projects"]["client"]["google"], "personal")
        self.assertEqual(self.registry()["accounts"]["google"]["personal"], {"email": PERSONAL})  # as Google writes it
        code, out, err = self.keyfit("add", "google", "--email", "nobody")
        self.assertEqual((code, err), (1, "nobody is not an email address\n"))

    def test_google_account_is_picked_from_gclouds_sign_ins(self):
        code, out, err = self.keyfit("add", "google")
        self.assertEqual(code, 1)
        self.assertIn(f"name the project's Google account: keyfit add google --email <work or personal email> (gcloud "
                      f"knows {ACME}, {GLOBEX}, {PERSONAL})", err)  # people only, never the service account
        code, out, err = self.keyfit("add", "google", answers=["2"])
        self.assertEqual(code, 0, err)
        self.assertIn(f"  1. {ACME}\n  2. {GLOBEX}\n  3. {PERSONAL}\n", out)
        self.assertEqual(self.registry()["accounts"]["google"]["globex"], {"email": GLOBEX})

    def test_gcloud_takes_the_configuration_of_the_projects_google_account(self):
        self.keyfit("add", "google", "--email", GLOBEX)
        code, out, err = self.keyfit("add", "gcloud")
        self.assertEqual(code, 0, err)
        self.assertEqual(out, "client: gcloud account 'globex' → configuration globex, project globex-app\n"
                              f"  ✔ gcloud    globex → globex-app   {GLOBEX} · account and project confirmed\n")
        reg = self.registry()
        self.assertEqual((reg["projects"]["client"]["gcloud"], reg["accounts"]["gcloud"]["globex"]),
                         ("globex", {"configuration": "globex", "project": "globex-app"}))
        self.assertEqual(self.fetched, [gcloud.TOKENINFO, gcloud.PROJECTS + "globex-app"])
        self.assertNotIn("ya29", out + err + (self.config / "registry.toml").read_text())

    def test_gcloud_sets_the_google_account_when_the_project_has_none(self):
        code, out, err = self.keyfit("add", "gcloud", "--configuration", "acme")
        self.assertEqual(code, 0, err)
        self.assertIn(f"client: google account 'acme' → {ACME}\nclient: gcloud account 'acme' → configuration acme, "
                      f"project acme-prod\n", out)
        self.assertEqual(self.registry()["projects"]["client"]["google"], "acme")

    def test_several_configurations_need_a_choice(self):
        code, out, err = self.keyfit("add", "gcloud")
        self.assertEqual(code, 1)
        self.assertIn("pick a gcloud configuration with --configuration NAME (this Mac has default, acme, globex)", err)
        code, out, err = self.keyfit("add", "gcloud", answers=["2"])
        self.assertEqual(code, 0, err)
        self.assertIn(f"  2. acme  ({ACME} → acme-prod)\n", out)
        self.assertEqual(self.registry()["accounts"]["gcloud"]["acme"]["configuration"], "acme")

    def test_another_project_id_is_recorded_with_the_next_step(self):
        self.keyfit("add", "google", "--email", ACME)
        code, out, err = self.keyfit("add", "gcloud", "--project-id", "acme-staging")
        self.assertEqual(code, 0, err)
        self.assertIn("client: gcloud account 'acme' → configuration acme, project acme-staging\n  next: configuration "
                      "acme works on project acme-prod, the project expects acme-staging\n  → gcloud config set project "
                      "acme-staging --configuration acme\n", out)
        self.assertEqual(self.gcloud.calls, [])  # nothing online before the local check passes
        code, out, err = self.keyfit("add", "gcloud", "--project-id", "Not A Project")
        self.assertEqual(code, 1)
        self.assertIn("is not a Google Cloud project ID", err)

    def test_unknown_configuration_and_none_at_all(self):
        code, out, err = self.keyfit("add", "gcloud", "--configuration", "nope")
        self.assertEqual(code, 1)
        self.assertIn("gcloud has no configuration nope in ~/.config/gcloud (it has default, acme, globex)\n"
                      "  → create it: gcloud config configurations create nope --no-activate", err)
        for f in (self.store / "configurations").iterdir():
            f.unlink()
        code, out, err = self.keyfit("add", "gcloud")
        self.assertIn("gcloud has no configurations in ~/.config/gcloud yet.\n  → gcloud config configurations create "
                      "client --no-activate, then: keyfit add gcloud --configuration client", err)
        (self.bin / "gcloud").unlink()
        code, out, err = self.keyfit("add", "gcloud")
        self.assertIn("gcloud is not installed.\n  → brew install --cask gcloud-cli", err)
        self.assertNotIn("gcloud", self.registry()["projects"]["client"])

    def test_a_moved_gcloud_folder_is_recorded(self):
        moved = self.tmp / "gcloud-elsewhere"
        self.store.rename(moved)
        with mock.patch.dict(os.environ, {"CLOUDSDK_CONFIG": str(moved)}):
            code, out, err = self.keyfit("add", "gcloud", "--configuration", "acme")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.registry()["accounts"]["gcloud"]["acme"]["config_dir"], str(moved))
        self.assertEqual(self.gcloud.calls[-1]["env"]["CLOUDSDK_CONFIG"], str(moved))

    def test_guided_init_offers_the_google_account_then_its_configurations(self):
        base = {"registry": None, "project": "client"}
        with mock.patch.object(cli, "_ask", return_value="1"), contextlib.redirect_stdout(io.StringIO()) as out:
            cli._offer_google("client", base)
            cli._offer_gcloud("client", base)
        self.assertIn(f"Google: the project's one Google account, work or personal; its Google tools are checked against "
                      f"it. gcloud is signed in to:\n  1. {ACME}\n", out.getvalue())
        self.assertIn(f"gcloud configurations signed in as {ACME}:\n  1. acme  ({ACME} → acme-prod)\n", out.getvalue())
        reg = self.registry()
        self.assertEqual((reg["projects"]["client"]["google"], reg["projects"]["client"]["gcloud"]), ("acme", "acme"))
        self.assertEqual(self.gcloud.calls, [])  # init checks everything once at the end
        (self.bin / "gcloud").unlink()
        with contextlib.redirect_stdout(io.StringIO()) as out:
            cli._offer_gcloud("client", base)
        self.assertEqual(out.getvalue(), "Google Cloud: gcloud not found on PATH — skipped\n")

    def test_status_and_doctor_show_the_configuration(self):
        self.keyfit("add", "gcloud", "--configuration", "acme")
        code, out, err = self.keyfit("status")
        self.assertIn(f"✔ google    {ACME}\n", out)
        self.assertIn(f"✔ gcloud    acme → acme-prod   {ACME} · account and project confirmed", out)
        with mock.patch.object(secrets, "keychain_reachable", return_value=""):
            code, out, err = self.keyfit("doctor")
        self.assertIn(f"✔ gcloud    {self.bin / 'gcloud'}", out)
        self.assertIn(f"✔ gcloud    acme → acme-prod   {ACME} · configuration acme in ~/.config/gcloud\n", out)


if __name__ == "__main__":
    unittest.main()
