"""Convex: the adapter, `keyfit add convex` and the convex shim, with a stub `security`, a fake convex and npx, a
temporary ~/.convex/config.json and a stand-in for api.convex.dev. The real Keychain, ~/.convex and the network
are never touched."""

import base64
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import tomllib
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

os.environ["KEYFIT_CONFIG_DIR"] = tempfile.mkdtemp()  # tests never touch the real ~/.config/keyfit

from keyfit import cache, cli, environment, secrets, shim  # noqa: E402
from keyfit.adapters import ADAPTERS, convex  # noqa: E402
from keyfit.adapters.convex import ConvexAdapter  # noqa: E402
from keyfit.cli import main  # noqa: E402
from keyfit.registry import Project, Registry  # noqa: E402
from test_secrets import FAKE_SECURITY  # noqa: E402
from test_vercel import Executed, executable  # noqa: E402


def token(seed: str) -> str:
    """A fake token shaped like the CLI's login: base64 of {"v2": "<32 hex>"}."""
    return base64.b64encode(json.dumps({"v2": (seed * 32)[:32]}).encode()).decode()


# Fake tokens and deployments; never real ones.
ACME, GLOBEX, BOTH, REVOKED, FRESH = token("a1"), token("b2"), token("c3"), token("d4"), token("e5")
MEMBERS = {ACME: ["acme"], GLOBEX: ["globex"], BOTH: ["acme", "globex"], FRESH: ["acme"]}
TEAM_NAMES = {"acme": "Acme", "globex": "Globex", "initech": "Initech"}
DEPLOYMENTS = {"happy-animal-123": ("acme", "web"), "brave-otter-456": ("globex", "app"),
               "quiet-lynx-789": ("initech", "intranet")}
SECRET = "eyJ2MiI6ImRlcGxveWtleXNlY3JldDAwMDAwMDAwMDAwMCJ9"  # the secret half of a fake deploy key

FAKE_CONVEX = """#!/bin/sh
echo "convex ran" >&2
exit 0
"""
# Fake npx: `npx convex login --force` writes the login it is given in NEW_CONVEX_LOGIN, as the real one does.
FAKE_NPX = """#!/usr/bin/env python3
import json, os, sys
home = os.environ["HOME"]
with open(os.path.join(home, "npx-calls.log"), "a") as fh:
    fh.write(json.dumps({"argv": sys.argv[1:], "override": os.environ.get("CONVEX_OVERRIDE_ACCESS_TOKEN")}) + "\\n")
if sys.argv[1:] == ["convex", "login", "--force"]:
    os.makedirs(os.path.join(home, ".convex"), exist_ok=True)
    with open(os.path.join(home, ".convex", "config.json"), "w") as fh:
        json.dump({"accessToken": os.environ["NEW_CONVEX_LOGIN"]}, fh)
"""


def api(path, tok, timeout):
    """Stands in for api.convex.dev: a token sees its teams and their deployments."""
    if tok not in MEMBERS:
        return 401, None
    if path == "/api/teams":
        return 200, [{"id": i, "name": TEAM_NAMES[slug], "slug": slug, "suspended": False}
                     for i, slug in enumerate(MEMBERS[tok], 1)]
    if path.startswith("/api/deployment/") and path.endswith("/team_and_project"):
        name = path.split("/")[3]
        team, project = DEPLOYMENTS.get(name, (None, None))
        if team not in MEMBERS[tok]:
            return 404, None  # DeploymentNotFound, also for another team's
        return 200, {"team": team, "project": project, "teamId": 1, "projectId": 2}
    return 404, None


class Terminal(io.StringIO):
    def isatty(self):
        return True


