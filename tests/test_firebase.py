"""Firebase: the firebase adapter, `keyfit add firebase` and the firebase shim, with a stand-in for the firebase CLI
(its sign-ins and folder bindings in a temporary configstore, `firebase projects:list` renewing a token) and for
Google's tokeninfo and the Firebase Management API. The real ~/.config/configstore, the Keychain and the network are
never touched."""

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import tomllib
import unittest
from pathlib import Path
from unittest import mock

os.environ["KEYFIT_CONFIG_DIR"] = tempfile.mkdtemp()  # tests never touch the real ~/.config/keyfit

from keyfit import cache, cli, environment, secrets, shim  # noqa: E402
from keyfit.adapters import ADAPTERS, firebase  # noqa: E402
from keyfit.adapters.firebase import FirebaseAdapter  # noqa: E402
from keyfit.adapters.gcloud import TOKENINFO  # noqa: E402
from keyfit.cli import main  # noqa: E402
from keyfit.registry import Registry  # noqa: E402
from test_vercel import Executed, executable  # noqa: E402

# Fake people and projects; never real ones.
ACME, GLOBEX, PERSONAL = "me@acme.io", "me@globex.io", "someone@gmail.com"
REACH = {ACME: ["acme-prod", "acme-staging"], GLOBEX: ["globex-app"], PERSONAL: ["side-project-1"]}
FAILED = '{\n  "status": "error",\n  "error": "Failed to list Firebase projects. See firebase-debug.log for more info."\n}'

# A firebase on PATH for the shim to start; Keyfit's own calls go to FakeFirebase (or, in one test, to a script).
FAKE_FIREBASE = """#!/bin/sh
echo "firebase ran" >&2
exit 0
"""


def token_of(email: str) -> str:
    return f"ya29.fake-{email}"


def tokens(email: str, fresh: bool = True) -> dict:
    """An account's tokens as the CLI keeps them: an expired access token is one Google no longer takes."""
    return {"access_token": token_of(email) if fresh else f"ya29.stale-{email}",
            "refresh_token": f"1//not-a-real-refresh-token-{email}", "expires_in": 3599, "token_type": "Bearer",
            "expires_at": int((time.time() + (3600 if fresh else -60)) * 1000)}


def user(email: str, signed_in_at: int = 1) -> dict:
    """The claims of Google's ID token the CLI keeps for a sign-in."""
    return {"iss": "accounts.google.com", "email": email, "email_verified": True, "sub": str(sum(map(ord, email))),
            "iat": signed_in_at, "exp": signed_in_at + 3600}


class FakeFirebase:
    """Stands in for `firebase --account EMAIL projects:list --json --non-interactive`, the call that lets the CLI
    renew an account's access token: each account has a state — ok (renewed), expired (the CLI's JSON error), hang,
    broken (no node)."""

    def __init__(self, case):
        self.case = case
        self.state = {}
        self.calls = []

    def __call__(self, argv, cwd, env, timeout):
        self.calls.append({"argv": argv[1:], "cwd": cwd, "env": dict(env)})
        if argv[1:2] != ["--account"] or argv[3:] != ["projects:list", "--json", "--non-interactive"]:
            return 97, "", f"unexpected: {argv[1:]}"
        email, state = argv[2], self.state.get(argv[2], "ok")
        if state == "hang":
            raise subprocess.TimeoutExpired(argv, timeout)
        if state == "broken":
            return 127, "", "env: node: No such file or directory\n"
        if state == "expired":
            return 2, FAILED, "- Preparing the list of your Firebase projects\n"
        self.case.renew(email)
        return 0, json.dumps({"status": "success", "result": []}), ""


def api(url, token, timeout):
    """Stands in for oauth2.googleapis.com/tokeninfo and firebase.googleapis.com/v1beta1/projects/ID."""
    who = next((email for email in REACH if token == token_of(email)), None)
    if url == TOKENINFO:
        if who is None:
            return 400, {"error": "invalid_token", "error_description": "Invalid Value"}
        return 200, {"email": who, "email_verified": "true", "expires_in": "3599", "scope": "openid"}
    if url.startswith(firebase.PROJECTS):
        if who is None:
            return 401, None
        project = url[len(firebase.PROJECTS):]
        if project not in REACH[who]:
            return 403, None
        state = "DELETED" if project == "acme-staging" else "ACTIVE"
        return 200, {"projectId": project, "projectNumber": "1", "displayName": project, "state": state}
    return 404, None


