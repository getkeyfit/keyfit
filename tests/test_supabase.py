"""Supabase: the adapter, `keyfit add supabase` and the supabase shim, with a stub `security`, a fake supabase and a
stand-in for its Management API. The real Keychain, ~/.supabase and the network are never touched."""

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
from keyfit.adapters import ADAPTERS, supabase  # noqa: E402
from keyfit.adapters.supabase import SupabaseAdapter  # noqa: E402
from keyfit.cli import main  # noqa: E402
from keyfit.registry import Project, Registry  # noqa: E402
from test_secrets import FAKE_SECURITY  # noqa: E402
from test_vercel import Executed, executable  # noqa: E402

# Fake tokens and refs; never real ones.
TOKEN = "sbp_" + "a1" * 20          # member of Acme
GLOBEX = "sbp_" + "b2" * 20         # member of Globex
BOTH = "sbp_v0_" + "c3" * 20        # member of Acme and Globex (newer token shape)
REVOKED = "sbp_" + "d4" * 20
MEMBERS = {TOKEN: ["acme-org"], GLOBEX: ["globex-org"], BOTH: ["acme-org", "globex-org"]}
ORG_NAMES = {"acme-org": "Acme", "globex-org": "Globex", "initech-org": "Initech"}
ACME_REF, GLOBEX_REF = "acme" * 5, "globex" * 3 + "gl"
BRANCH_REF, FOREIGN_REF = "branch" * 3 + "br", "foreign" * 2 + "foreig"  # a preview branch; a project of Initech
PROJECTS = {ACME_REF: "acme-org", GLOBEX_REF: "globex-org", FOREIGN_REF: "initech-org"}

FAKE_SUPABASE = """#!/bin/sh
echo "supabase ran" >&2
exit 0
"""


def api(path, token, timeout):
    """Stands in for api.supabase.com: a token sees its organizations and their projects."""
    if token not in MEMBERS:
        return 401, {"message": "Unauthorized"}
    if path == "/v1/organizations":
        return 200, [{"id": slug, "slug": slug, "name": ORG_NAMES[slug]} for slug in MEMBERS[token]]
    if path.startswith("/v1/projects/"):
        ref = path.rsplit("/", 1)[1]
        if ref not in PROJECTS:
            return 404, None
        if PROJECTS[ref] not in MEMBERS[token]:
            return 403, None
        return 200, {"id": ref, "ref": ref, "organization_id": PROJECTS[ref], "organization_slug": PROJECTS[ref],
                     "name": f"{ref[:4]} app", "region": "eu-central-1", "status": "ACTIVE_HEALTHY"}
    return 404, None