class ConvexCase(unittest.TestCase):
    """A temporary HOME, a stub security with Keyfit's item for Acme, a fake convex and npx, and four projects:
    client (Acme, token in the Keychain), mine (Globex, token in a variable), solo (Acme, the convex CLI's own
    login) and plain (no Convex account)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        executable(self.bin / "convex", FAKE_CONVEX)
        executable(self.bin / "npx", FAKE_NPX)
        self.stub = self.tmp / "stub"
        self.stub.mkdir()
        executable(self.stub / "security", FAKE_SECURITY)
        self.keychain({"keyfit/convex-acme": ACME})
        self.config = self.tmp / "config"
        self.repos = {}
        for name in ("client", "mine", "solo", "plain"):
            repo = self.tmp / name
            repo.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            self.repos[name] = repo
        self.outside = self.tmp / "outside"
        self.outside.mkdir()
        self.env = {"HOME": str(self.home), "PATH": f"{self.bin}{os.pathsep}/usr/bin:/bin",
                    "KEYFIT_CONFIG_DIR": str(self.config), "GLOBEX_CONVEX_TOKEN": GLOBEX}
        self.fetched = []

        def fetch(path, tok, timeout):
            self.fetched.append(path)
            return api(path, tok, timeout)
        for patcher in (mock.patch("urllib.request.urlopen", side_effect=AssertionError("no network in tests")),
                        mock.patch.object(ADAPTERS["convex"], "fetch", fetch),
                        mock.patch.object(secrets, "SECURITY", str(self.stub / "security")),
                        mock.patch.dict(os.environ, self.env, clear=True)):
            patcher.start()
            self.addCleanup(patcher.stop)
        old = os.getcwd()
        self.addCleanup(os.chdir, old)
        os.chdir(self.repos["client"])

    def keychain(self, items: dict):
        path = self.stub / "keychain.json"
        current = json.loads(path.read_text()) if path.exists() else {}
        path.write_text(json.dumps({**current, **items}))

    def security_calls(self) -> list:
        log = self.stub / "argv.log"
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    def cli_login(self, tok):
        """The convex CLI's own login, as `npx convex login` writes it (None: signed out)."""
        path = self.home / ".convex" / "config.json"
        if tok is None:
            path.unlink(missing_ok=True)
            return
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps({"accessToken": tok}, indent=2))

    def env_local(self, folder: Path, text: str, name=".env.local"):
        (folder / name).write_text(text)

    def register(self):
        reg = Registry(self.config / "registry.toml")
        for name, repo in self.repos.items():
            reg.add_project(name, repo)
        reg.set_account("convex", "acme", {"token": "keychain:keyfit/convex-acme", "team": "acme", "team_name": "Acme"})
        reg.set_account("convex", "globex", {"token": "env:GLOBEX_CONVEX_TOKEN", "team": "globex",
                                             "team_name": "Globex"})
        reg.set_account("convex", "mine-cli", {"token": "cli", "team": "acme", "team_name": "Acme"})
        reg.set_connection("client", "convex", "acme")
        reg.set_connection("mine", "convex", "globex")
        reg.set_connection("solo", "convex", "mine-cli")
        reg.save()
        return reg

    def conn(self, **changes):
        return dict({"name": "acme", "token": "keychain:keyfit/convex-acme", "team": "acme", "team_name": "Acme"},
                    **changes)


