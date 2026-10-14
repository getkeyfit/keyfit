"""Vercel: the adapter, `keyfit add vercel` and the vercel shim, with a fake vercel and a stand-in for its API."""

import contextlib
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import time
import tomllib
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

os.environ["KEYFIT_CONFIG_DIR"] = tempfile.mkdtemp()  # tests never touch the real ~/.config/keyfit

from keyfit import cache, environment, shim  # noqa: E402
from keyfit.adapters import ADAPTERS, vercel  # noqa: E402
from keyfit.adapters.vercel import VercelAdapter  # noqa: E402
from keyfit.cli import main  # noqa: E402
from keyfit.registry import Project, Registry  # noqa: E402

# Fake tokens; never real ones.
TOKEN = "vca_" + "Fake0ctocat" * 3
HUBOT = "vca_" + "FakeHub0t" * 3
RENEWED = "vca_" + "FakeRenewed" * 3
USERS = {TOKEN: "octocat", HUBOT: "hubot", RENEWED: "octocat"}
TEAMS = {"team_acme": "acme", "team_globex": "globex"}
MEMBERS = {"octocat": {"team_acme"}, "hubot": {"team_globex"}}

# Stands in for vercel: `whoami` renews an expired access token the way the real CLI does; every call is logged.
FAKE_VERCEL = """#!/usr/bin/env python3
import json, os, sys, time
args = sys.argv[1:]
with open(os.environ["VERCEL_CALLS"], "a") as fh:
    fh.write(json.dumps({"argv": args, "env": {k: os.environ.get(k) for k in
             ("VERCEL_ORG_ID", "VERCEL_TOKEN", "KEYFIT_PROJECT", "VERCEL_TELEMETRY_DISABLED")}}) + "\\n")
if "whoami" in args:
    folder = (args[args.index("--global-config") + 1] if "--global-config" in args else
              os.path.join(os.environ["HOME"], "Library", "Application Support", "com.vercel.cli"))
    path = os.path.join(folder, "auth.json")
    with open(path) as fh: auth = json.load(fh)
    if auth.get("expiresAt", 1e12) < time.time() and "refreshToken" in auth and os.environ.get("FAKE_RENEWED"):
        auth.update(token=os.environ["FAKE_RENEWED"], expiresAt=int(time.time()) + 3600)
        with open(path, "w") as fh: json.dump(auth, fh)
    print("octocat"); sys.exit(0)
print(json.dumps(args))
"""


def api(path, token, timeout, slug_paths=True):
    """Stands in for api.vercel.com: a token knows its user and the teams that user belongs to."""
    if token not in USERS:
        return 403, {"error": {"code": "forbidden", "invalidToken": True}}
    user = USERS[token]
    if path == "/v2/user":
        return 200, {"user": {"id": f"u_{user}", "username": user, "email": f"{user}@example.com"}}
    if path == "/v2/teams?limit=100":
        return 200, {"teams": [{"id": i, "slug": TEAMS[i]} for i in sorted(MEMBERS[user])]}
    if path.startswith("/v2/teams/"):
        ident = path.rsplit("/", 1)[1]
        team_id = ident if ident in TEAMS else next((i for i, s in TEAMS.items() if s == ident and slug_paths), None)
        if team_id is None:
            return 404, {}
        if team_id not in MEMBERS[user]:
            return 403, {"error": {"code": "team_unauthorized"}}
        return 200, {"id": team_id, "slug": TEAMS[team_id], "name": TEAMS[team_id].title()}
    return 404, {}


def executable(path: Path, text: str) -> None:
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


class Executed(BaseException):
    """os.execve, caught: a BaseException, so the shim's `except Exception` does not take it for a Keyfit bug."""