class FirebaseCase(unittest.TestCase):
    """A temporary HOME with the firebase CLI signed in as three people (the default login PERSONAL, ACME and GLOBEX
    added), four repositories and a firebase on PATH. client and mine are Firebase folders bound to ACME and GLOBEX;
    mono keeps its Firebase folder in app/ (bound to ACME), so its top has no firebase.json; plain has no Firebase.
    Projects: client and mono (Google account acme, Firebase project acme-prod), mine (globex, globex-app), plain."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.home = self.tmp / "home"
        self.store = self.home / ".config" / "configstore" / "firebase-tools.json"
        self.store.parent.mkdir(parents=True)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        executable(self.bin / "firebase", FAKE_FIREBASE)
        self.config = self.tmp / "config"
        self.repos = {}
        for name in ("client", "mine", "mono", "plain"):
            repo = self.tmp / name
            repo.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            self.repos[name] = repo
        self.app = self.repos["mono"] / "app"
        self.app.mkdir()
        for folder, aliases in ((self.repos["client"], {"default": "acme-prod", "staging": "acme-staging"}),
                                (self.repos["mine"], {"default": "globex-app"}), (self.app, {"default": "acme-prod"})):
            self.firebase_folder(folder, aliases)
        self.outside = self.tmp / "outside"
        self.outside.mkdir()
        self.data = {"motd": {"minVersion": "3.0.5"}, "user": user(PERSONAL), "tokens": tokens(PERSONAL),
                     "additionalAccounts": [{"user": user(e), "tokens": tokens(e)} for e in (ACME, GLOBEX)],
                     "activeAccounts": {str(self.repos["client"]): ACME, str(self.repos["mine"]): GLOBEX,
                                        str(self.app): ACME}}
        self.save()
        self.env = {"HOME": str(self.home), "PATH": f"{self.bin}{os.pathsep}/usr/bin:/bin",
                    "KEYFIT_CONFIG_DIR": str(self.config)}
        self.firebase = FakeFirebase(self)
        self.fetched = []

        def fetch(url, token, timeout):
            self.fetched.append(url)
            return api(url, token, timeout)
        adapter = ADAPTERS["firebase"]
        for patcher in (mock.patch("urllib.request.urlopen", side_effect=AssertionError("no network in tests")),
                        mock.patch.object(adapter, "run", self.firebase), mock.patch.object(adapter, "fetch", fetch),
                        mock.patch.object(secrets, "SECURITY", "/nonexistent/security"),
                        mock.patch.dict(os.environ, self.env, clear=True)):
            patcher.start()
            self.addCleanup(patcher.stop)
        old = os.getcwd()
        self.addCleanup(os.chdir, old)
        os.chdir(self.repos["client"])

    def firebase_folder(self, folder: Path, aliases: dict | None):
        (folder / "firebase.json").write_text('{"hosting": {"public": "dist"}}\n')
        if aliases is not None:
            (folder / ".firebaserc").write_text(json.dumps({"projects": aliases}, indent=2) + "\n")

    def save(self):
        self.store.write_text(json.dumps(self.data, indent=2))

    def account(self, email):
        if self.data.get("user", {}).get("email") == email:
            return self.data
        return next(a for a in self.data["additionalAccounts"] if a["user"]["email"] == email)

    def expire(self, *emails):
        for email in emails:
            self.account(email)["tokens"] = tokens(email, fresh=False)
        self.save()

    def renew(self, email):
        """What the CLI writes when it renews an access token: the account's new tokens, the same sign-in."""
        self.data = json.loads(self.store.read_text())
        self.account(email)["tokens"] = tokens(email)
        self.save()

    def bind(self, folder, email):
        self.data["activeAccounts"][str(folder)] = email
        self.save()

    def register(self):
        reg = Registry(self.config / "registry.toml")
        for name, repo in self.repos.items():
            reg.add_project(name, repo)
        reg.set_account("google", "acme", {"email": ACME})
        reg.set_account("google", "globex", {"email": GLOBEX})
        reg.set_account("firebase", "acme-prod", {"project": "acme-prod"})
        reg.set_account("firebase", "globex-app", {"project": "globex-app"})
        for project, google, project_id in (("client", "acme", "acme-prod"), ("mono", "acme", "acme-prod"),
                                            ("mine", "globex", "globex-app")):
            reg.set_connection(project, "google", google)
            reg.set_connection(project, "firebase", project_id)
        reg.save()
        return reg

    def conn(self, **changes):
        return dict({"name": "acme-prod", "project": "acme-prod", "account": ACME}, **changes)