class AdapterTests(ConvexCase):
    def setUp(self):
        super().setUp()
        self.convex = ConvexAdapter(fetch=api, timeout=5)

    def check(self, conn, **extra):
        env = dict(self.env, **extra)
        self.convex.inject(conn, env)
        return self.convex.verify(conn, env)

    def test_inject_puts_the_accounts_token_and_keeps_it_on_api_convex_dev(self):
        env = dict(self.env, CONVEX_OVERRIDE_ACCESS_TOKEN=GLOBEX, CONVEX_PROVISION_HOST="https://evil.example")
        self.convex.inject(self.conn(), env)
        self.assertEqual(env["CONVEX_OVERRIDE_ACCESS_TOKEN"], ACME)
        self.assertNotIn("CONVEX_PROVISION_HOST", env)  # would send the token to another host
        self.assertEqual(env["KEYFIT_CONVEX"], "acme")

    def test_the_clis_own_login_is_read_from_its_file(self):
        self.cli_login(GLOBEX)
        env = dict(self.env, CONVEX_OVERRIDE_ACCESS_TOKEN=ACME)  # an inherited override would win over the login
        conn = self.conn(name="mine-cli", token="cli", team="globex", team_name="Globex")
        self.convex.inject(conn, env)
        self.assertEqual(env["CONVEX_OVERRIDE_ACCESS_TOKEN"], GLOBEX)
        self.assertEqual(self.convex.verify(conn, env).detail, "login and team confirmed")
        self.cli_login(None)
        check = self.check(conn)
        self.assertEqual((check.status, check.detail, check.fix),
                         ("fail", "the convex CLI is not signed in", "npx convex login --force"))
        (self.home / ".convex" / "config.json").write_text("{not json")
        self.assertIn(".convex/config.json is unreadable", self.check(conn).detail)

    def test_an_override_variable_itself_can_be_the_source_and_survives_its_own_session(self):
        conn = self.conn(name="globex", token="env:CONVEX_OVERRIDE_ACCESS_TOKEN", team="globex")
        project = Project("mine", [str(self.repos["mine"])], {"convex": conn})
        session = environment.build(project, dict(self.env, CONVEX_OVERRIDE_ACCESS_TOKEN=GLOBEX))  # e.g. claude
        nested = environment.build(project, session)  # convex, or keyfit status, inside that session
        self.assertEqual(nested["CONVEX_OVERRIDE_ACCESS_TOKEN"], GLOBEX)
        other = Project("client", [str(self.repos["client"])],
                        {"convex": self.conn(token="env:CONVEX_OVERRIDE_ACCESS_TOKEN")})
        self.assertNotIn("CONVEX_OVERRIDE_ACCESS_TOKEN", environment.build(other, session))  # never another account's

    def test_another_projects_token_does_not_follow_into_a_project_without_convex(self):
        client = Project("client", [str(self.repos["client"])], {"convex": self.conn()})
        plain = Project("plain", [str(self.repos["plain"])], {})
        session = environment.build(client, dict(self.env))
        self.assertEqual(session["CONVEX_OVERRIDE_ACCESS_TOKEN"], ACME)
        nested = environment.build(plain, session)
        for var in ("CONVEX_OVERRIDE_ACCESS_TOKEN", "KEYFIT_CONVEX"):
            self.assertNotIn(var, nested)
        own = environment.build(plain, dict(self.env, CONVEX_OVERRIDE_ACCESS_TOKEN=GLOBEX, CONVEX_DEPLOYMENT="dev:x"))
        self.assertEqual((own["CONVEX_OVERRIDE_ACCESS_TOKEN"], own["CONVEX_DEPLOYMENT"]), (GLOBEX, "dev:x"))  # yours

    def test_inherited_deployment_variables_of_another_project_are_removed(self):
        self.env_local(self.repos["client"], "CONVEX_DEPLOYMENT=dev:happy-animal-123 # team: acme, project: web\n")
        env = dict(self.env, CONVEX_DEPLOYMENT="dev:brave-otter-456", CONVEX_DEPLOY_KEY=f"prod:brave-otter-456|{SECRET}",
                   CONVEX_DEPLOYMENT_TOKEN=f"prod:brave-otter-456|{SECRET}")
        self.convex.inject(self.conn(), env)
        for var in ("CONVEX_DEPLOYMENT", "CONVEX_DEPLOY_KEY", "CONVEX_DEPLOYMENT_TOKEN"):
            self.assertNotIn(var, env)  # the CLI now reads the folder's own .env.local
        same = dict(self.env, CONVEX_DEPLOYMENT="dev:happy-animal-123")
        self.convex.inject(self.conn(), same)
        self.assertEqual(same["CONVEX_DEPLOYMENT"], "dev:happy-animal-123")  # what the folder says anyway: kept
        os.chdir(self.repos["mine"])  # no .env.local: an inherited deployment cannot be this folder's
        bare = dict(self.env, CONVEX_DEPLOYMENT="dev:happy-animal-123")
        self.convex.inject(self.conn(), bare)
        self.assertNotIn("CONVEX_DEPLOYMENT", bare)

    def test_team_confirmed(self):
        check = self.check(self.conn())
        self.assertEqual((check.status, check.label, check.detail), ("ok", "Acme", "login and team confirmed"))

    def test_rejected_token(self):
        self.keychain({"keyfit/convex-acme": REVOKED})
        check = self.check(self.conn())
        self.assertTrue(check.failed)
        self.assertEqual(check.detail, "Convex rejected the token (signed out or revoked?)")
        self.assertEqual(check.fix, "keyfit add convex --name acme --copy   (after npx convex login --force as that "
                                    "account; or paste a token: keyfit add convex --name acme)")
        self.assertFalse(check.transient)

    def test_token_of_another_team(self):
        check = self.check(self.conn(token="env:GLOBEX_CONVEX_TOKEN"))
        self.assertTrue(check.failed)
        self.assertEqual(check.detail, "this login has no access to team Acme (acme; it sees globex)")
        self.assertEqual(check.fix, "keyfit add convex --name acme   (a token of a member of Acme)")
        self.cli_login(GLOBEX)  # the CLI's own login, as someone outside the team: signing in again fixes it
        self.assertEqual(self.check(self.conn(token="cli")).fix, "npx convex login --force")

    def test_missing_malformed_doubled_or_deploy_key_fails_before_the_network(self):
        self.keychain({"keyfit/convex-acme": ""})
        self.assertEqual(self.check(self.conn()).detail, "token not found (Keychain item keyfit/convex-acme)")
        self.keychain({"keyfit/convex-acme": ACME + ACME})
        self.assertEqual(self.check(self.conn()).detail, "Keychain item keyfit/convex-acme looks pasted twice")
        self.keychain({"keyfit/convex-acme": f"prod:happy-animal-123|{SECRET}"})
        self.assertEqual(self.check(self.conn()).detail,
                         "Keychain item keyfit/convex-acme holds a deploy key (…|…), not an access token")
        self.keychain({"keyfit/convex-acme": "short"})
        self.assertEqual(self.check(self.conn()).detail,
                         "Keychain item keyfit/convex-acme does not hold a Convex access token")
        check = self.check(self.conn(token="env:UNSET_VAR"))
        self.assertEqual((check.detail, check.fix),
                         ("token not found ($UNSET_VAR)", "set $UNSET_VAR to a Convex access token"))
        self.assertEqual(self.check(self.conn(team=None)).detail, "no expected team recorded")

    def test_deployment_of_the_team(self):
        self.env_local(self.repos["client"], "# Deployment used by `npx convex dev`\n"
                                             "CONVEX_DEPLOYMENT=dev:happy-animal-123 # team: acme, project: web\n")
        check = self.check(self.conn())
        self.assertEqual((check.status, check.detail),
                         ("ok", "login, team and deployment happy-animal-123 (web) confirmed"))

    def test_deployment_of_another_team(self):
        self.keychain({"keyfit/convex-acme": BOTH})  # a member of both: the token reaches it
        self.env_local(self.repos["client"], "CONVEX_DEPLOYMENT=dev:brave-otter-456\n")
        check = self.check(self.conn())
        self.assertTrue(check.failed)
        self.assertEqual(check.detail, "CONVEX_DEPLOYMENT in .env.local points this folder at deployment "
                                       "brave-otter-456 of another team (Globex), project expects Acme")
        self.assertEqual(check.fix, "npx convex dev --configure existing --team acme")

    def test_deployment_the_login_cannot_reach(self):
        self.env_local(self.repos["client"], "CONVEX_DEPLOYMENT=prod:quiet-lynx-789\n", name=".env")
        check = self.check(self.conn())
        self.assertTrue(check.failed)
        self.assertEqual(check.detail, "CONVEX_DEPLOYMENT in .env points this folder at deployment quiet-lynx-789: "
                                       "this login cannot reach it (another team's, or deleted?)")

    def test_env_local_comes_before_env_and_a_deploy_key_before_the_deployment(self):
        self.keychain({"keyfit/convex-acme": BOTH})
        self.env_local(self.repos["client"], "CONVEX_DEPLOYMENT=dev:brave-otter-456\n", name=".env")
        self.env_local(self.repos["client"], "CONVEX_DEPLOYMENT=dev:happy-animal-123\n")
        self.assertEqual(self.check(self.conn()).status, "ok")
        self.env_local(self.repos["client"], f"CONVEX_DEPLOYMENT=dev:happy-animal-123\n"
                                             f"CONVEX_DEPLOY_KEY='prod:brave-otter-456|{SECRET}'\n")
        check = self.check(self.conn())
        self.assertTrue(check.failed)
        self.assertIn("CONVEX_DEPLOY_KEY in .env.local points this folder at deployment brave-otter-456 of another "
                      "team", check.detail)
        self.assertNotIn(SECRET, check.detail + check.fix)

    def test_preview_keys_name_their_team(self):
        self.env_local(self.repos["client"], f"CONVEX_DEPLOY_KEY=preview:globex:app|{SECRET}\n")
        check = self.check(self.conn())
        self.assertTrue(check.failed)
        self.assertEqual(check.detail, "CONVEX_DEPLOY_KEY in .env.local is a preview deploy key of another team "
                                       "(globex), project expects Acme")
        self.assertNotIn("/api/teams", self.fetched)  # decided without the network
        self.env_local(self.repos["client"], f"CONVEX_DEPLOY_KEY=preview:acme:web|{SECRET}\n")
        self.assertEqual(self.check(self.conn()).status, "ok")

    def test_local_self_hosted_and_project_keys(self):
        self.env_local(self.repos["client"], "CONVEX_DEPLOYMENT=anonymous:anonymous-web\n")
        check = self.check(self.conn())
        self.assertEqual((check.status, check.detail), ("ok", "login and team confirmed · CONVEX_DEPLOYMENT in "
                                                              ".env.local names local deployment anonymous-web"))
        self.env_local(self.repos["client"], "CONVEX_SELF_HOSTED_URL=http://127.0.0.1:3210\n"
                                             "CONVEX_SELF_HOSTED_ADMIN_KEY=secret\n")
        self.assertIn("names a self-hosted deployment (no Convex team to check)", self.check(self.conn()).detail)

    def test_a_project_deploy_key_stays_the_clis_login(self):
        # with a project key, `convex dev` refuses any other login: Keyfit leaves it alone and says so
        self.env_local(self.repos["client"], f"CONVEX_DEPLOY_KEY=project:acme:web|{SECRET}\n")
        env = dict(self.env)
        self.convex.inject(self.conn(), env)
        self.assertNotIn("CONVEX_OVERRIDE_ACCESS_TOKEN", env)
        check = self.convex.verify(self.conn(), env)
        self.assertEqual((check.status, check.detail),
                         ("warn", "CONVEX_DEPLOY_KEY in .env.local holds a project deploy key, which the convex CLI uses "
                                  "instead of any login: the account is not used here, and the key is not checked"))
        self.assertEqual(self.fetched, [])

    def test_network_error_is_transient_and_a_recent_success_carries_it(self):
        project = Project("client", [str(self.repos["client"])], {"convex": self.conn()})
        env = environment.build(project, dict(self.env))
        self.assertEqual(environment.verify(project, env)[0].status, "ok")  # cached

        def down(path, tok, timeout):
            raise urllib.error.URLError("Network is unreachable")
        with mock.patch.object(ADAPTERS["convex"], "fetch", down):
            check = environment.verify(project, env)[0]
        self.assertEqual(check.status, "warn")
        self.assertIn("could not re-check now (Convex did not answer (Network is unreachable))", check.detail)

    def test_busy_api_is_transient(self):
        self.convex.fetch = lambda path, tok, timeout: (503, None)
        check = self.check(self.conn())
        self.assertTrue(check.failed and check.transient)
        self.assertEqual(check.fix, "check the network and retry")

    def test_fingerprint_follows_the_token_and_the_deployment(self):
        env = dict(self.env)
        self.convex.inject(self.conn(), env)
        first = self.convex.fingerprint(self.conn(), env)
        self.env_local(self.repos["client"], "CONVEX_DEPLOYMENT=dev:happy-animal-123\n")
        pointed = self.convex.fingerprint(self.conn(), env)
        self.assertNotEqual(first, pointed)
        self.keychain({"keyfit/convex-acme": BOTH})
        self.convex.inject(self.conn(), env)
        self.assertNotEqual(pointed, self.convex.fingerprint(self.conn(), env))
        self.assertNotIn(BOTH, pointed + first)

    def test_env_files_are_read_like_dotenv(self):
        self.env_local(self.repos["client"], "# comment\nexport CONVEX_DEPLOYMENT = dev:happy-animal-123  # team: acme\n"
                                             "CONVEX_DEPLOY_KEY=\"prod:a#b|c\"\nCONVEX_URL=https://x#y\nEMPTY=\n")
        values = convex.read_env_file(self.repos["client"] / ".env.local")
        self.assertEqual(values["CONVEX_DEPLOYMENT"], "dev:happy-animal-123")
        self.assertEqual(values["CONVEX_DEPLOY_KEY"], "prod:a#b|c")
        self.assertEqual((values["CONVEX_URL"], values["EMPTY"]), ("https://x", ""))

    def test_words_and_options_are_read_like_the_cli(self):
        self.assertEqual(convex.words(["login", "--force"]), ["login"])
        self.assertEqual(convex.words(["--verbose", "login", "status"]), ["login", "status"])
        self.assertEqual(convex.words(["deploy", "--cmd", "pnpm build", "--prod"]), ["deploy"])
        self.assertEqual(convex.options(["deploy", "--deployment-name=a-b-1", "--env-file", "e", "--admin-key"]),
                         [("--deployment-name", "a-b-1"), ("--env-file", "e"), ("--admin-key", None)])
        self.assertEqual(convex.options(["run", "f", "--", "--team", "x"]), [])