class SupabaseCase(unittest.TestCase):
    """A temporary HOME, a stub security with Keyfit's item for Acme, a fake supabase and three projects:
    client (Acme, token in the Keychain), mine (Globex, token in a variable) and plain (no Supabase account)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        executable(self.bin / "supabase", FAKE_SUPABASE)
        self.stub = self.tmp / "stub"
        self.stub.mkdir()
        executable(self.stub / "security", FAKE_SECURITY)
        self.keychain({"keyfit/supabase-acme": TOKEN})
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
                    "KEYFIT_CONFIG_DIR": str(self.config), "GLOBEX_SUPABASE_TOKEN": GLOBEX}
        self.fetched = []

        def fetch(path, token, timeout):
            self.fetched.append(path)
            return api(path, token, timeout)
        for patcher in (mock.patch("urllib.request.urlopen", side_effect=AssertionError("no network in tests")),
                        mock.patch.object(ADAPTERS["supabase"], "fetch", fetch),
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

    def link(self, folder: Path, ref: str):
        (folder / "supabase" / ".temp").mkdir(parents=True, exist_ok=True)
        (folder / "supabase" / "config.toml").write_text('project_id = "app"\n')
        (folder / "supabase" / ".temp" / "project-ref").write_text(ref + "\n")

    def register(self):
        reg = Registry(self.config / "registry.toml")
        for name, repo in self.repos.items():
            reg.add_project(name, repo)
        reg.set_account("supabase", "acme", {"token": "keychain:keyfit/supabase-acme", "org": "acme-org",
                                             "org_name": "Acme"})
        reg.set_account("supabase", "globex", {"token": "env:GLOBEX_SUPABASE_TOKEN", "org": "globex-org",
                                               "org_name": "Globex"})
        reg.set_connection("client", "supabase", "acme")
        reg.set_connection("mine", "supabase", "globex")
        reg.save()
        return reg

    def conn(self, **changes):
        return dict({"name": "acme", "token": "keychain:keyfit/supabase-acme", "org": "acme-org", "org_name": "Acme"},
                    **changes)


class AdapterTests(SupabaseCase):
    def setUp(self):
        super().setUp()
        self.supabase = SupabaseAdapter(fetch=api, timeout=5)

    def check(self, conn, **extra):
        env = dict(self.env, **extra)
        self.supabase.inject(conn, env)
        return self.supabase.verify(conn, env)

    def test_inject_puts_the_accounts_token_and_the_default_profile(self):
        env = dict(self.env, SUPABASE_ACCESS_TOKEN=GLOBEX, SUPABASE_PROFILE="evil.yaml")
        self.supabase.inject(self.conn(), env)
        self.assertEqual(env["SUPABASE_ACCESS_TOKEN"], TOKEN)
        self.assertEqual(env["SUPABASE_PROFILE"], "supabase")  # beats a profile file that names another API host
        self.assertEqual(env["KEYFIT_SUPABASE"], "acme")

    def test_a_token_in_supabase_access_token_itself_can_be_the_source(self):
        env = dict(self.env, SUPABASE_ACCESS_TOKEN=GLOBEX)
        self.supabase.inject(self.conn(token="env:SUPABASE_ACCESS_TOKEN", org="globex-org"), env)
        self.assertEqual(env["SUPABASE_ACCESS_TOKEN"], GLOBEX)

    def test_supabase_access_token_as_the_source_survives_its_own_session(self):
        conn = self.conn(name="globex", token="env:SUPABASE_ACCESS_TOKEN", org="globex-org")
        project = Project("mine", [str(self.repos["mine"])], {"supabase": conn})
        session = environment.build(project, dict(self.env, SUPABASE_ACCESS_TOKEN=GLOBEX))  # e.g. claude
        nested = environment.build(project, session)  # supabase, or keyfit status, inside that session
        self.assertEqual(nested["SUPABASE_ACCESS_TOKEN"], GLOBEX)
        other = Project("client", [str(self.repos["client"])], {"supabase": self.conn(token="env:SUPABASE_ACCESS_TOKEN")})
        self.assertNotIn("SUPABASE_ACCESS_TOKEN", environment.build(other, session))  # never another account's

    def test_another_projects_token_does_not_follow_into_a_project_without_supabase(self):
        client = Project("client", [str(self.repos["client"])], {"supabase": self.conn()})
        plain = Project("plain", [str(self.repos["plain"])], {})
        session = environment.build(client, dict(self.env))  # e.g. a Claude session of client
        self.assertEqual(session["SUPABASE_ACCESS_TOKEN"], TOKEN)
        nested = environment.build(plain, session)  # a tool of plain started from inside it
        for var in ("SUPABASE_ACCESS_TOKEN", "SUPABASE_PROFILE", "KEYFIT_SUPABASE"):
            self.assertNotIn(var, nested)
        own = environment.build(plain, dict(self.env, SUPABASE_ACCESS_TOKEN=GLOBEX))  # the user's own: kept
        self.assertEqual(own["SUPABASE_ACCESS_TOKEN"], GLOBEX)

    def test_organization_confirmed(self):
        check = self.check(self.conn())
        self.assertEqual((check.status, check.label, check.detail), ("ok", "Acme", "token and organization confirmed"))

    def test_rejected_token(self):
        self.keychain({"keyfit/supabase-acme": REVOKED})
        check = self.check(self.conn())
        self.assertTrue(check.failed)
        self.assertEqual(check.detail, "Supabase rejected the token (deleted or expired?)")
        self.assertIn("keyfit add supabase --name acme", check.fix)
        self.assertFalse(check.transient)

    def test_token_of_another_organization(self):
        check = self.check(self.conn(token="env:GLOBEX_SUPABASE_TOKEN"))
        self.assertTrue(check.failed)
        self.assertEqual(check.detail, "this token has no access to organization Acme (acme-org)")
        self.assertEqual(check.fix, "keyfit add supabase --name acme   (a token of a member of Acme)")

    def test_missing_malformed_or_doubled_token_fails_before_the_network(self):
        self.keychain({"keyfit/supabase-acme": ""})
        self.assertEqual(self.check(self.conn()).detail, "token not found (Keychain item keyfit/supabase-acme)")
        self.keychain({"keyfit/supabase-acme": "sbp_nothex"})
        self.assertEqual(self.check(self.conn()).detail,
                         "Keychain item keyfit/supabase-acme does not hold a Supabase access token (sbp_…)")
        self.keychain({"keyfit/supabase-acme": TOKEN + TOKEN})
        self.assertEqual(self.check(self.conn()).detail, "the token in Keychain item keyfit/supabase-acme looks pasted twice")
        check = self.check(self.conn(token="env:UNSET_VAR"))
        self.assertEqual((check.detail, check.fix),
                         ("token not found ($UNSET_VAR)", "set $UNSET_VAR to a token from "
                          "supabase.com/dashboard/account/tokens"))

    def test_organization_not_recorded(self):
        check = self.check(self.conn(org=None, org_name=None))
        self.assertEqual((check.detail, check.fix), ("no expected organization recorded",
                                                     "keyfit add supabase --name acme"))

    def test_linked_project_of_the_organization(self):
        self.link(self.repos["client"], ACME_REF)
        sub = self.repos["client"] / "supabase" / "functions"
        sub.mkdir()
        os.chdir(sub)  # the CLI finds supabase/config.toml upwards, and so does the check
        check = self.check(self.conn())
        self.assertEqual((check.status, check.detail),
                         ("ok", f"token, organization and linked project {ACME_REF} confirmed"))

    def test_linked_to_a_project_of_another_organization(self):
        self.keychain({"keyfit/supabase-acme": BOTH})  # a member of both: the token reaches it
        self.link(self.repos["client"], GLOBEX_REF)
        check = self.check(self.conn())
        self.assertTrue(check.failed)
        self.assertEqual(check.detail, f"supabase/.temp/project-ref links this folder to project {GLOBEX_REF} of "
                                       "another organization (Globex), project expects Acme"
                         .replace("supabase/.temp", str(self.repos["client"] / "supabase" / ".temp"), 1))
        self.assertEqual(check.fix, "supabase link   (a project of Acme)")

    def test_linked_to_a_project_the_token_cannot_reach(self):
        self.link(self.repos["client"], FOREIGN_REF)
        check = self.check(self.conn())
        self.assertTrue(check.failed)
        self.assertIn(f"links this folder to project {FOREIGN_REF}: this token cannot reach that project", check.detail)

    def test_preview_branch_is_a_warning_not_a_failure(self):
        self.link(self.repos["client"], BRANCH_REF)
        check = self.check(self.conn())
        self.assertEqual(check.status, "warn")
        self.assertIn(f"names {BRANCH_REF}, which this token does not find as a project (a preview branch?)",
                      check.detail)

    def test_the_typescript_clis_other_links_are_checked_too(self):
        # stack mode links through supabase/.link.json; `branches` acts on the parent in .temp/linked-project.json
        self.keychain({"keyfit/supabase-acme": BOTH})
        folder = self.repos["client"] / "supabase"
        (folder / ".temp").mkdir(parents=True)
        (folder / "config.toml").write_text('project_id = "app"\n')
        (folder / ".link.json").write_text(json.dumps({"project": {"ref": GLOBEX_REF}, "fetchedAt": "x",
                                                       "active_branch": {"ref": BRANCH_REF, "name": "dev"}}))
        check = self.check(self.conn())
        self.assertTrue(check.failed)
        self.assertIn(f".link.json links this folder to project {GLOBEX_REF} of another organization (Globex)",
                      check.detail)
        (folder / ".link.json").unlink()
        (folder / ".temp" / "project-ref").write_text(BRANCH_REF)
        (folder / ".temp" / "linked-project.json").write_text(json.dumps({"ref": GLOBEX_REF, "name": "globex app"}))
        check = self.check(self.conn())
        self.assertTrue(check.failed)
        self.assertIn(f"linked-project.json links this folder to project {GLOBEX_REF} of another organization",
                      check.detail)
        (folder / ".temp" / "linked-project.json").write_text(json.dumps({"ref": ACME_REF}))
        self.assertEqual(self.check(self.conn()).status, "warn")  # the parent is Acme's, the branch is not checked
        (folder / ".temp" / "project-ref").unlink()  # the CLI reads the parent only next to a linked ref
        (folder / ".temp" / "linked-project.json").write_text(json.dumps({"ref": GLOBEX_REF}))
        self.assertEqual(self.check(self.conn()).status, "ok")

    def test_project_id_variable_and_workdir_are_followed_like_the_cli(self):
        check = self.check(self.conn(), SUPABASE_PROJECT_ID=GLOBEX_REF)
        self.assertTrue(check.failed)
        self.assertIn(f"$SUPABASE_PROJECT_ID links this folder to project {GLOBEX_REF}", check.detail)
        other = self.tmp / "elsewhere"
        self.link(other, FOREIGN_REF)
        check = self.check(self.conn(), SUPABASE_WORKDIR=str(other))
        self.assertIn(f"project {FOREIGN_REF}: this token cannot reach that project", check.detail)

    def test_network_error_is_transient_and_a_recent_success_carries_it(self):
        project = Project("client", [str(self.repos["client"])], {"supabase": self.conn()})
        env = environment.build(project, dict(self.env))
        self.assertEqual(environment.verify(project, env)[0].status, "ok")  # cached

        def down(path, token, timeout):
            raise urllib.error.URLError("Network is unreachable")
        with mock.patch.object(ADAPTERS["supabase"], "fetch", down):
            check = environment.verify(project, env)[0]
        self.assertEqual(check.status, "warn")
        self.assertIn("could not re-check now (Supabase did not answer (Network is unreachable))", check.detail)

    def test_busy_api_is_transient(self):
        self.supabase.fetch = lambda path, token, timeout: (503, None)
        check = self.check(self.conn())
        self.assertTrue(check.failed and check.transient)
        self.assertEqual(check.fix, "check the network and retry")

    def test_fingerprint_follows_the_token_and_the_link(self):
        env = dict(self.env)
        self.supabase.inject(self.conn(), env)
        first = self.supabase.fingerprint(self.conn(), env)
        self.link(self.repos["client"], ACME_REF)
        linked = self.supabase.fingerprint(self.conn(), env)
        self.assertNotEqual(first, linked)
        self.keychain({"keyfit/supabase-acme": BOTH})
        self.supabase.inject(self.conn(), env)
        self.assertNotEqual(linked, self.supabase.fingerprint(self.conn(), env))
        self.assertNotIn(BOTH, linked + first)

    def test_words_skip_flags_and_option_values(self):
        self.assertEqual(supabase.words(["--debug", "--yes", "login"]), ["login"])
        self.assertEqual(supabase.words(["--workdir", "w", "-o", "json", "projects", "delete", "--debug", "r"]),
                         ["projects", "delete", "r"])
        self.assertEqual(supabase.words(["link", "-p", "secret", "--project-ref=x", "--", "r"]), ["link"])
        self.assertEqual(supabase.words(["--agent", "no", "--output-format", "json", "projects", "delete", "r"]),
                         ["projects", "delete", "r"])  # root options of the Go and the TypeScript CLI

    def test_options_are_read_like_the_cli(self):
        self.assertEqual(supabase.options(["link", "--project-ref", "x", "--profile=p", "--workdir", "w", "--org-id"]),
                         [("--project-ref", "x"), ("--profile", "p"), ("--workdir", "w"), ("--org-id", None)])
        self.assertEqual(supabase.options(["functions", "deploy", "--", "--project-ref", "x"]), [])

    def test_the_clis_login_is_found_without_reading_it_then_read_and_decoded(self):
        self.assertEqual(supabase.cli_signed_in(self.env), "")
        self.assertEqual(supabase.read_cli_login(self.env), (None, "the supabase CLI is not signed in"))
        encoded = "go-keyring-base64:" + base64.b64encode(TOKEN.encode()).decode()  # how the Go CLI stores it
        self.keychain({"Supabase CLI/supabase": encoded})
        self.assertEqual(supabase.cli_signed_in(self.env), "Keychain item Supabase CLI/supabase")
        self.assertNotIn("-w", self.security_calls()[-1])  # finding it reads no secret
        self.assertEqual(supabase.read_cli_login(self.env), (TOKEN, "Keychain item Supabase CLI/supabase"))
        self.keychain({"Supabase CLI/supabase": GLOBEX})  # how the TypeScript CLI stores it: plain
        self.assertEqual(supabase.read_cli_login(self.env)[0], GLOBEX)

    def test_older_logins_and_the_token_file(self):
        self.keychain({"Supabase CLI/access-token": TOKEN})
        self.assertEqual(supabase.read_cli_login(self.env), (TOKEN, "Keychain item Supabase CLI/access-token"))
        self.keychain({"Supabase CLI/access-token": ""})  # gone from the Keychain: the file the CLI falls back to
        os.remove(self.stub / "keychain.json")
        folder = self.tmp / "supabase-home"
        folder.mkdir()
        (folder / "access-token").write_text(GLOBEX)
        login = supabase.read_cli_login(dict(self.env, SUPABASE_HOME=str(folder)))
        self.assertEqual(login[0], GLOBEX)
        self.assertTrue(login[1].endswith("supabase-home/access-token"))


class ShimTests(SupabaseCase):
    """The supabase shim, run in-process with os.execve caught."""

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
                code = shim.main(["supabase", *args])
            except Executed:
                code = None
        self.stderr = err.getvalue()
        return code, seen.get("argv"), seen.get("env")

    def test_each_project_gets_its_own_token(self):
        code, argv, env = self.run_shim("projects", "list", SUPABASE_ACCESS_TOKEN=GLOBEX, SUPABASE_PROFILE="x")
        self.assertIsNone(code, self.stderr)
        self.assertEqual(argv, ["supabase", "projects", "list"])
        self.assertEqual((env["SUPABASE_ACCESS_TOKEN"], env["SUPABASE_PROFILE"]), (TOKEN, "supabase"))
        code, argv, env = self.run_shim("projects", "list", cwd=self.repos["mine"])
        self.assertEqual(env["SUPABASE_ACCESS_TOKEN"], GLOBEX)

    def test_project_without_supabase_runs_as_before(self):
        # Supabase is often used only locally (supabase start): no account, no refusal
        code, argv, env = self.run_shim("start", cwd=self.repos["plain"], SUPABASE_ACCESS_TOKEN=GLOBEX)
        self.assertIsNone(code, self.stderr)
        self.assertEqual(argv, ["supabase", "start"])
        self.assertEqual(env["SUPABASE_ACCESS_TOKEN"], GLOBEX)  # the user's own variable stays
        self.assertEqual(self.stderr, "")

    def test_dry_run_prints_a_project_without_supabase_instead_of_running_it(self):
        code, argv, _ = self.run_shim("db", "push", cwd=self.repos["plain"], KEYFIT_DRY_RUN="1")
        self.assertEqual((code, argv), (0, None), self.stderr)  # never started
        self.assertIn("keyfit: project plain has no supabase account mapped: supabase runs without checks, as before "
                      "Keyfit\n  would run: ", self.stderr)
        self.assertIn("supabase db push\n  environment changes (names only): ", self.stderr)

    def test_unregistered_folder_passes_through(self):
        code, argv, env = self.run_shim("--profile", "staging", "link", "--project-ref", GLOBEX_REF, cwd=self.outside,
                                        SUPABASE_ACCESS_TOKEN=GLOBEX)
        self.assertEqual(argv, ["supabase", "--profile", "staging", "link", "--project-ref", GLOBEX_REF])
        self.assertEqual(env["SUPABASE_ACCESS_TOKEN"], GLOBEX)
        self.assertEqual(self.fetched, [])

    def test_failed_check_blocks_with_the_fix(self):
        self.keychain({"keyfit/supabase-acme": REVOKED})
        code, argv, _ = self.run_shim("db", "push")
        self.assertEqual((code, argv), (1, None))
        self.assertIn("keyfit: supabase not started, client needs attention", self.stderr)
        self.assertIn("✘ supabase   Acme   Supabase rejected the token (deleted or expired?)", self.stderr)
        self.assertIn("→ keyfit add supabase --name acme   (paste a token from supabase.com/dashboard/account/tokens, "
                      "or add --cli for the supabase CLI's login)", self.stderr)

    def test_folder_linked_to_another_organization_blocks(self):
        self.keychain({"keyfit/supabase-acme": BOTH})
        self.link(self.repos["client"], GLOBEX_REF)
        code, argv, _ = self.run_shim("db", "push")
        self.assertEqual(code, 1)
        self.assertIn(f"links this folder to project {GLOBEX_REF} of another organization (Globex)", self.stderr)

    def test_project_refs_of_another_organization_are_refused(self):
        self.keychain({"keyfit/supabase-acme": BOTH})
        for args in (["link", "--project-ref", GLOBEX_REF], [f"--project-ref={GLOBEX_REF}", "db", "push"],
                     ["link", GLOBEX_REF], ["projects", "delete", GLOBEX_REF],
                     ["gen", "types", "--lang", "typescript", "--project-id", GLOBEX_REF]):
            code, argv, _ = self.run_shim(*args)
            self.assertEqual((code, argv), (1, None), args)
            self.assertIn(f"keyfit: this folder belongs to client (Supabase organization Acme); refusing", self.stderr)
            self.assertIn("it belongs to another organization (globex-org)", self.stderr)
        for args in (["--yes", "projects", "delete", GLOBEX_REF], ["projects", "delete", "--debug", GLOBEX_REF],
                     ["--yes", "gen", "types", "--project-id", GLOBEX_REF],
                     ["link", "--unknown-option", "value", GLOBEX_REF]):
            code, argv, _ = self.run_shim(*args)
            self.assertEqual((code, argv), (1, None), args)
        code, argv, _ = self.run_shim("link", "--project-ref", ACME_REF)
        self.assertEqual(argv, ["supabase", "link", "--project-ref", ACME_REF])
        code, argv, _ = self.run_shim("link", "--project-ref", FOREIGN_REF)
        self.assertEqual(code, 1)
        self.assertIn(f"refusing --project-ref {FOREIGN_REF}: this token cannot reach that project", self.stderr)

    def test_an_option_with_a_value_before_the_command_hides_no_ref(self):
        self.keychain({"keyfit/supabase-acme": BOTH})
        for args in (["--agent", "no", "projects", "delete", GLOBEX_REF],
                     ["--agent", "yes", "gen", "types", "typescript", "--project-id", GLOBEX_REF],
                     ["--output-format", "json", "link", GLOBEX_REF],
                     ["--some-future-option", "value", "projects", "delete", GLOBEX_REF],  # unknown to Keyfit
                     ["--some-future-option", "value", "gen", "types", "--project-id", GLOBEX_REF]):
            code, argv, _ = self.run_shim(*args)
            self.assertEqual((code, argv), (1, None), args)
            self.assertIn("it belongs to another organization (globex-org)", self.stderr)
        self.run_shim("--some-future-option", "value", "projects", "delete", GLOBEX_REF)
        self.assertIn(f"refusing value projects delete {GLOBEX_REF}: it belongs", self.stderr)
        code, argv, _ = self.run_shim("--agent", "no", "projects", "delete", ACME_REF)
        self.assertIsNone(code, self.stderr)

    def test_the_typescript_clis_links_count_as_the_folders_own(self):
        self.keychain({"keyfit/supabase-acme": BOTH})
        folder = self.repos["client"] / "supabase"
        folder.mkdir()
        (folder / "config.toml").write_text('project_id = "app"\n')
        (folder / ".link.json").write_text(json.dumps({"project": {"ref": GLOBEX_REF}, "fetchedAt": "x",
                                                       "active_branch": {"ref": GLOBEX_REF}}))
        code, argv, _ = self.run_shim("db", "push")
        self.assertEqual((code, argv), (1, None))
        self.assertIn(f".link.json links this folder to project {GLOBEX_REF} of another organization", self.stderr)
        other = self.repos["client"] / "apps" / "other" / "supabase"
        (other / ".temp").mkdir(parents=True)
        (other / ".temp" / "project-ref").write_text(ACME_REF)
        (other / ".temp" / "linked-project.json").write_text(json.dumps({"ref": GLOBEX_REF}))
        (folder / ".link.json").unlink()
        code, argv, _ = self.run_shim("--workdir", "apps/other", "branches", "create", "x")
        self.assertEqual(code, 1)
        self.assertIn(f"refusing --workdir apps/other (linked to {GLOBEX_REF} in", self.stderr)

    def test_confirmed_project_refs_are_cached(self):
        self.run_shim("link", "--project-ref", ACME_REF)
        self.fetched.clear()
        code, argv, _ = self.run_shim("link", "--project-ref", ACME_REF)
        self.assertIsNone(code, self.stderr)
        self.assertEqual(self.fetched, [])

    def test_branches_and_local_ids_go_ahead(self):
        for args in (["link", "--project-ref", BRANCH_REF], ["link", "--project-ref", "my-feature"],
                     ["stop", "--project-id", GLOBEX_REF]):
            code, argv, _ = self.run_shim(*args)
            self.assertEqual(argv, ["supabase", *args], self.stderr)

    def test_workdir_linked_to_another_organization_is_refused(self):
        self.keychain({"keyfit/supabase-acme": BOTH})
        self.link(self.repos["client"] / "apps" / "other", GLOBEX_REF)
        code, argv, _ = self.run_shim("--workdir", "apps/other", "db", "push")
        self.assertEqual(code, 1)
        self.assertIn(f"refusing --workdir apps/other (linked to {GLOBEX_REF} in", self.stderr)

    def test_other_profile_and_organization_are_refused(self):
        for args, shown in ((["--profile", "snap", "projects", "list"], "--profile snap"),
                            (["projects", "create", "x", "--org-id", "globex-org"], "--org-id globex-org")):
            code, argv, _ = self.run_shim(*args)
            self.assertEqual(code, 1)
            self.assertIn(f"refusing {shown}", self.stderr)
        code, argv, _ = self.run_shim("--profile", "supabase", "projects", "create", "x", "--org-id", "acme-org")
        self.assertIsNone(code, self.stderr)

    def test_login_in_a_project_with_an_account_is_refused_with_the_fix(self):
        code, argv, _ = self.run_shim("login")
        self.assertEqual((code, argv), (1, None))
        self.assertIn("keyfit: this folder belongs to client, whose Supabase token Keyfit keeps (Keychain item "
                      "keyfit/supabase-acme); `supabase login` would change only the supabase CLI's own login",
                      self.stderr)
        self.assertIn("Another token for client: keyfit add supabase --name acme", self.stderr)
        code, argv, _ = self.run_shim("logout", "--yes")
        self.assertEqual(code, 1)
        for args in (["--debug", "login"], ["--yes", "--workdir", ".", "logout"]):  # flags first: still a sign-in
            code, argv, _ = self.run_shim(*args)
            self.assertEqual((code, argv), (1, None), args)
        self.assertEqual(self.fetched, [])  # no check ran

    def test_login_elsewhere_runs_without_injected_variables(self):
        session = environment.build(Registry.load(self.config / "registry.toml").get("client"), dict(self.env))
        code, argv, env = self.run_shim("login", cwd=self.repos["plain"], **{k: session[k] for k in
                                        ("SUPABASE_ACCESS_TOKEN", "SUPABASE_PROFILE", "KEYFIT_SUPABASE")})
        self.assertEqual(argv, ["supabase", "login"])
        for var in ("SUPABASE_ACCESS_TOKEN", "SUPABASE_PROFILE"):
            self.assertNotIn(var, env)  # the 2.1xx CLI would store an injected token as its own login
        code, argv, env = self.run_shim("login", cwd=self.repos["plain"], SUPABASE_PROFILE="selfhosted",
                                        SUPABASE_ACCESS_TOKEN=GLOBEX)
        self.assertEqual((env["SUPABASE_PROFILE"], env["SUPABASE_ACCESS_TOKEN"]), ("selfhosted", GLOBEX))  # yours

    def test_session_keeps_supabase_on_its_project(self):
        code, argv, env = self.run_shim("projects", "list", cwd=self.outside, KEYFIT_PROJECT="mine")
        self.assertEqual(env["SUPABASE_ACCESS_TOKEN"], GLOBEX)

    def test_claude_session_gets_the_token_for_npx_supabase(self):
        project = Registry.load(self.config / "registry.toml").get("client")
        env = environment.build(project, dict(self.env))  # what `claude` starts with (every connection)
        self.assertEqual((env["SUPABASE_ACCESS_TOKEN"], env["SUPABASE_PROFILE"]), (TOKEN, "supabase"))

    def test_no_token_in_arguments_output_or_files(self):
        for repo in ("client", "mine"):
            code, argv, env = self.run_shim("db", "push", cwd=self.repos[repo])
            self.assertIsNone(code, self.stderr)
            for token in (TOKEN, GLOBEX):
                self.assertNotIn(token, " ".join(argv) + self.stderr)
        for path in (self.config / "registry.toml", self.config / "cache" / "checks.json"):
            for token in (TOKEN, GLOBEX):
                self.assertNotIn(token, path.read_text(), path)
        for argv in self.security_calls():
            self.assertNotIn(TOKEN, " ".join(argv))


class AddSupabaseTests(SupabaseCase):
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
            code = main(["add", "supabase", *args])
        return code, out.getvalue(), err.getvalue()

    def registry(self):
        return tomllib.loads((self.config / "registry.toml").read_text())

    def test_pasted_token_is_checked_then_kept_in_the_keychain(self):
        self.keychain({"keyfit/supabase-acme": ""})
        os.remove(self.stub / "keychain.json")
        code, out, err = self.add(stdin=TOKEN + "\n")
        self.assertEqual(code, 0, err)
        self.assertEqual(out, "client: supabase account 'acme' → organization Acme (acme-org) via Keychain item "
                              "keyfit/supabase-acme\n")
        reg = self.registry()
        self.assertEqual(reg["projects"]["client"]["supabase"], "acme")
        self.assertEqual(reg["accounts"]["supabase"]["acme"], {"token": "keychain:keyfit/supabase-acme",
                                                               "org": "acme-org", "org_name": "Acme"})
        self.assertEqual(reg["secrets"]["keychain"], ["supabase-acme"])
        self.assertEqual(json.loads((self.stub / "keychain.json").read_text())["keyfit/supabase-acme"], TOKEN)
        self.assertIn(["-i"], self.security_calls())  # the token went in through stdin, never argv
        for argv in self.security_calls():
            self.assertNotIn(TOKEN, " ".join(argv))
        self.assertNotIn(TOKEN, out + err + (self.config / "registry.toml").read_text())

    def test_default_name_never_takes_another_organizations_account(self):
        self.add("--token", "env:GLOBEX_SUPABASE_TOKEN")  # account "globex"
        with mock.patch.dict(ORG_NAMES, {"acme-org": "Globex"}):  # another organization, same display name
            os.environ["ACME_TOKEN"] = TOKEN
            code, out, err = self.add("--token", "ACME_TOKEN")
        self.assertEqual(code, 0, err)
        accounts = self.registry()["accounts"]["supabase"]
        self.assertEqual((accounts["globex"]["org"], accounts["globex-2"]["org"]), ("globex-org", "acme-org"))
        self.assertIn("supabase account 'globex-2'", out)

    def test_token_typed_as_an_option_is_refused(self):
        code, out, err = self.add("--token", TOKEN)
        self.assertEqual(code, 1)
        self.assertIn("--token takes where the token lives", err)
        self.assertNotIn(TOKEN, out + err)
        for option in ("--name", "--org"):
            code, out, err = self.add("--token", "env:GLOBEX_SUPABASE_TOKEN", option, TOKEN)
            self.assertEqual(code, 1, option)
            self.assertIn("take a short name, not a token", err)
            self.assertNotIn(TOKEN, out + err)
        self.assertEqual(self.fetched, [])
        self.assertFalse((self.config / "registry.toml").exists() and TOKEN in (self.config / "registry.toml").read_text())

    def test_something_else_is_never_sent_to_supabase(self):
        code, out, err = self.add(stdin="ghp_" + "x" * 36 + "\n")
        self.assertEqual(code, 1)
        self.assertIn("that is not a Supabase access token (sbp_…), so it was not sent to Supabase", err)
        self.assertEqual(self.fetched, [])

    def test_token_in_a_variable(self):
        code, out, err = self.add("--token", "env:GLOBEX_SUPABASE_TOKEN", "--name", "gx")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.registry()["accounts"]["supabase"]["gx"],
                         {"token": "env:GLOBEX_SUPABASE_TOKEN", "org": "globex-org", "org_name": "Globex"})
        self.assertNotIn("secrets", self.registry())  # nothing stored in the Keychain

    def test_several_organizations_need_a_choice(self):
        os.environ["BOTH_TOKEN"] = BOTH
        code, out, err = self.add("--token", "BOTH_TOKEN")
        self.assertEqual(code, 1)
        self.assertIn("this login belongs to 2 organizations (Acme (acme-org), Globex (globex-org)): pick one with "
                      "--org SLUG", err)
        code, out, err = self.add("--token", "BOTH_TOKEN", "--org", "globex")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.registry()["accounts"]["supabase"]["globex"]["org"], "globex-org")

    def test_organization_names_never_drive_the_terminal(self):
        # KEY-82: an organization's name is whatever its owner typed, escape sequences included
        os.environ["BOTH_TOKEN"] = BOTH
        with mock.patch.dict(ORG_NAMES, {"acme-org": "Acme\x1b]0;owned\x07", "globex-org": "Globex\x1b[2J"}):
            code, out, err = self.add("--token", "BOTH_TOKEN")
            self.assertIn(r"(Acme\x1b]0;owned\x07 (acme-org), Globex\x1b[2J (globex-org))", err)
            code, out, err = self.add("--token", "BOTH_TOKEN", "--org", "globex-org")
        self.assertEqual(code, 0, err)
        self.assertIn(r"→ organization Globex\x1b[2J (globex-org) via $BOTH_TOKEN", out)
        self.assertNotIn("\x1b", out + err)
        self.assertEqual(self.registry()["accounts"]["supabase"]["globex-2j"]["org_name"], "Globex\x1b[2J")  # readable

    def test_linked_folder_decides_the_organization(self):
        os.environ["BOTH_TOKEN"] = BOTH
        self.link(self.repos["client"], GLOBEX_REF)
        code, out, err = self.add("--token", "BOTH_TOKEN")
        self.assertEqual(code, 0, err)
        self.assertIn("organization Globex (globex-org)", out)

    def test_folder_linked_to_a_preview_branch_falls_back_to_the_tokens_organization(self):
        self.link(self.repos["client"], BRANCH_REF)
        code, out, err = self.add("--token", "env:GLOBEX_SUPABASE_TOKEN")
        self.assertEqual(code, 0, err)
        self.assertIn("organization Globex (globex-org)", out)

    def test_copy_of_the_clis_login(self):
        os.remove(self.stub / "keychain.json")
        code, out, err = self.add("--cli")
        self.assertEqual(code, 1)
        self.assertIn("could not copy the supabase CLI's login: the supabase CLI is not signed in", err)
        self.keychain({"Supabase CLI/supabase": "go-keyring-base64:" + base64.b64encode(TOKEN.encode()).decode()})
        code, out, err = self.add("--cli")
        self.assertEqual(code, 0, err)
        self.assertIn("via Keychain item keyfit/supabase-acme, a copy of the supabase CLI's login (Keychain item "
                      "Supabase CLI/supabase)", out)
        self.assertEqual(json.loads((self.stub / "keychain.json").read_text())["keyfit/supabase-acme"], TOKEN)

    def test_guided_init_offers_the_clis_login_and_reads_it_only_after_yes(self):
        self.keychain({"Supabase CLI/supabase": TOKEN})
        with mock.patch.object(cli, "_ask", return_value="n"), contextlib.redirect_stdout(io.StringIO()) as out:
            cli._offer_supabase("client", {"registry": None, "project": "client"})
        self.assertIn("Supabase: the supabase CLI is signed in (Keychain item Supabase CLI/supabase)", out.getvalue())
        self.assertFalse(any("-w" in argv and "Supabase CLI" in argv for argv in self.security_calls()))
        with mock.patch.object(cli, "_ask", return_value="y"), contextlib.redirect_stdout(io.StringIO()) as out:
            cli._offer_supabase("client", {"registry": None, "project": "client"})
        self.assertIn("client: supabase account 'acme' → organization Acme (acme-org)", out.getvalue())

    def test_status_line_for_supabase(self):
        self.add("--token", "env:GLOBEX_SUPABASE_TOKEN")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            main(["status"])
        self.assertIn("✔ supabase   Globex   token and organization confirmed", out.getvalue())
        self.assertNotIn("supabase won't start", out.getvalue())


class CacheKeyTests(unittest.TestCase):
    def test_project_confirmations_do_not_share_the_check_key(self):
        conn = {"token": "env:X", "org": "acme-org"}
        self.assertNotEqual(cache.key("p", "supabase", conn, "f"),
                            cache.key("p", "supabase-project", {"ref": ACME_REF, "org": "acme-org"}, "f"))


if __name__ == "__main__":
    unittest.main()