class AdapterTests(FirebaseCase):
    def setUp(self):
        super().setUp()
        self.fb = FirebaseAdapter(run=self.firebase, fetch=api, cli_timeout=5)

    def check(self, conn=None, **extra):
        conn = conn or self.conn()
        env = dict(self.env, **extra)
        self.fb.inject(conn, env)
        return self.fb.verify(conn, env)

    def test_inject_scrubs_what_would_act_instead_of_the_sign_in(self):
        env = dict(self.env, FIREBASE_TOKEN="1//theirs", GOOGLE_APPLICATION_CREDENTIALS="/keys/globex.json",
                   GOOGLE_CLOUD_PROJECT="kept", XDG_CONFIG_HOME="/kept", FIREBASE_CLI_EXPERIMENTS="kept")
        self.fb.inject(self.conn(), env)
        self.assertNotIn("FIREBASE_TOKEN", env)
        self.assertNotIn("GOOGLE_APPLICATION_CREDENTIALS", env)
        for var in ("GOOGLE_CLOUD_PROJECT", "XDG_CONFIG_HOME", "FIREBASE_CLI_EXPERIMENTS"):
            self.assertIn(var, env)
        self.assertEqual(set(env) - set(self.env), {"GOOGLE_CLOUD_PROJECT", "XDG_CONFIG_HOME",
                                                     "FIREBASE_CLI_EXPERIMENTS"})  # nothing is set

    def test_the_project_google_account_reaches_firebase_through_the_registry(self):
        reg = self.register()
        self.assertEqual(reg.get("client").connections["firebase"],
                         {"project": "acme-prod", "name": "acme-prod", "account": ACME})
        reg.data["projects"]["client"].pop("google")
        self.assertIsNone(reg.get("client").connections["firebase"]["account"])

    def test_agent_sessions_lose_other_tokens_and_keep_them_without_firebase(self):
        reg = self.register()
        session = environment.build(reg.get("client"), dict(self.env, FIREBASE_TOKEN="1//theirs",
                                                            GOOGLE_APPLICATION_CREDENTIALS="/keys/x.json"))
        self.assertNotIn("FIREBASE_TOKEN", session)
        self.assertNotIn("GOOGLE_APPLICATION_CREDENTIALS", session)
        self.assertIn(f"google {ACME} · firebase acme-prod", session["KEYFIT_SUMMARY"])
        own = environment.build(reg.get("plain"), dict(self.env, FIREBASE_TOKEN="1//mine"))
        self.assertEqual(own["FIREBASE_TOKEN"], "1//mine")  # the user's own, in a project without Firebase: kept

    def test_sign_in_binding_and_project_are_confirmed(self):
        check = self.check()
        self.assertEqual((check.status, check.label), ("ok", "acme-prod"))
        self.assertEqual(check.detail, f"{ACME}, bound at {self.repos['client']} · account and project confirmed")
        self.assertEqual(self.firebase.calls, [])  # a fresh access token: the CLI is not needed
        self.assertNotIn(token_of(ACME), check.detail + check.label + check.fix)
        os.chdir(self.repos["client"] / ".git")  # a subfolder: the CLI's folder is the one with firebase.json
        self.assertEqual(self.check().status, "ok")

    def test_an_expired_access_token_is_renewed_by_the_cli_first(self):
        self.expire(ACME)
        check = self.check()
        self.assertEqual((check.status, check.detail[-29:]), ("ok", "account and project confirmed"))
        call = self.firebase.calls[-1]
        self.assertEqual(call["argv"], ["--account", ACME, "projects:list", "--json", "--non-interactive"])
        self.assertNotEqual(call["cwd"], str(self.repos["client"]))  # elsewhere: its log never lands in the project
        self.assertFalse(Path(call["cwd"]).exists())
        self.assertEqual((call["env"].get("NO_UPDATE_NOTIFIER"), "FIREBASE_TOKEN" in call["env"]), ("1", False))

    def test_a_sign_in_the_cli_cannot_renew(self):
        self.expire(ACME, PERSONAL)
        self.firebase.state.update({ACME: "expired", PERSONAL: "expired"})
        check = self.check()
        self.assertEqual((check.detail, check.fix, check.transient),
                         (f"the firebase CLI could not renew its sign-in for {ACME} (expired or revoked): sign in "
                          f"again as {ACME}", "firebase login:add", False))
        self.bind(self.repos["client"], PERSONAL)  # the default login renews with --reauth
        check = self.check(self.conn(account=PERSONAL, project="acme-prod"))
        self.assertEqual(check.fix, "firebase login --reauth")

    def test_no_answer_is_transient(self):
        self.expire(ACME)
        self.firebase.state[ACME] = "hang"
        check = self.check()
        self.assertTrue(check.failed and check.transient)
        self.assertEqual(check.detail, "the firebase CLI did not answer within 5 s")
        self.firebase.state[ACME] = "expired"  # the CLI reports a network failure as expired credentials

        def down(url, token, timeout):
            raise OSError("no route")
        self.fb.fetch = down
        check = self.check()
        self.assertEqual((check.detail, check.fix, check.transient),
                         ("Google did not answer (network?)", "check the network and retry", True))

    def test_a_cli_that_cannot_run(self):
        self.expire(ACME)
        self.firebase.state[ACME] = "broken"
        check = self.check()
        self.assertEqual((check.detail, check.transient),
                         ("firebase projects:list failed: env: node: No such file or directory", False))
        self.assertEqual(check.fix, f"KEYFIT_DISABLE=1 firebase projects:list --account {ACME}   (shows the full error)")
        (self.bin / "firebase").unlink()
        check = self.check()
        self.assertEqual((check.status, check.detail),
                         ("warn", f"{ACME}, bound at {self.repos['client']} · not checked online: no firebase CLI "
                                  f"here to renew its sign-in"))
        (self.repos["client"] / "node_modules" / ".bin").mkdir(parents=True)  # the project's own, as npx runs it
        executable(self.repos["client"] / "node_modules" / ".bin" / "firebase", FAKE_FIREBASE)
        self.firebase.state[ACME] = "ok"
        self.assertEqual(self.check().status, "ok")
        self.assertEqual(firebase.firebase_bin(self.env, self.repos["client"]),
                         str(self.repos["client"] / "node_modules" / ".bin" / "firebase"))

    def test_not_signed_in_or_not_as_the_account(self):
        self.data["additionalAccounts"] = [{"user": user(GLOBEX), "tokens": tokens(GLOBEX)}]
        self.save()
        check = self.check()
        self.assertEqual((check.detail, check.fix), (f"the firebase CLI is not signed in as {ACME} (it has "
                                                     f"{PERSONAL}, {GLOBEX})", f"firebase login:add {ACME}"))
        self.store.write_text("{}")
        self.assertEqual((self.check().detail, self.check().fix), ("the firebase CLI is not signed in", "firebase login"))
        self.store.unlink()
        self.assertEqual(self.check().fix, "firebase login")
        self.store.write_text("{not json")
        self.assertEqual(self.check().detail, "~/.config/configstore/firebase-tools.json is not JSON (the firebase CLI "
                                              "empties it at its next start)")
        self.assertEqual(self.firebase.calls, [])

    def test_folder_bound_to_another_account_or_to_none(self):
        self.bind(self.repos["client"], GLOBEX)
        check = self.check()
        self.assertEqual((check.detail, check.fix),
                         (f"the firebase CLI's folder {self.repos['client']} is bound to {GLOBEX}, the project's "
                          f"Google account is {ACME}", f"firebase login:use {ACME}"))
        del self.data["activeAccounts"][str(self.repos["client"])]
        self.save()
        self.assertEqual(self.check().detail, f"the firebase CLI's folder {self.repos['client']} uses the default login "
                                              f"{PERSONAL}, the project's Google account is {ACME}")
        self.assertEqual(self.check(self.conn(account=PERSONAL, project="acme-prod")).detail[:16], "someone@gmail.co")
        self.bind(self.repos["client"], ACME.upper())  # Google addresses ignore case
        self.assertEqual(self.check().status, "ok")

    def test_another_firebase_project(self):
        check = self.check(self.conn(project="globex-app"))
        self.assertEqual((check.detail, check.fix),
                         (f"the firebase CLI works on acme-prod in {self.repos['client']} (the default in .firebaserc), "
                          f"the project expects globex-app", "firebase use globex-app"))
        self.data["activeProjects"] = {str(self.repos["client"]): "staging"}  # `firebase use staging` (an alias)
        self.save()
        self.assertEqual(self.check().detail, f"the firebase CLI works on acme-staging in {self.repos['client']} "
                                              f"(picked with `firebase use`), the project expects acme-prod")
        self.data["activeProjects"] = {str(self.tmp): "acme-prod"}  # a folder above counts too
        self.save()
        self.assertEqual(self.check().status, "ok")
        del self.data["activeProjects"]
        self.save()
        (self.repos["client"] / ".firebaserc").unlink()
        self.assertEqual((self.check().detail, self.check().fix),
                         (f"no Firebase project selected in {self.repos['client']} (.firebaserc has no default)",
                          "firebase use acme-prod"))
        self.firebase_folder(self.repos["client"], {"prod": "acme-prod"})  # the only alias is taken
        self.assertEqual(self.check().status, "ok")

    def test_outside_every_firebase_folder_only_the_default_login_counts(self):
        os.chdir(self.repos["mono"])
        check = self.check()
        self.assertEqual((check.status, check.detail),
                         ("warn", f"no firebase.json here, so the firebase CLI uses its default login {PERSONAL}; "
                                  f"Keyfit's firebase adds --account {ACME} · account and project confirmed"))
        self.data["user"], self.data["additionalAccounts"][0]["user"] = user(ACME), user(PERSONAL)
        self.data["tokens"], self.data["additionalAccounts"][0]["tokens"] = tokens(ACME), tokens(PERSONAL)
        self.save()
        check = self.check()
        self.assertEqual((check.status, check.detail),
                         ("ok", f"{ACME} (default login, no firebase.json here) · account and project confirmed"))
        os.chdir(self.app)
        self.assertEqual(self.check().detail, f"{ACME}, bound at {self.app} · account and project confirmed")

    def test_project_without_a_google_account_and_broken_records(self):
        self.assertEqual((self.check(self.conn(account=None)).detail, self.check(self.conn(account=None)).fix),
                         ("the project has no Google account", "keyfit add google --email <the project's Google account>"))
        self.assertEqual(self.check(self.conn(project="X")).detail, "the expected project must be a Firebase project ID")
        self.assertEqual(self.firebase.calls, [])

    def test_token_of_another_person(self):
        self.fb.fetch = lambda url, token, timeout: api(url, token_of(GLOBEX), timeout)
        check = self.check()
        self.assertEqual((check.detail, check.fix),
                         (f"the firebase CLI's sign-in for {ACME} belongs to {GLOBEX}", "firebase login:add"))
        self.fb.fetch = lambda url, token, timeout: api(url, "garbage", timeout)
        self.assertEqual(self.check().detail, f"Google does not accept the firebase CLI's sign-in for {ACME}")

    def test_project_the_account_cannot_reach(self):
        self.firebase_folder(self.repos["client"], {"default": "globex-app"})
        check = self.check(self.conn(project="globex-app"))
        self.assertEqual((check.detail, check.fix),
                         (f"{ACME} cannot reach Firebase project globex-app (it does not exist, has no Firebase, or the "
                          f"account has no access)", f"keyfit add firebase --project-id <a project {ACME} can reach>"))
        self.firebase_folder(self.repos["client"], {"default": "acme-staging"})
        self.assertEqual(self.check(self.conn(project="acme-staging")).detail,
                         "Firebase project acme-staging is not active (DELETED)")

    def test_network_errors_from_google_are_transient(self):
        def down(url, token, timeout):
            raise OSError("no route")
        self.fb.fetch = down
        check = self.check()
        self.assertEqual((check.detail, check.transient), ("Google did not answer (OSError)", True))
        self.fb.fetch = lambda url, token, timeout: (503, None) if url.startswith(firebase.PROJECTS) else \
            api(url, token, timeout)
        self.assertEqual((self.check().detail, self.check().transient),
                         ("the Firebase Management API answered 503", True))

    def test_fingerprint_follows_sign_ins_not_renewals(self):
        before = self.fb.fingerprint(self.conn(), self.env)
        self.expire(ACME)
        self.renew(ACME)
        self.assertEqual(self.fb.fingerprint(self.conn(), self.env), before)
        self.account(ACME)["user"] = user(ACME, signed_in_at=2)  # `firebase login:add` again
        self.save()
        self.assertNotEqual(self.fb.fingerprint(self.conn(), self.env), before)

    def test_store_and_folders_are_read_like_the_cli(self):
        self.assertEqual(firebase.store_path({"HOME": "/h"}), Path("/h/.config/configstore/firebase-tools.json"))
        self.assertEqual(firebase.store_path({"HOME": "/h", "XDG_CONFIG_HOME": "/x"}),
                         Path("/x/configstore/firebase-tools.json"))
        self.assertEqual(firebase.emails(firebase.read_store(self.env)), [PERSONAL, ACME, GLOBEX])
        self.assertEqual(firebase.default_login(firebase.read_store(self.env)), PERSONAL)
        client = str(self.repos["client"])
        self.assertEqual(firebase.project_root(self.repos["client"] / ".git" / "hooks"), client)
        self.assertIsNone(firebase.project_root(self.repos["mono"]))
        self.assertEqual(firebase.project_root(self.repos["mono"], "app/firebase.json"), str(self.app))
        self.assertEqual(firebase.project_root(self.repos["mono"], f"{self.app}/x/../firebase.json"), str(self.app))
        (self.repos["client"] / ".firebaserc").write_text('{\n  // comments, as the CLI allows\n  "projects": '
                                                          '{"default": "acme-prod",},\n}\n')
        self.assertEqual(firebase.aliases(client), {"default": "acme-prod"})
        before = self.store.read_text()
        self.check()
        self.assertEqual(self.store.read_text(), before)  # read only

    def test_options_and_words_are_read_like_the_cli(self):
        args = ["-P", "p", "deploy", "--account=a@b.io", "--only", "hosting", "-Pq", "--config", "c.json", "-cd.json",
                "--token", "t", "--", "--project", "x"]
        self.assertEqual(firebase.options(args), [("--project", "p"), ("--account", "a@b.io"), ("--project", "q"),
                                                  ("--config", "c.json"), ("--config", "d.json"), ("--token", "t")])
        self.assertEqual(firebase.words(["--project", "p", "login:use", ACME]), ["login:use", ACME])
        self.assertEqual(firebase.words(["use", "staging", "--alias", "x", "--json"]), ["use", "staging"])
        self.assertEqual(firebase.words(["--some-option", "login:use", ACME]), [])  # unclear: never a sign-in command
        self.assertEqual(firebase.words(["login", "--reauth", "--no-localhost"]), ["login"])
        self.assertEqual(firebase.options(["-jP", "x", "deploy", "-ic", "y.json", "-P=z"]),
                         [("--project", "x"), ("--config", "y.json"), ("--project", "=z")])  # as commander groups them
        self.assertEqual(firebase.words(["-jP", "x", "use", "y"]), ["use", "y"])
        self.assertEqual(firebase.words(["-ji", "use", "y"]), ["use", "y"])
        self.assertEqual(firebase.words(["-m", "a message", "use", "y"]), [])  # an option Keyfit does not know

    def test_access_token_of_a_real_process(self):
        self.expire(ACME)
        script = self.tmp / "firebase-script"
        executable(script, f"#!{sys.executable}\nimport json, os, sys, time\n"
                           "open(os.environ['SEEN'], 'w').write(json.dumps([sys.argv[1:], os.getcwd(), sorted(os.environ)]))\n"
                           f"path = {str(self.store)!r}\ndata = json.load(open(path))\n"
                           "account = next(a for a in data['additionalAccounts'] if a['user']['email'] == sys.argv[2])\n"
                           f"account['tokens'] = {{'access_token': {token_of(ACME)!r}, "
                           "'expires_at': (time.time() + 3600) * 1000}\n"
                           "json.dump(data, open(path, 'w'))\nprint(json.dumps({'status': 'success', 'result': []}))\n")
        seen = self.tmp / "seen.json"
        (self.bin / "firebase").unlink()
        os.symlink(script, self.bin / "firebase")
        env = dict(self.env, SEEN=str(seen), FIREBASE_TOKEN="1//theirs")
        token, reason, state = FirebaseAdapter(cli_timeout=30).access_token(ACME, env)
        self.assertEqual((token, state), (token_of(ACME), "ok"), reason)
        argv, cwd, names = json.loads(seen.read_text())
        self.assertEqual(argv, ["--account", ACME, "projects:list", "--json", "--non-interactive"])
        self.assertNotIn(str(self.repos["client"]), cwd)
        self.assertNotIn("FIREBASE_TOKEN", names)
        self.assertIn("NO_UPDATE_NOTIFIER", names)