class ShimTests(ConvexCase):
    """The convex shim, run in-process with os.execve caught."""

    def setUp(self):
        super().setUp()
        self.register()

    def run_shim(self, *args, cwd=None, tool="convex", terminal=None, **extra):
        seen = {}

        def execve(path, argv, env):
            seen.update(path=path, argv=argv, env=env)
            raise Executed()
        os.chdir(cwd or self.repos["client"])
        err = Terminal() if terminal is not None else io.StringIO()
        stdin = Terminal(terminal) if terminal is not None else io.StringIO()
        with mock.patch.dict(os.environ, extra), mock.patch("os.execve", execve), contextlib.redirect_stderr(err), \
                mock.patch.object(sys, "stdin", stdin):
            try:
                code = shim.main([tool, *args])
            except Executed:
                code = None
        self.stderr = err.getvalue()
        return code, seen.get("argv"), seen.get("env")

    def test_each_project_gets_its_own_token(self):
        code, argv, env = self.run_shim("deploy", CONVEX_OVERRIDE_ACCESS_TOKEN=GLOBEX)
        self.assertIsNone(code, self.stderr)
        self.assertEqual(argv, ["convex", "deploy"])
        self.assertEqual(env["CONVEX_OVERRIDE_ACCESS_TOKEN"], ACME)
        code, argv, env = self.run_shim("deploy", cwd=self.repos["mine"])
        self.assertEqual(env["CONVEX_OVERRIDE_ACCESS_TOKEN"], GLOBEX)

    def test_project_without_convex_runs_as_before(self):
        # Convex is often used with an anonymous local deployment: no account, no refusal
        code, argv, env = self.run_shim("dev", cwd=self.repos["plain"], CONVEX_OVERRIDE_ACCESS_TOKEN=GLOBEX,
                                        CONVEX_DEPLOYMENT="dev:brave-otter-456")
        self.assertIsNone(code, self.stderr)
        self.assertEqual(argv, ["convex", "dev"])
        self.assertEqual((env["CONVEX_OVERRIDE_ACCESS_TOKEN"], env["CONVEX_DEPLOYMENT"]), (GLOBEX, "dev:brave-otter-456"))
        self.assertEqual((self.stderr, self.fetched), ("", []))

    def test_unregistered_folder_passes_through(self):
        code, argv, env = self.run_shim("deploy", "--admin-key", "x", "--url", "u", cwd=self.outside,
                                        CONVEX_DEPLOYMENT="dev:brave-otter-456")
        self.assertEqual(argv, ["convex", "deploy", "--admin-key", "x", "--url", "u"])
        self.assertEqual(env["CONVEX_DEPLOYMENT"], "dev:brave-otter-456")
        self.assertEqual(self.fetched, [])

    def test_failed_check_blocks_with_the_fix(self):
        self.keychain({"keyfit/convex-acme": REVOKED})
        code, argv, _ = self.run_shim("deploy")
        self.assertEqual((code, argv), (1, None))
        self.assertIn("keyfit: convex not started, client needs attention", self.stderr)
        self.assertIn("✘ convex    Acme   Convex rejected the token (signed out or revoked?)", self.stderr)
        self.assertIn("→ keyfit add convex --name acme --copy", self.stderr)

    def test_folder_pointing_at_another_teams_deployment_blocks(self):
        self.keychain({"keyfit/convex-acme": BOTH})
        self.env_local(self.repos["client"], "CONVEX_DEPLOYMENT=dev:brave-otter-456\n")
        code, argv, _ = self.run_shim("deploy")
        self.assertEqual(code, 1)
        self.assertIn("points this folder at deployment brave-otter-456 of another team (Globex)", self.stderr)
        self.assertIn("→ npx convex dev --configure existing --team acme", self.stderr)

    def test_other_teams_and_deployments_are_refused(self):
        self.keychain({"keyfit/convex-acme": BOTH})
        self.env_local(self.repos["client"], "CONVEX_DEPLOYMENT=prod:brave-otter-456\n", name="other.env")
        for args, shown in ((["deploy", "--admin-key", "k", "--url", "u"], "--admin-key: it would skip client's"),
                            (["dev", "--configure", "existing", "--team", "globex"], "--team globex"),
                            (["deploy", "--deployment", "globex:app:prod"], "--deployment globex:app:prod: another"),
                            (["run", "f", "--deployment-name", "brave-otter-456"], "--deployment-name brave-otter-456: "
                                                                                  "it belongs to another team (globex)"),
                            (["deploy", "--deployment=brave-otter-456"], "--deployment brave-otter-456: it belongs"),
                            (["deploy", "--env-file", "other.env"], "--env-file other.env (deployment brave-otter-456)"),
                            (["logs", "--deployment-name", "quiet-lynx-789"], "--deployment-name quiet-lynx-789: this "
                                                                               "login cannot reach it")):
            code, argv, _ = self.run_shim(*args)
            self.assertEqual((code, argv), (1, None), args)
            self.assertIn(f"keyfit: this folder belongs to client (Convex team Acme); refusing {shown}", self.stderr)
        for args in (["deploy", "--deployment-name", "happy-animal-123"], ["deploy", "--team", "acme"],
                     ["deploy", "--prod"], ["deploy", "--deployment", "web:dev"], ["run", "--", "--team", "x"]):
            code, argv, _ = self.run_shim(*args)
            self.assertEqual(argv, ["convex", *args], self.stderr)

    def test_confirmed_deployments_are_cached(self):
        self.run_shim("deploy", "--deployment-name", "happy-animal-123")
        self.fetched.clear()
        code, argv, _ = self.run_shim("deploy", "--deployment-name", "happy-animal-123")
        self.assertIsNone(code, self.stderr)
        self.assertEqual(self.fetched, [])

    def test_login_in_a_project_whose_token_keyfit_keeps_is_refused_with_the_fix(self):
        for args in (["login"], ["logout"], ["login", "--force"], ["--verbose", "login"]):
            code, argv, _ = self.run_shim(*args)
            self.assertEqual((code, argv), (1, None), args)
        self.assertIn("keyfit: this folder belongs to client, whose Convex token Keyfit keeps (Keychain item "
                      "keyfit/convex-acme); `convex login` would change only the convex CLI's own login", self.stderr)
        self.assertIn("Another token for client: keyfit add convex --name acme", self.stderr)
        self.assertEqual(self.fetched, [])  # no check ran
        code, argv, env = self.run_shim("login", "status")  # only reads the login: runs like any command
        self.assertEqual(argv, ["convex", "login", "status"])

    def test_login_where_the_account_is_the_clis_own_login(self):
        self.cli_login(ACME)
        code, argv, env = self.run_shim("login", "--force", cwd=self.repos["solo"])
        self.assertEqual(argv, ["convex", "login", "--force"])
        self.assertNotIn("CONVEX_OVERRIDE_ACCESS_TOKEN", env)  # it signs the CLI itself in
        self.assertEqual(self.fetched, [])

    def test_expired_cli_login_offers_sign_in_now_then_starts(self):
        self.cli_login(REVOKED)
        code, argv, env = self.run_shim("deploy", cwd=self.repos["solo"], terminal="\n", NEW_CONVEX_LOGIN=FRESH)
        self.assertIsNone(code, self.stderr)
        self.assertIn("✘ convex    Acme   Convex rejected the token (signed out or revoked?)\n"
                      "    → npx convex login --force\n  Sign in now? [Y/n] ", self.stderr)
        self.assertIn("keyfit: signed in, starting convex", self.stderr)
        self.assertEqual(env["CONVEX_OVERRIDE_ACCESS_TOKEN"], FRESH)
        calls = [json.loads(line) for line in (self.home / "npx-calls.log").read_text().splitlines()]
        self.assertEqual(calls, [{"argv": ["convex", "login", "--force"], "override": None}])

    def test_expired_cli_login_blocks_claude_without_a_terminal(self):
        self.cli_login(None)
        executable(self.bin / "claude", "#!/bin/sh\nexit 0\n")
        code, argv, _ = self.run_shim("-p", "hi", cwd=self.repos["solo"], tool="claude")
        self.assertEqual((code, argv), (1, None))
        self.assertIn("✘ convex    Acme   the convex CLI is not signed in\n    → npx convex login --force\n",
                      self.stderr)
        self.assertNotIn("Sign in now?", self.stderr)

    def test_session_keeps_convex_on_its_project(self):
        code, argv, env = self.run_shim("deploy", cwd=self.outside, KEYFIT_PROJECT="mine")
        self.assertEqual(env["CONVEX_OVERRIDE_ACCESS_TOKEN"], GLOBEX)

    def test_claude_session_gets_the_token_for_npx_convex(self):
        executable(self.bin / "claude", "#!/bin/sh\nexit 0\n")
        self.env_local(self.repos["client"], "CONVEX_DEPLOYMENT=dev:happy-animal-123\n")
        code, argv, env = self.run_shim("-p", "hi", tool="claude", CONVEX_DEPLOYMENT="dev:brave-otter-456")
        self.assertIsNone(code, self.stderr)
        self.assertEqual(env["CONVEX_OVERRIDE_ACCESS_TOKEN"], ACME)
        self.assertNotIn("CONVEX_DEPLOYMENT", env)  # npx convex reads the folder's own .env.local

    def test_no_token_in_arguments_output_or_files(self):
        for repo in ("client", "mine"):
            code, argv, env = self.run_shim("deploy", cwd=self.repos[repo])
            self.assertIsNone(code, self.stderr)
            for tok in (ACME, GLOBEX):
                self.assertNotIn(tok, " ".join(argv) + self.stderr)
        for path in (self.config / "registry.toml", self.config / "cache" / "checks.json"):
            for tok in (ACME, GLOBEX):
                self.assertNotIn(tok, path.read_text(), path)
        for argv in self.security_calls():
            self.assertNotIn(ACME, " ".join(argv))