class VercelCase(unittest.TestCase):
    """A temporary HOME with the CLI's default folder and another login folder, a fake vercel and three projects."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.home = self.tmp / "home"
        self.default = self.home / "Library" / "Application Support" / "com.vercel.cli"
        self.own = self.tmp / "config" / "vercel" / "own"
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        executable(self.bin / "vercel", FAKE_VERCEL)
        self.calls = self.tmp / "vercel-calls"
        self.calls.write_text("")
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
                    "KEYFIT_CONFIG_DIR": str(self.config), "VERCEL_CALLS": str(self.calls)}
        for patcher in (mock.patch("urllib.request.urlopen", side_effect=AssertionError("no network in tests")),
                        mock.patch.object(ADAPTERS["vercel"], "fetch", api),
                        mock.patch.dict(os.environ, self.env, clear=True)):
            patcher.start()
            self.addCleanup(patcher.stop)
        old = os.getcwd()
        self.addCleanup(os.chdir, old)
        os.chdir(self.repos["client"])

    def login(self, folder: Path, token=TOKEN, team=None, expires=None, refresh=False):
        folder.mkdir(parents=True, exist_ok=True)
        auth = {"// Note": "This is your Vercel credentials file. DO NOT SHARE!", "token": token}
        if expires is not None:
            auth["expiresAt"] = int(time.time() + expires)
        if refresh:
            auth["refreshToken"] = "vcr_" + "FakeRefresh" * 3
        (folder / "auth.json").write_text(json.dumps(auth))
        (folder / "config.json").write_text(json.dumps({"currentTeam": team} if team else {}))

    def calls_made(self):
        return [json.loads(line) for line in self.calls.read_text().splitlines()]

    def register(self):
        """client → the default folder (octocat, team acme); mine → another one (hubot, globex); plain → none."""
        reg = Registry(self.config / "registry.toml")
        for name, repo in self.repos.items():
            reg.add_project(name, repo)
        reg.set_account("vercel", "acme", {"config_dir": str(self.default), "user": "octocat", "team": "acme",
                                           "team_id": "team_acme"})
        reg.set_account("vercel", "own", {"config_dir": str(self.own), "user": "hubot", "team": "globex",
                                          "team_id": "team_globex"})
        reg.set_connection("client", "vercel", "acme")
        reg.set_connection("mine", "vercel", "own")
        reg.save()
        return reg

    def conn(self, **changes):
        return dict({"name": "acme", "config_dir": str(self.default), "user": "octocat", "team": "acme",
                     "team_id": "team_acme"}, **changes)


class AdapterTests(VercelCase):
    def setUp(self):
        super().setUp()
        self.vercel = VercelAdapter(fetch=api, timeout=5, cli_timeout=30)

    def check(self, conn, **extra):
        env = dict(self.env, **extra)
        self.vercel.inject(conn, env)
        return self.vercel.verify(conn, env)

    def test_inject_scrubs_variables_that_redirect_the_cli(self):
        env = dict(self.env, VERCEL_TOKEN="x", VERCEL_ORG_ID="team_globex", VERCEL_PROJECT_ID="prj_1",
                   NOW_ORG_ID="team_globex", NOW_PROJECT_ID="prj_1", VERCEL_TEAM_ID="team_globex",
                   VERCEL_TELEMETRY_DISABLED="1")
        self.vercel.inject(self.conn(), env)
        for var in vercel.SCRUB:
            self.assertNotIn(var, env)
        self.assertEqual(env["VERCEL_TELEMETRY_DISABLED"], "1")  # everything else stays as it was
        self.assertNotIn(TOKEN, json.dumps(env))  # the CLI takes no token from the environment; Keyfit sets none

    def test_confirmed_login_and_team(self):
        self.login(self.default)
        check = self.check(self.conn())
        self.assertEqual((check.status, check.detail), ("ok", "login and team confirmed"))
        self.assertEqual(check.label, "octocat → acme")
        check = self.check(self.conn(team=None, team_id=None))
        self.assertEqual((check.status, check.detail, check.label), ("ok", "login confirmed", "octocat"))

    def test_wrong_user(self):
        self.login(self.default, HUBOT)
        check = self.check(self.conn())
        self.assertTrue(check.failed)
        self.assertEqual(check.detail, "signed in as hubot, project expects octocat")
        self.assertEqual(check.fix, "vercel login   (as octocat)")
        self.assertFalse(check.transient)

    def test_wrong_team(self):
        self.login(self.default)
        check = self.check(self.conn(team="globex", team_id="team_globex"))
        self.assertTrue(check.failed)
        self.assertEqual(check.detail, "this login has no access to Vercel team team_globex")
        self.assertIn("keyfit add vercel --name acme --team <a team of octocat>", check.fix)

    def test_renamed_team_asks_to_record_it_again(self):
        self.login(self.default)
        check = self.check(self.conn(team="acme-old"))
        self.assertTrue(check.failed)
        self.assertIn("team team_acme is now acme (team_acme)", check.detail)
        self.assertEqual(check.fix, "keyfit add vercel --name acme --team acme")

    def test_rejected_token(self):
        self.login(self.default, "vca_revoked")
        check = self.check(self.conn())
        self.assertTrue(check.failed)
        self.assertIn("rejected", check.detail)
        self.assertEqual(check.fix, "vercel login")
        self.assertFalse(check.transient)

    def test_network_error_is_transient_and_a_recent_success_carries_it(self):
        self.login(self.default)
        project = Project("client", [str(self.repos["client"])], {"vercel": self.conn()})
        env = environment.build(project, dict(self.env))
        self.assertEqual(environment.verify(project, env)[0].status, "ok")  # cached

        def down(path, token, timeout):
            raise urllib.error.URLError("Network is unreachable")
        with mock.patch.object(ADAPTERS["vercel"], "fetch", down):
            check = environment.verify(project, env)[0]
            self.assertEqual(check.status, "warn")
            self.assertIn("could not re-check now (Vercel did not answer (Network is unreachable))", check.detail)
            cache.clear()
            check = environment.verify(project, env)[0]
        self.assertTrue(check.failed)
        self.assertTrue(check.transient)
        self.assertEqual(check.fix, "check the network and retry")

    def test_not_signed_in(self):
        self.own.mkdir(parents=True)
        check = self.check(self.conn(config_dir=str(self.own), name="own"))
        self.assertTrue(check.failed)
        self.assertEqual(check.detail, "not signed in")
        self.assertEqual(check.fix, f"vercel login --global-config {self.own}")
        check = self.check(self.conn(config_dir=str(self.tmp / "gone"), name="gone"))
        self.assertEqual(check.fix, "keyfit add vercel --name gone --new")

    def test_identity_not_recorded_yet(self):
        self.login(self.own)
        check = self.check({"name": "own", "config_dir": str(self.own)})
        self.assertTrue(check.failed)
        self.assertEqual(check.detail, "no expected identity recorded")
        self.assertIn(f"then: keyfit add vercel --name own --config-dir {self.own}", check.fix)

    def test_repository_linked_to_another_team(self):
        self.login(self.default)
        repo = self.repos["client"]
        (repo / ".vercel").mkdir()
        (repo / ".vercel" / "project.json").write_text(json.dumps({"orgId": "team_globex", "projectId": "prj_1"}))
        (repo / "app").mkdir()
        for cwd in (repo, repo / "app"):  # from a subfolder too
            os.chdir(cwd)
            check = self.check(self.conn())
            self.assertTrue(check.failed, cwd)
            self.assertIn("project.json links this repository to another Vercel team (team_globex), "
                          "project expects acme", check.detail)
            self.assertEqual(check.fix, f"rm -r {repo / '.vercel'}, then link it again: vercel link")
        (repo / ".vercel" / "project.json").write_text(json.dumps({"orgId": "team_acme", "projectId": "prj_1"}))
        self.assertEqual(self.check(self.conn()).status, "ok")
        (repo / ".vercel" / "repo.json").write_text(json.dumps({"orgId": "team_globex", "projects": []}))
        self.assertIn("repo.json links", self.check(self.conn()).detail)

    def test_text_from_the_repository_cannot_drive_the_terminal(self):
        # a cloned repository controls .vercel/project.json; its orgId is printed in the failure
        self.login(self.default)
        repo = self.repos["client"]
        (repo / ".vercel").mkdir()
        (repo / ".vercel" / "project.json").write_text(json.dumps({"orgId": "team_x\x1b]0;title\x07\r\x1b[2K\u202e"}))
        os.chdir(repo)
        lines = "\n".join(self.check(self.conn()).lines())
        self.assertFalse(any(ord(c) < 0x20 and c != "\n" for c in lines), repr(lines))
        self.assertNotIn("\u202e", lines)
        self.assertIn("team_x\\x1b]0;title\\x07\\r\\x1b[2K\\u202e", lines)

    def test_link_outside_the_repository_is_not_read(self):
        self.login(self.default)
        (self.tmp / ".vercel").mkdir()
        (self.tmp / ".vercel" / "project.json").write_text(json.dumps({"orgId": "team_globex"}))
        self.assertEqual(self.check(self.conn()).status, "ok")  # the walk stops at the repository root

    def test_expired_token_is_renewed_by_the_cli(self):
        self.login(self.own, TOKEN, expires=-60, refresh=True)
        conn = self.conn(config_dir=str(self.own), name="own")
        self.assertEqual(self.vercel.local(conn, dict(self.env)).detail, "access token due for renewal")
        check = self.check(conn, FAKE_RENEWED=RENEWED)
        self.assertEqual(check.status, "ok", check.detail)
        self.assertEqual(json.loads((self.own / "auth.json").read_text())["token"], RENEWED)
        call = self.calls_made()[-1]
        self.assertEqual(call["argv"], ["whoami", "--global-config", str(self.own)])
        self.assertEqual(call["env"]["VERCEL_TELEMETRY_DISABLED"], "1")
        self.login(self.own, TOKEN, expires=-60, refresh=True)
        check = self.check(conn)  # the CLI could not renew it
        self.assertTrue(check.failed)
        self.assertIn("expired and Vercel did not renew it", check.detail)

    def test_expired_token_without_refresh_token(self):
        self.login(self.default, expires=-60)
        check = self.check(self.conn())
        self.assertTrue(check.failed)
        self.assertEqual(check.detail, "signed out (the access token expired)")
        self.assertEqual(self.calls_made(), [])

    def test_slug_lookup_falls_back_to_the_logins_teams(self):
        adapter = VercelAdapter(fetch=lambda p, t, timeout: api(p, t, timeout, slug_paths=False))
        self.assertEqual(adapter.team_of(TOKEN, "acme"), ({"id": "team_acme", "slug": "acme"}, "", False))
        self.assertEqual(adapter.team_of(TOKEN, "globex")[0], None)

    def test_fingerprint_follows_the_token(self):
        self.login(self.default)
        first = self.vercel.fingerprint(self.conn(), dict(self.env))
        self.login(self.default, RENEWED)
        self.assertNotEqual(first, self.vercel.fingerprint(self.conn(), dict(self.env)))
        self.assertNotIn(TOKEN, first)

    def test_default_folder_is_the_one_the_cli_picks(self):
        env = dict(self.env)
        self.assertEqual(vercel.cli_default(env), self.default)  # none exists yet: the first candidate
        (self.home / ".now").mkdir(parents=True)
        self.assertEqual(vercel.cli_default(env), self.home / ".now")  # an older login folder the CLI still reads
        self.default.mkdir(parents=True)
        self.assertEqual(vercel.cli_default(env), self.default)
        self.assertEqual(vercel.config_args({"config_dir": str(self.default)}, env), [])
        self.assertEqual(vercel.config_args({"config_dir": str(self.own)}, env), ["--global-config", str(self.own)])
        env["XDG_DATA_HOME"] = str(self.tmp / "xdg")  # the CLI would look elsewhere: the folder must be named
        self.assertEqual(vercel.config_args({"config_dir": str(self.default)}, env),
                         ["--global-config", str(self.default)])

    def test_options_are_read_like_the_cli(self):
        args = ["-dS", "acme", "--team=globex", "--global-config", "/x", "-t", "tok", "deploy", "--cwd", "app",
                "--", "--token", "after"]
        self.assertEqual(vercel.options(args), [("--scope", "acme"), ("--team", "globex"), ("--global-config", "/x"),
                                                ("--token", "tok"), ("--cwd", "app")])
        self.assertEqual(vercel.options(["-Q"]), [("--global-config", None)])
        self.assertEqual(vercel.options(["--scopes", "x", "-v"]), [])


class ShimTests(VercelCase):
    """The vercel shim, run in-process with os.execve caught."""

    def setUp(self):
        super().setUp()
        self.register()

    def run_shim(self, *args, cwd=None, **extra):
        seen = {}

        def execve(path, argv, env):
            seen.update(path=path, argv=argv, env=env)
            raise Executed()
        os.chdir(cwd or self.repos["client"])
        err = io.StringIO()
        with mock.patch.dict(os.environ, extra), mock.patch("os.execve", execve), contextlib.redirect_stderr(err):
            try:
                code = shim.main(["vercel", *args])
            except Executed:
                code = None
        self.stderr = err.getvalue()
        return code, seen.get("argv"), seen.get("env")

    def test_default_login_gets_the_team_but_no_folder(self):
        self.login(self.default)
        code, argv, env = self.run_shim("deploy", "--prod", VERCEL_ORG_ID="team_globex", VERCEL_TOKEN="x")
        self.assertIsNone(code, self.stderr)
        self.assertEqual(argv, ["vercel", "--scope", "acme", "deploy", "--prod"])
        self.assertNotIn("VERCEL_ORG_ID", env)
        self.assertNotIn("VERCEL_TOKEN", env)
        self.assertEqual(env["KEYFIT_PROJECT"], "client")

    def test_another_login_gets_its_folder_and_team(self):
        self.login(self.own, HUBOT, team="team_acme")  # the team selected in the folder does not matter
        code, argv, _ = self.run_shim("link", "--yes", cwd=self.repos["mine"])
        self.assertIsNone(code, self.stderr)
        self.assertEqual(argv, ["vercel", "--global-config", str(self.own), "--scope", "globex", "link", "--yes"])

    def test_the_projects_own_folder_and_team_may_be_named(self):
        self.login(self.own, HUBOT)
        code, argv, _ = self.run_shim("ls", "-S", "team_globex", "--global-config", str(self.own),
                                      cwd=self.repos["mine"])
        self.assertIsNone(code, self.stderr)
        self.assertEqual(argv, ["vercel", "ls", "-S", "team_globex", "--global-config", str(self.own)])

    def test_other_team_folder_and_token_are_refused(self):
        self.login(self.default)
        for args, refused in ((["--scope", "globex", "deploy"], "refusing --scope globex"),
                              (["-dS", "team_globex"], "refusing --scope team_globex"),
                              (["--team=globex"], "refusing --team globex"),
                              (["-Q", str(self.own), "ls"], f"refusing --global-config {self.own}"),
                              (["--global-config=elsewhere"], "refusing --global-config elsewhere"),
                              (["deploy", "-t", "abc"], "refusing --token"),
                              (["--token=abc"], "refusing --token")):
            with self.subTest(args=args):
                code, argv, _ = self.run_shim(*args)
                self.assertEqual(code, 1)
                self.assertIsNone(argv)  # the real vercel never ran
                self.assertIn(f"keyfit: this folder belongs to client (", self.stderr)
                self.assertIn(refused, self.stderr)
                self.assertIn("Bypass once: KEYFIT_DISABLE=1 vercel …", self.stderr)
        code, argv, _ = self.run_shim("env", "run", "--", "node", "--token", "x")  # after `--`: not vercel's
        self.assertIsNone(code, self.stderr)

    def test_example_refusal_message(self):
        self.login(self.default)
        self.run_shim("--scope", "globex", "deploy")
        self.assertEqual(self.stderr, "keyfit: this folder belongs to client (Vercel team acme); refusing --scope "
                                      "globex.\n  Bypass once: KEYFIT_DISABLE=1 vercel …\n")

    def test_sign_in_commands_pass_through_into_the_projects_folder(self):
        # nobody is signed in yet: the checks would fail, sign-in still runs, and lands in the project's folder
        self.own.mkdir(parents=True)
        for words in (["login"], ["logout"], ["switch", "globex"], ["whoami"], ["teams", "ls"]):
            with self.subTest(words=words):
                code, argv, env = self.run_shim(*words, cwd=self.repos["mine"], VERCEL_ORG_ID="team_acme")
                self.assertIsNone(code, self.stderr)
                self.assertEqual(argv, ["vercel", "--global-config", str(self.own), *words])
                self.assertNotIn("VERCEL_ORG_ID", env)
        code, argv, _ = self.run_shim("login", cwd=self.repos["client"])  # the default folder needs no flag
        self.assertEqual(argv, ["vercel", "login"])
        code, argv, _ = self.run_shim("login", "-Q", "/tmp/x", cwd=self.repos["mine"])  # a folder the user named
        self.assertEqual(argv, ["vercel", "login", "-Q", "/tmp/x"])

    def test_project_without_vercel_is_refused_like_aws(self):
        code, argv, _ = self.run_shim("ls", cwd=self.repos["plain"])
        self.assertEqual(code, 1)
        self.assertIn("project plain has no vercel account mapped, so vercel was not started", self.stderr)
        self.assertIn("keyfit add vercel", self.stderr)
        code, argv, _ = self.run_shim("login", cwd=self.repos["plain"])
        self.assertEqual(argv, ["vercel", "login"])

    def test_unregistered_folder_passes_through(self):
        code, argv, env = self.run_shim("--scope", "globex", "ls", cwd=self.outside, VERCEL_ORG_ID="team_globex")
        self.assertEqual(argv, ["vercel", "--scope", "globex", "ls"])
        self.assertEqual(env["VERCEL_ORG_ID"], "team_globex")

    def test_failed_check_blocks(self):
        self.login(self.default, HUBOT)
        code, argv, _ = self.run_shim("deploy")
        self.assertEqual(code, 1)
        self.assertIsNone(argv)
        self.assertIn("keyfit: vercel not started, client needs attention", self.stderr)
        self.assertIn("✘ vercel    octocat → acme   signed in as hubot, project expects octocat", self.stderr)

    def test_linked_to_another_team_blocks(self):
        self.login(self.default)
        app = self.repos["client"] / "apps" / "web"
        (app / ".vercel").mkdir(parents=True)
        (app / ".vercel" / "project.json").write_text(json.dumps({"orgId": "team_globex", "projectId": "prj_9"}))
        code, argv, _ = self.run_shim("--cwd", "apps/web", "deploy")
        self.assertEqual(code, 1)
        self.assertIn("links apps/web to another Vercel team (team_globex)", self.stderr)
        self.assertIn("refusing --cwd apps/web", self.stderr)
        code, argv, _ = self.run_shim("deploy", cwd=app)
        self.assertEqual(code, 1)
        self.assertIn("links this repository to another Vercel team (team_globex)", self.stderr)

    def test_session_keeps_vercel_on_its_project(self):
        self.login(self.own, HUBOT)
        code, argv, _ = self.run_shim("ls", cwd=self.outside, KEYFIT_PROJECT="mine")
        self.assertEqual(argv, ["vercel", "--global-config", str(self.own), "--scope", "globex", "ls"])

    def test_no_token_in_arguments_output_or_files(self):
        self.login(self.default)
        self.login(self.own, HUBOT)
        for repo in ("client", "mine"):
            code, argv, env = self.run_shim("deploy", cwd=self.repos[repo])
            self.assertIsNone(code, self.stderr)
            for token in (TOKEN, HUBOT):
                self.assertNotIn(token, " ".join(argv) + self.stderr)
                self.assertNotIn(token, json.dumps(env))
        for path in (self.config / "registry.toml", self.config / "cache" / "checks.json"):  # Keyfit's own files
            for token in (TOKEN, HUBOT):
                self.assertNotIn(token, path.read_text(), path)


class ThroughTheShimFileTests(VercelCase):
    """`keyfit setup` writes the shims; the vercel one runs as a real process (remote checks served from the cache)."""

    def test_setup_writes_a_vercel_shim_that_injects_the_login(self):
        with mock.patch.object(sys, "platform", "darwin"), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["setup"]), 0)
        shims = self.config / "bin"
        self.assertEqual(sorted(p.name for p in shims.iterdir()),
                         [".keyfit-shims", "aws", "claude", "codex", "convex", "gh", "supabase",
                          "vercel", "wrangler"])
        reg = self.register()
        self.login(self.own, HUBOT)
        conn = reg.get("mine").connections["vercel"]
        cache.put(cache.key("mine", "vercel", conn, ADAPTERS["vercel"].fingerprint(conn, dict(self.env))), "ok",
                  "login and team confirmed")
        env = dict(self.env, PATH=f"{shims}{os.pathsep}{self.env['PATH']}", VERCEL_TOKEN="inherited",
                   https_proxy="http://127.0.0.1:9", HTTPS_PROXY="http://127.0.0.1:9")  # nothing could leave anyway
        out = subprocess.run(["vercel", "deploy"], cwd=self.repos["mine"], env=env, capture_output=True, text=True,
                             timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(json.loads(out.stdout), ["--global-config", str(self.own), "--scope", "globex", "deploy"])
        call = self.calls_made()[-1]
        self.assertEqual(call["env"]["KEYFIT_PROJECT"], "mine")
        self.assertIsNone(call["env"]["VERCEL_TOKEN"])
        self.assertNotIn(HUBOT, out.stdout + out.stderr)


class AddVercelTests(VercelCase):
    def setUp(self):
        super().setUp()
        with contextlib.redirect_stdout(io.StringIO()):
            main(["init", "--yes", "--name", "client"])

    def add(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["add", "vercel", *args])
        return code, out.getvalue(), err.getvalue()

    def registry(self):
        return tomllib.loads((self.config / "registry.toml").read_text())

    def test_default_login_with_the_team_selected_there(self):
        self.login(self.default, team="team_acme")
        code, out, err = self.add()
        self.assertEqual(code, 0, err)
        self.assertEqual(out, "client: vercel account 'acme' → octocat, team acme (selected in that login; another: "
                              "--team SLUG) in ~/Library/Application Support/com.vercel.cli\n")
        reg = self.registry()
        self.assertEqual(reg["projects"]["client"]["vercel"], "acme")
        self.assertEqual(reg["accounts"]["vercel"]["acme"],
                         {"config_dir": "~/Library/Application Support/com.vercel.cli", "user": "octocat",
                          "team": "acme", "team_id": "team_acme"})
        self.assertNotIn(TOKEN, (self.config / "registry.toml").read_text())
        self.assertNotIn(TOKEN, out + err)

    def test_team_given_by_slug(self):
        self.login(self.default)
        code, out, err = self.add("--team", "acme", "--name", "work")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.registry()["accounts"]["vercel"]["work"]["team_id"], "team_acme")
        code, out, err = self.add("--team", "globex")
        self.assertEqual(code, 1)
        self.assertIn("this login has no access to Vercel team globex", err)

    def test_without_a_team_the_login_is_the_identity(self):
        self.login(self.default)
        self.assertEqual(self.add()[0], 0)
        self.assertEqual(self.registry()["accounts"]["vercel"]["octocat"],
                         {"config_dir": "~/Library/Application Support/com.vercel.cli", "user": "octocat"})

    def test_not_signed_in(self):
        code, out, err = self.add()
        self.assertEqual(code, 1)
        self.assertIn("could not record the Vercel login: not signed in (~/Library/Application Support/com.vercel.cli)",
                      err)
        self.assertIn("sign in with: vercel login", err)

    def test_new_login_folder_then_sign_in_through_the_shim(self):
        code, out, err = self.add("--new", "--name", "own")
        self.assertEqual(code, 0, err)
        self.assertEqual(stat.S_IMODE(self.own.stat().st_mode), 0o700)
        self.assertIn("  vercel login\n", out)
        self.assertIn(f"  keyfit add vercel --name own --config-dir {self.own}", out)
        self.assertEqual(self.registry()["accounts"]["vercel"]["own"], {"config_dir": str(self.own)})
        self.assertEqual(self.add("--new")[0], 1)  # --new needs a name

        execs = []

        def execve(path, argv, env):
            execs.append(argv)
            raise Executed()
        with mock.patch("os.execve", execve), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(Executed):
            shim.main(["vercel", "login"])  # from inside the project, nobody signed in yet
        self.assertEqual(execs, [["vercel", "--global-config", str(self.own), "login"]])

        self.login(self.own, HUBOT, team="team_globex")  # what `vercel login` leaves in the folder
        code, out, err = self.add("--name", "own", "--config-dir", str(self.own))
        self.assertEqual(code, 0, err)
        self.assertEqual(self.registry()["accounts"]["vercel"]["own"],
                         {"config_dir": str(self.own), "user": "hubot", "team": "globex", "team_id": "team_globex"})

    def test_new_and_config_dir_exclude_each_other(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            main(["add", "vercel", "--new", "--name", "x", "--config-dir", "/x"])

    def test_status_line_for_vercel(self):
        self.login(self.default, team="team_acme")
        self.add()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(main(["status"]), 0)
        self.assertEqual(out.getvalue(), "client\n  ✔ vercel    octocat → acme   login and team confirmed\n"
                                         "  aws and gh won't start here until mapped: keyfit add aws|github …\n")


if __name__ == "__main__":
    unittest.main()