class ShimTests(FirebaseCase):
    """The firebase shim, run in-process with os.execve caught."""

    def setUp(self):
        super().setUp()
        self.register()
        cache.clear()

    def run_shim(self, *args, tool="firebase", cwd=None, **extra):
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

    def test_each_project_runs_with_its_sign_in_checked_and_no_other_token(self):
        code, argv, env = self.run_shim("deploy", "--only", "hosting", FIREBASE_TOKEN="1//theirs",
                                        GOOGLE_APPLICATION_CREDENTIALS="/keys/globex.json")
        self.assertIsNone(code, self.stderr)
        self.assertEqual(argv, ["firebase", "deploy", "--only", "hosting"])
        self.assertNotIn("FIREBASE_TOKEN", env)
        self.assertNotIn("GOOGLE_APPLICATION_CREDENTIALS", env)
        self.assertEqual(env["KEYFIT_PROJECT"], "client")
        code, argv, env = self.run_shim("deploy", cwd=self.repos["mine"])
        self.assertEqual((code, argv, self.stderr), (None, ["firebase", "deploy"], ""))

    def test_project_without_firebase_and_unregistered_folders_run_as_before(self):
        code, argv, env = self.run_shim("emulators:start", cwd=self.repos["plain"], FIREBASE_TOKEN="1//mine")
        self.assertEqual((code, argv, env["FIREBASE_TOKEN"]), (None, ["firebase", "emulators:start"], "1//mine"))
        code, argv, env = self.run_shim("--project", "globex-app", "--account", GLOBEX, "deploy", cwd=self.outside,
                                        FIREBASE_TOKEN="1//mine")
        self.assertEqual(argv, ["firebase", "--project", "globex-app", "--account", GLOBEX, "deploy"])
        self.assertEqual(env["FIREBASE_TOKEN"], "1//mine")
        self.assertEqual((self.firebase.calls, self.fetched), ([], []))

    def test_dry_run_prints_a_project_without_firebase_instead_of_running_it(self):
        code, argv, _ = self.run_shim("deploy", cwd=self.repos["plain"], KEYFIT_DRY_RUN="1")
        self.assertEqual((code, argv), (0, None), self.stderr)  # never started
        self.assertIn("keyfit: project plain has no firebase account mapped: firebase runs without checks, as before "
                      "Keyfit\n  would run: ", self.stderr)
        self.assertIn("firebase deploy\n  environment changes (names only): ", self.stderr)

    def test_mismatch_blocks_with_the_fix(self):
        self.bind(self.repos["client"], GLOBEX)
        code, argv, _ = self.run_shim("deploy")
        self.assertEqual((code, argv), (1, None))
        self.assertIn("keyfit: firebase not started, client needs attention", self.stderr)
        self.assertIn(f"✘ firebase   acme-prod   the firebase CLI's folder {self.repos['client']} is bound to {GLOBEX}, "
                      f"the project's Google account is {ACME}\n    → firebase login:use {ACME}\n", self.stderr)

    def test_another_account_project_or_a_token_is_refused(self):
        for args, shown in ((["--project", "globex-app", "deploy"], "refusing --project globex-app"),
                            (["deploy", "-P", "staging"], "refusing --project staging"),  # an alias of another project
                            (["deploy", "--project=side-project-1"], "refusing --project side-project-1"),
                            (["-Pglobex-app", "deploy"], "refusing --project globex-app"),
                            (["-jP", "globex-app", "deploy"], "refusing --project globex-app"),
                            (["--account", GLOBEX, "deploy"], f"refusing --account {GLOBEX}"),
                            (["deploy", "--token", "1//x"], "refusing --token")):
            code, argv, _ = self.run_shim(*args)
            self.assertEqual((code, argv), (1, None), args)
            self.assertIn(shown, self.stderr)
        self.assertIn(f"keyfit: this folder belongs to client (Google account {ACME}, Firebase project acme-prod); "
                      f"refusing --token: the firebase CLI would use that token instead of client's Google account and "
                      f"its check.\n  Bypass once: KEYFIT_DISABLE=1 firebase …", self.stderr)
        for args in (["--project", "acme-prod", "deploy"], ["-P", "default", "deploy"], ["--account", "Me@Acme.io", "deploy"]):
            code, argv, _ = self.run_shim(*args)
            self.assertEqual(argv, ["firebase", *args], self.stderr)

    def test_sign_in_and_setup_commands_run_without_checks(self):
        self.expire(ACME)
        self.firebase.state[ACME] = "expired"
        self.bind(self.repos["client"], PERSONAL)
        for args in (["login:list"], ["login:use", ACME], ["use", "acme-prod"], ["use", "default"], ["login:add"],
                     ["login:add", ACME], ["logout", ACME], ["use"], ["login"]):
            code, argv, env = self.run_shim(*args, FIREBASE_TOKEN="1//theirs")
            self.assertEqual(argv, ["firebase", *args], self.stderr)
            self.assertNotIn("FIREBASE_TOKEN", env)
        self.assertEqual(self.firebase.calls, [])

    def test_binding_a_folder_to_another_account_is_refused(self):
        code, argv, _ = self.run_shim("login:use", GLOBEX)
        self.assertEqual((code, argv), (1, None))
        self.assertIn(f"keyfit: refusing `firebase login:use {GLOBEX}` for {self.repos['client']}: client uses Google "
                      f"account {ACME} there.", self.stderr)
        code, argv, _ = self.run_shim("login:use", PERSONAL, "--config", f"{self.repos['mine']}/firebase.json",
                                      cwd=self.repos["plain"])  # from a project without Firebase, into mine's folder
        self.assertEqual(code, 1)
        self.assertIn(f"for {self.repos['mine']}: mine uses Google account {GLOBEX} there.", self.stderr)
        code, argv, _ = self.run_shim("login:use", ACME, cwd=self.repos["mono"])
        self.assertEqual(code, 1)
        self.assertIn("has no firebase.json (nor any folder above it): `firebase login:use` would change the firebase "
                      "CLI's default login", self.stderr)
        code, argv, _ = self.run_shim("login:use", ACME, cwd=self.app)
        self.assertEqual(argv, ["firebase", "login:use", ACME])

    def test_using_another_firebase_project_is_refused(self):
        for target in ("globex-app", "staging"):
            code, argv, _ = self.run_shim("use", target)
            self.assertEqual((code, argv), (1, None), target)
        self.assertIn("keyfit: this folder belongs to client, whose Firebase project is acme-prod; refusing `firebase "
                      "use staging`.", self.stderr)
        code, argv, _ = self.run_shim("use", "acme-prod", "--alias", "prod")
        self.assertEqual(argv, ["firebase", "use", "acme-prod", "--alias", "prod"])

    def test_reauth_that_would_replace_the_default_login_is_refused(self):
        code, argv, _ = self.run_shim("login", "--reauth")
        self.assertEqual((code, argv), (1, None))
        self.assertIn(f"keyfit: refusing `firebase login --reauth`: it would replace the firebase CLI's default login "
                      f"({PERSONAL}), which folders without their own account use, with {ACME}.\n  Renew {ACME}'s "
                      f"sign-in instead: firebase login:add   (choose {ACME})", self.stderr)
        self.data["user"], self.data["additionalAccounts"][0]["user"] = user(ACME), user(PERSONAL)
        self.save()
        code, argv, _ = self.run_shim("login", "--reauth")
        self.assertEqual(argv, ["firebase", "login", "--reauth"])

    def test_outside_every_firebase_folder_the_account_is_named(self):
        code, argv, _ = self.run_shim("projects:create", "acme-new", cwd=self.repos["mono"])
        self.assertEqual(argv, ["firebase", "--account", ACME, "projects:create", "acme-new"], self.stderr)
        self.assertEqual(self.stderr, "")
        code, argv, _ = self.run_shim("deploy", cwd=self.app)  # inside, the folder's binding decides
        self.assertEqual(argv, ["firebase", "deploy"])
        for args in ([], ["--version"], ["--non-interactive"], ["-P", "acme-prod"]):  # no command: the CLI would fail
            code, argv, _ = self.run_shim(*args, cwd=self.repos["mono"])
            self.assertEqual(argv, ["firebase", *args])

    def test_another_firebase_folder_named_with_config_is_checked_too(self):
        code, argv, _ = self.run_shim("deploy", "--config", f"{self.repos['mine']}/firebase.json")
        self.assertEqual((code, argv), (1, None))
        self.assertIn(f"keyfit: firebase not started, client needs attention\n  ✘ firebase   acme-prod   the firebase "
                      f"CLI's folder {self.repos['mine']} is bound to {GLOBEX}, the project's Google account is {ACME}\n"
                      f"    → firebase login:use {ACME} --config {self.repos['mine']}/firebase.json", self.stderr)
        code, argv, _ = self.run_shim("deploy", "-c", "../mono/app/firebase.json")
        self.assertEqual(argv, ["firebase", "deploy", "-c", "../mono/app/firebase.json"], self.stderr)

    def test_session_keeps_firebase_on_its_project(self):
        code, argv, _ = self.run_shim("deploy", cwd=self.repos["mine"], KEYFIT_PROJECT="client")
        self.assertEqual(code, 1)  # mine's folder is bound to mine's account, not the session's
        self.assertIn(f"is bound to {GLOBEX}, the project's Google account is {ACME}", self.stderr)
        code, argv, _ = self.run_shim("deploy", cwd=self.app, KEYFIT_PROJECT="client")
        self.assertEqual(argv, ["firebase", "deploy"])

    def test_agents_start_with_the_firebase_sign_in_checked(self):
        executable(self.bin / "claude", "#!/bin/sh\nexit 0\n")
        code, argv, env = self.run_shim("-p", "hi", tool="claude", FIREBASE_TOKEN="1//theirs")
        self.assertIsNone(code, self.stderr)
        self.assertNotIn("FIREBASE_TOKEN", env)  # npx firebase-tools in the session uses the folder's sign-in
        self.bind(self.repos["client"], GLOBEX)
        cache.clear()
        code, argv, _ = self.run_shim("-p", "hi", tool="claude")
        self.assertEqual((code, argv), (1, None))
        self.assertIn("keyfit: claude not started, client needs attention", self.stderr)
        self.assertIn(f"→ firebase login:use {ACME}", self.stderr)
        code, argv, _ = self.run_shim("-p", "hi", tool="claude", cwd=self.repos["mono"])
        self.assertIsNone(code, self.stderr)
        self.assertIn(f"keyfit: ! firebase   acme-prod   no firebase.json here, so the firebase CLI uses its default login "
                      f"{PERSONAL}", self.stderr)

    def terminal(self, answers, run, tool="firebase", *args):
        class Terminal(io.StringIO):
            def isatty(self):
                return True
        err = Terminal()
        with mock.patch.object(sys, "stdin", Terminal(answers)), mock.patch("subprocess.run", run), \
                contextlib.redirect_stderr(err), mock.patch("os.execve", side_effect=Executed()):
            with self.assertRaises(Executed):
                shim.main([tool, *args])
        return err.getvalue()

    def test_sign_in_now_renews_the_sign_in(self):
        self.expire(ACME)
        self.firebase.state[ACME] = "expired"
        ran = []
        real_run = subprocess.run

        def run(argv, *args, **kwargs):
            if kwargs.get("executable") == str(self.bin / "firebase"):
                ran.append((list(argv), "FIREBASE_TOKEN" in kwargs["env"]))
                self.firebase.state[ACME] = "ok"
                return subprocess.CompletedProcess(argv, 0)
            return real_run(argv, *args, **kwargs)
        err = self.terminal("\n", run, "firebase", "deploy")
        self.assertIn(f"the firebase CLI could not renew its sign-in for {ACME} (expired or revoked): sign in again as "
                      f"{ACME}\n    → firebase login:add\n  Sign in now? [Y/n] ", err)
        self.assertEqual(ran, [(["firebase", "login:add"], False)])
        self.assertIn("keyfit: signed in, starting firebase", err)

    def test_bind_now_binds_the_folder(self):
        del self.data["activeAccounts"][str(self.repos["client"])]
        self.save()
        ran = []
        real_run = subprocess.run

        def run(argv, *args, **kwargs):
            if kwargs.get("executable") == str(self.bin / "firebase"):
                ran.append((list(argv), os.getcwd()))
                self.bind(self.repos["client"], ACME)
                return subprocess.CompletedProcess(argv, 0)
            return real_run(argv, *args, **kwargs)
        executable(self.bin / "claude", "#!/bin/sh\nexit 0\n")
        err = self.terminal("y\n", run, "claude")
        self.assertIn(f"→ firebase login:use {ACME}\n  Bind this folder now? [Y/n] ", err)
        self.assertEqual(ran, [(["firebase", "login:use", ACME], str(self.repos["client"]))])
        self.assertIn("keyfit: bound this folder, starting claude", err)

    def test_dry_run_names_only_the_variables(self):
        code, _, _ = self.run_shim("deploy", KEYFIT_DRY_RUN="1", FIREBASE_TOKEN="1//theirs-secret")
        self.assertEqual(code, 0)
        self.assertIn("environment changes (names only): FIREBASE_TOKEN, KEYFIT_PROJECT, KEYFIT_SUMMARY\n", self.stderr)
        self.assertNotIn("theirs-secret", self.stderr)

    def test_no_token_is_kept_or_shown(self):
        self.expire(ACME)
        self.run_shim("deploy")
        files = [p for p in self.config.rglob("*") if p.is_file()]
        self.assertTrue(files)
        for path in files:
            self.assertNotIn("ya29", path.read_text())
            self.assertNotIn("1//", path.read_text())
        self.assertNotIn("ya29", self.stderr)