class AddConvexTests(ConvexCase):
    def setUp(self):
        super().setUp()
        with contextlib.redirect_stdout(io.StringIO()), mock.patch.object(sys, "platform", "darwin"):
            main(["init", "--yes", "--name", "client"])
        patcher = mock.patch.object(sys, "platform", "darwin")
        patcher.start()
        self.addCleanup(patcher.stop)

    def add(self, *args, stdin=""):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
                mock.patch.object(sys, "stdin", io.StringIO(stdin)):
            code = main(["add", "convex", *args])
        return code, out.getvalue(), err.getvalue()

    def registry(self):
        return tomllib.loads((self.config / "registry.toml").read_text())

    def test_the_clis_own_login(self):
        code, out, err = self.add("--cli")
        self.assertEqual(code, 1)
        self.assertIn("could not read the convex CLI's login: the convex CLI is not signed in\n  → sign in: npx convex "
                      "login, then: keyfit add convex --cli", err)
        self.cli_login(ACME)
        code, out, err = self.add("--cli")
        self.assertEqual(code, 0, err)
        self.assertEqual(out, "client: convex account 'acme' → team Acme (acme) via the convex CLI's login "
                              "(~/.convex/config.json)\n")
        self.assertEqual(self.registry()["accounts"]["convex"]["acme"], {"token": "cli", "team": "acme",
                                                                         "team_name": "Acme"})
        self.assertNotIn("secrets", self.registry())  # nothing stored in the Keychain
        self.assertNotIn(ACME, (self.config / "registry.toml").read_text())

    def test_copy_of_the_clis_login_is_kept_in_the_keychain(self):
        os.remove(self.stub / "keychain.json")
        self.cli_login(ACME)
        code, out, err = self.add("--copy")
        self.assertEqual(code, 0, err)
        self.assertEqual(out, "client: convex account 'acme' → team Acme (acme) via Keychain item keyfit/convex-acme, "
                              "a copy of the convex CLI's login (~/.convex/config.json)\n")
        self.assertEqual(json.loads((self.stub / "keychain.json").read_text())["keyfit/convex-acme"], ACME)
        self.assertEqual(self.registry()["secrets"]["keychain"], ["convex-acme"])
        self.assertIn(["-i"], self.security_calls())  # the token went in through stdin, never argv
        for argv in self.security_calls():
            self.assertNotIn(ACME, " ".join(argv))
        self.assertNotIn(ACME, out + err + (self.config / "registry.toml").read_text())

    def test_pasted_token_is_checked_then_kept(self):
        code, out, err = self.add("--name", "work", stdin=GLOBEX + "\n")
        self.assertEqual(code, 0, err)
        self.assertEqual(out, "client: convex account 'work' → team Globex (globex) via Keychain item "
                              "keyfit/convex-work\n")
        self.assertEqual(json.loads((self.stub / "keychain.json").read_text())["keyfit/convex-work"], GLOBEX)

    def test_token_in_a_variable(self):
        code, out, err = self.add("--token", "GLOBEX_CONVEX_TOKEN")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.registry()["accounts"]["convex"]["globex"],
                         {"token": "env:GLOBEX_CONVEX_TOKEN", "team": "globex", "team_name": "Globex"})

    def test_several_teams_need_a_choice_unless_the_folder_decides(self):
        os.environ["BOTH_TOKEN"] = BOTH
        code, out, err = self.add("--token", "BOTH_TOKEN")
        self.assertEqual(code, 1)
        self.assertIn("this login belongs to 2 teams (Acme (acme), Globex (globex)): pick one with --team SLUG", err)
        code, out, err = self.add("--token", "BOTH_TOKEN", "--team", "Globex")
        self.assertEqual(code, 0, err)
        self.assertIn("team Globex (globex)", out)
        self.env_local(self.repos["client"], "CONVEX_DEPLOYMENT=dev:happy-animal-123\n")
        code, out, err = self.add("--token", "BOTH_TOKEN", "--name", "fromfolder")
        self.assertEqual(code, 0, err)
        self.assertIn("team Acme (acme)", out)
        code, out, err = self.add("--token", "BOTH_TOKEN", "--team", "initech")
        self.assertEqual(code, 1)
        self.assertIn("this login has no team initech", err)

    def test_deploy_keys_and_tokens_in_options_are_refused_without_being_sent(self):
        code, out, err = self.add(stdin=f"prod:happy-animal-123|{SECRET}\n")
        self.assertEqual(code, 1)
        self.assertIn("that value holds a deploy key (…|…), not an access token, so it was not sent to Convex", err)
        for args in (["--token", ACME], ["--name", ACME], ["--team", f"prod:x-y-1|{SECRET}"]):
            code, out, err = self.add(*args)
            self.assertEqual(code, 1, args)
            self.assertNotIn(ACME, out + err)
            self.assertNotIn(SECRET, out + err)
        self.assertEqual(self.fetched, [])

    def test_guided_init_offers_the_clis_login(self):
        with mock.patch.object(cli, "_ask", return_value="y"), contextlib.redirect_stdout(io.StringIO()) as out:
            cli._offer_convex("client", {"registry": None, "project": "client"})
        self.assertIn("Convex: the convex CLI is not signed in — later: npx convex login, then: keyfit add convex --cli",
                      out.getvalue())
        self.cli_login(ACME)
        with mock.patch.object(cli, "_ask", return_value="n"), contextlib.redirect_stdout(io.StringIO()) as out:
            cli._offer_convex("client", {"registry": None, "project": "client"})
        self.assertIn("Convex: the convex CLI is signed in (~/.convex/config.json)", out.getvalue())
        self.assertEqual(self.fetched, [])  # nothing is sent before a yes
        with mock.patch.object(cli, "_ask", return_value="y"), contextlib.redirect_stdout(io.StringIO()) as out:
            cli._offer_convex("client", {"registry": None, "project": "client"})
        self.assertIn("client: convex account 'acme' → team Acme (acme) via the convex CLI's login", out.getvalue())

    def test_status_and_doctor_lines(self):
        self.add("--token", "env:GLOBEX_CONVEX_TOKEN")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            main(["status"])
        self.assertIn("✔ convex    Globex   login and team confirmed", out.getvalue())
        os.remove(self.bin / "convex")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            main(["doctor"])
        self.assertIn("✔ convex    not installed globally   npx convex gets its account from claude and codex sessions",
                      out.getvalue())


class CacheKeyTests(unittest.TestCase):
    def test_deployment_confirmations_do_not_share_the_check_key(self):
        conn = {"token": "env:X", "team": "acme"}
        self.assertNotEqual(cache.key("p", "convex", conn, "f"),
                            cache.key("p", "convex-deployment", {"name": "happy-animal-123", "team": "acme"}, "f"))


if __name__ == "__main__":
    unittest.main()