class AddTests(FirebaseCase):
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

    def test_firebase_takes_the_folders_project_and_sign_in(self):
        code, out, err = self.keyfit("add", "firebase")
        self.assertEqual(code, 0, err)
        self.assertEqual(out, f"client: google account 'acme' → {ACME}\n"
                              "client: firebase account 'acme-prod' → project acme-prod\n"
                              f"  ✔ firebase   acme-prod   {ACME}, bound at {self.repos['client']} · account and project "
                              "confirmed\n")
        reg = self.registry()
        self.assertEqual((reg["projects"]["client"]["firebase"], reg["accounts"]["firebase"]["acme-prod"],
                          reg["projects"]["client"]["google"]), ("acme-prod", {"project": "acme-prod"}, "acme"))
        self.assertEqual(self.fetched, [TOKENINFO, firebase.PROJECTS + "acme-prod"])
        self.assertNotIn("ya29", out + err + (self.config / "registry.toml").read_text())

    def test_another_project_id_is_recorded_with_the_next_step(self):
        self.keyfit("add", "google", "--email", ACME)
        code, out, err = self.keyfit("add", "firebase", "--project-id", "acme-staging")
        self.assertEqual(code, 0, err)
        self.assertIn("client: firebase account 'acme-staging' → project acme-staging\n  next: the firebase CLI works on "
                      f"acme-prod in {self.repos['client']} (the default in .firebaserc), the project expects "
                      "acme-staging\n  → firebase use acme-staging\n", out)
        self.assertEqual(self.firebase.calls, [])  # nothing online before the local check passes
        code, out, err = self.keyfit("add", "firebase", "--project-id", "Not A Project")
        self.assertEqual((code, err), (1, "Not A Project is not a Firebase project ID (6 to 30 lowercase letters, "
                                          "digits and hyphens)\n"))

    def test_no_firebase_project_here(self):
        (self.repos["client"] / "firebase.json").unlink()
        code, out, err = self.keyfit("add", "firebase")
        self.assertEqual(code, 1)
        self.assertEqual(err, "no Firebase project here (no firebase.json with a .firebaserc default in this folder or "
                              "above): name it with --project-id ID\n")
        self.assertNotIn("firebase", self.registry()["projects"]["client"])

    def test_google_account_lists_the_firebase_clis_sign_ins(self):
        code, out, err = self.keyfit("add", "google")
        self.assertEqual(code, 1)
        self.assertIn(f"(the firebase CLI knows {PERSONAL}, {ACME}, {GLOBEX})", err)
        code, out, err = self.keyfit("add", "google", answers=["2"])
        self.assertIn(f"Google accounts the firebase CLI is signed in to:\n  1. {PERSONAL}\n  2. {ACME}\n", out)
        self.assertEqual(self.registry()["accounts"]["google"]["acme"], {"email": ACME})
        self.keyfit("add", "firebase")
        self.bind(self.repos["client"], GLOBEX)
        code, out, err = self.keyfit("add", "google", "--email", GLOBEX)
        self.assertIn(f"client: google account 'globex' → {GLOBEX}\n", out)
        self.bind(self.repos["client"], PERSONAL)
        code, out, err = self.keyfit("add", "google", "--email", ACME)
        self.assertIn(f"  next: the firebase CLI's folder {self.repos['client']} is bound to {PERSONAL}, the project's "
                      f"Google account is {ACME}\n  → firebase login:use {ACME}\n", out)

    def test_guided_init_offers_the_google_account_then_the_firebase_project(self):
        base = {"registry": None, "project": "client"}
        with mock.patch.object(cli, "_ask", side_effect=["2", "y"]), contextlib.redirect_stdout(io.StringIO()) as out:
            cli._offer_google("client", base)
            cli._offer_firebase("client", base)
        self.assertIn(f"Google: the project's one Google account, work or personal; its Google tools are checked against "
                      f"it. the firebase CLI is signed in to:\n  1. {PERSONAL}\n  2. {ACME}\n", out.getvalue())
        self.assertIn(f"Firebase: {self.repos['client']} works on Firebase project acme-prod\n", out.getvalue())
        reg = self.registry()
        self.assertEqual((reg["projects"]["client"]["google"], reg["projects"]["client"]["firebase"]),
                         ("acme", "acme-prod"))
        self.assertEqual((self.firebase.calls, self.fetched), ([], []))  # init checks everything once at the end
        (self.repos["client"] / "firebase.json").unlink()
        with contextlib.redirect_stdout(io.StringIO()) as out:
            cli._offer_firebase("client", base)
        self.assertEqual(out.getvalue(), "Firebase: no firebase.json with a Firebase project here — later, in the "
                                         "project's Firebase folder: keyfit add firebase\n")
        self.firebase_folder(self.repos["client"], {"default": "acme-prod"})
        self.store.unlink()
        with contextlib.redirect_stdout(io.StringIO()) as out:
            cli._offer_google("client", base)
            cli._offer_firebase("client", base)
        self.assertIn("Google: neither gcloud nor the firebase CLI is signed in to a Google account here", out.getvalue())
        self.assertIn("but the firebase CLI is not signed in — later: firebase login, then: keyfit add firebase",
                      out.getvalue())

    def test_status_and_doctor_show_the_sign_in_and_project(self):
        self.keyfit("add", "firebase")
        code, out, err = self.keyfit("status")
        self.assertIn(f"✔ firebase   acme-prod   {ACME}, bound at {self.repos['client']} · account and project "
                      f"confirmed", out)
        with mock.patch.object(secrets, "keychain_reachable", return_value=""):
            code, out, err = self.keyfit("doctor")
        self.assertIn(f"✔ firebase   {self.bin / 'firebase'}", out)
        self.assertIn(f"✔ firebase   acme-prod   {ACME}, bound at {self.repos['client']} · sign-ins in "
                      f"~/.config/configstore/firebase-tools.json\n", out)


if __name__ == "__main__":
    unittest.main()
