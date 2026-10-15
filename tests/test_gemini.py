"""Gemini CLI: the adapter, its MCP allowlist, `keyfit add gemini` and the gemini shim, with a stand-in gemini (it prints
what it was started with, and its sign-in writes a Google sign-in for the email it is told) and stand-ins for Google's
tokeninfo and Gemini API. Gemini CLI's real folder (~/.gemini), the Keychain and the network are never touched: every
home is a temporary folder, and the shims run with HTTPS_PROXY on a closed local port."""

import base64
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

from keyfit import cli, mcp, secrets  # noqa: E402
from keyfit.adapters import ADAPTERS, gemini  # noqa: E402
from keyfit.adapters.gemini import GeminiAdapter  # noqa: E402
from keyfit.cli import main  # noqa: E402
from keyfit.registry import Project, Registry  # noqa: E402
from keyfit.shim import Refused, adjust_args, is_login_command, sign_in_command  # noqa: E402
from test_vercel import executable  # noqa: E402

# Fake people; never real ones.
ACME, PERSONAL = "me@acme.io", "someone@gmail.com"
KEY = "AIzaFakeFakeFakeFakeFakeFakeFakeFake123"  # shaped like a Google API key; not one
LINEAR = {"httpUrl": "https://mcp.linear.app/mcp", "headers": {"Authorization": "Bearer ${LINEAR_API_KEY}"}}

# Fake gemini: prints its argv and the variables that decide its sign-in. Its sign-in (`--prompt-interactive /quit`)
# signs in as FAKE_SIGN_IN_AS in the home it was given, as Gemini CLI's own dialog would.
FAKE_GEMINI = """#!/usr/bin/env python3
import base64, json, os, sys, time
args = sys.argv[1:]
home = os.environ.get("GEMINI_CLI_HOME") or os.environ["HOME"]
if args[-2:] == ["--prompt-interactive", "/quit"] and os.environ.get("FAKE_SIGN_IN_AS"):
    os.makedirs(os.path.join(home, ".gemini"), exist_ok=True)
    claims = base64.urlsafe_b64encode(json.dumps({"email": os.environ["FAKE_SIGN_IN_AS"]}).encode()).decode().rstrip("=")
    with open(os.path.join(home, ".gemini", "oauth_creds.json"), "w") as fh:
        json.dump({"access_token": "ya29.fake", "refresh_token": "1//fake", "id_token": "e30." + claims + ".sig",
                   "expiry_date": 1000}, fh)
    print("fake sign-in done", file=sys.stderr)
    sys.exit(0)
print(json.dumps({"argv": args, "env": {k: os.environ.get(k) for k in (
    "GEMINI_CLI_HOME", "GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_CLOUD_PROJECT", "GOOGLE_APPLICATION_CREDENTIALS",
    "GOOGLE_GENAI_USE_VERTEXAI", "GEMINI_DEFAULT_AUTH_TYPE", "GEMINI_FORCE_ENCRYPTED_FILE_STORAGE", "GOOGLE_MAPS_KEY",
    "KEYFIT_GEMINI", "KEYFIT_PROJECT")}}))
"""


def id_token(claims: dict) -> str:
    part = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"e30.{part}.sig"


def sign_in(home: Path, email: str | None, valid: bool = False, noted: str | None = None, **extra) -> Path:
    """What Gemini CLI leaves in a home after a Google sign-in: oauth_creds.json and google_accounts.json."""
    folder = home / ".gemini"
    folder.mkdir(parents=True, exist_ok=True)
    creds = {"access_token": f"ya29.{email}", "refresh_token": "1//fake", "token_type": "Bearer",
             "expiry_date": int((time.time() + (3000 if valid else -3000)) * 1000), **extra}
    if email:
        creds["id_token"] = id_token({"email": email, "iss": "https://accounts.google.com"})
    (folder / "oauth_creds.json").write_text(json.dumps(creds))
    if noted:
        (folder / "google_accounts.json").write_text(json.dumps({"active": noted, "old": []}))
    return folder / "oauth_creds.json"


def settings(home: Path, data: dict) -> None:
    (home / ".gemini").mkdir(parents=True, exist_ok=True)
    (home / ".gemini" / "settings.json").write_text(json.dumps(data))


def tokeninfo(url, token, timeout):
    """Stands in for oauth2.googleapis.com/tokeninfo: the token names its email (ya29.EMAIL)."""
    if url != gemini.TOKENINFO or not token.startswith("ya29.") or token == "ya29.revoked":
        return 400, {"error": "invalid_token"}
    return 200, {"email": token[len("ya29."):], "expires_in": "3000"}


def models(key, timeout):
    """Stands in for the Gemini API's model list."""
    return {KEY: (200, {"models": [{"name": "models/gemini-x"}]}), "AIzaRestricted": (403, {}),
            "AIzaBusy": (503, {})}.get(key, (400, {"error": {"status": "INVALID_ARGUMENT"}}))


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.own = self.tmp / "keyfit" / "gemini" / "work"
        self.own.mkdir(parents=True)
        self.adapter = GeminiAdapter(fetch=tokeninfo, models=models)
        self.conn = {"name": "work", "home": str(self.own), "account": ACME}
        self.system = mock.patch.object(gemini, "SYSTEM", self.tmp / "no-system")  # never this Mac's settings
        self.system.start()
        self.addCleanup(self.system.stop)

    def env(self, **extra):
        return {"HOME": str(self.home), **extra}

    def verify(self, conn=None, remote=True, **extra):
        conn = conn or self.conn
        env = self.env(**extra)
        self.adapter.inject(conn, env)
        return self.adapter.verify(conn, env) if remote else self.adapter.local(conn, env)

    def test_inject_points_gemini_cli_at_the_accounts_home(self):
        env = self.env(GEMINI_CLI_HOME="/elsewhere", GEMINI_API_KEY="inherited", GOOGLE_API_KEY="kept")
        self.adapter.inject(self.conn, env)
        self.assertEqual((env["GEMINI_CLI_HOME"], env["KEYFIT_GEMINI"]), (str(self.own), "work"))
        # other agents' sessions keep the shell's keys; only where Gemini CLI starts are they removed (scrub)
        self.assertEqual((env["GEMINI_API_KEY"], env["GOOGLE_API_KEY"]), ("inherited", "kept"))
        env = self.env(GEMINI_CLI_HOME="/elsewhere")
        self.adapter.inject({"name": "personal", "account": PERSONAL}, env)
        self.assertNotIn("GEMINI_CLI_HOME", env)  # your own home: no variable

    def test_a_key_account_gets_its_key(self):
        conn = {"name": "acme", "home": str(self.own), "key": "env:ACME_GEMINI_KEY"}
        env = self.env(ACME_GEMINI_KEY=KEY, GEMINI_API_KEY="inherited")
        self.adapter.inject(conn, env)
        self.assertEqual((env["GEMINI_API_KEY"], env["KEYFIT_GEMINI_KEY"]), (KEY, "1"))
        self.adapter.inject(self.conn, env)  # a Google account in a session that had another project's key
        self.assertNotIn("GEMINI_API_KEY", env)

    def test_forget_removes_only_what_keyfit_set(self):
        env = self.env(GEMINI_CLI_HOME="/mine", GEMINI_API_KEY="mine")
        self.adapter.forget(env)
        self.assertEqual((env["GEMINI_CLI_HOME"], env["GEMINI_API_KEY"]), ("/mine", "mine"))
        env = self.env(ACME_GEMINI_KEY=KEY)
        self.adapter.inject({"name": "acme", "home": str(self.own), "key": "env:ACME_GEMINI_KEY"}, env)
        self.adapter.forget(env)
        self.assertFalse({"GEMINI_CLI_HOME", "GEMINI_API_KEY", "KEYFIT_GEMINI", "KEYFIT_GEMINI_KEY"} & set(env))

    def test_scrub_removes_what_would_pick_another_sign_in(self):
        inherited = {var: "x" for var in gemini.SCRUB}
        env = self.env(GEMINI_API_KEY="other", GOOGLE_EXTERNAL_ACCOUNT_AUDIENCE="x", GOOGLE_MAPS_KEY="kept", **inherited)
        gemini.scrub(self.conn, env)
        self.assertEqual(sorted(k for k in env if k.startswith(("GOOGLE_", "GEMINI_"))),
                         ["GEMINI_DEFAULT_AUTH_TYPE", "GOOGLE_MAPS_KEY"])
        self.assertEqual(env["GEMINI_DEFAULT_AUTH_TYPE"], "oauth-personal")  # Sign in with Google, preselected
        env = self.env(GEMINI_API_KEY=KEY, GOOGLE_CLOUD_PROJECT="other-project")
        gemini.scrub({"key": "env:X", "cloud_project": "acme-ai"}, env)
        self.assertEqual((env["GEMINI_API_KEY"], env["GOOGLE_CLOUD_PROJECT"], env["GEMINI_DEFAULT_AUTH_TYPE"]),
                         (KEY, "acme-ai", "gemini-api-key"))

    def test_confirmed_by_google_while_the_token_is_valid(self):
        sign_in(self.own, ACME, valid=True)
        check = self.verify()
        self.assertEqual((check.status, check.label), ("ok", ACME))
        self.assertEqual(check.detail, f"Google sign-in in {self.own} · confirmed by Google")

    def test_an_expired_token_is_not_renewed_and_the_local_check_decides(self):
        sign_in(self.own, ACME)
        adapter = GeminiAdapter(fetch=lambda *a: self.fail("an expired token is never sent"), models=models)
        check = adapter.verify(self.conn, self.env())
        self.assertEqual(check.status, "ok")
        self.assertIn("not confirmed online (the token has expired; Gemini CLI renews it)", check.detail)

    def test_another_account_blocks_with_the_fix(self):
        creds = sign_in(self.own, PERSONAL, valid=True)
        check = self.verify()
        self.assertTrue(check.failed)
        self.assertEqual(check.detail, f"Gemini CLI is signed in as {PERSONAL}, the project's Google account is {ACME}")
        self.assertEqual(check.fix, f"rm {creds}, then: GEMINI_CLI_HOME={self.own} gemini --prompt-interactive /quit")
        self.assertIsNone(sign_in_command(check.fix))  # removing a sign-in is printed, never run
        # in your own home, Keyfit never suggests removing your sign-in: the project gets a home of its own
        sign_in(self.home, PERSONAL)
        check = self.verify({"name": "work", "account": ACME})
        self.assertEqual(check.fix, "keyfit add gemini --name work --new   (a Gemini CLI folder of the project's own; "
                                    "sign in there)")

    def test_case_does_not_matter(self):
        sign_in(self.own, "Me@Acme.io")
        self.assertEqual(self.verify(remote=False).status, "ok")

    def test_not_signed_in_offers_gemini_clis_own_sign_in(self):
        check = self.verify()
        self.assertTrue(check.failed)
        self.assertEqual(check.detail, f"not signed in with Google in {self.own / '.gemini'}")
        self.assertEqual(check.fix, f"GEMINI_CLI_HOME={self.own} gemini --prompt-interactive /quit")
        self.assertEqual(sign_in_command(check.fix),
                         ({"GEMINI_CLI_HOME": str(self.own)}, ["gemini", "--prompt-interactive", "/quit"]))
        check = self.verify({"name": "personal", "account": ACME})
        self.assertEqual(check.fix, "gemini --prompt-interactive /quit")

    def test_the_email_gemini_cli_noted_when_there_is_no_id_token(self):
        sign_in(self.own, None, noted=ACME)
        self.assertEqual(self.verify(remote=False).status, "ok")
        (self.own / ".gemini" / "google_accounts.json").unlink()
        check = self.verify(remote=False)
        self.assertTrue(check.failed)
        self.assertIn("cannot tell which Google account", check.detail)

    def test_other_credentials_are_not_a_google_sign_in(self):
        sign_in(self.own, ACME, type="service_account")
        check = self.verify(remote=False)
        self.assertTrue(check.failed)
        self.assertIn("holds service_account credentials, not a Google sign-in", check.detail)

    def test_another_sign_in_method_blocks(self):
        sign_in(self.own, ACME)
        settings(self.own, {"security": {"auth": {"selectedType": "gemini-api-key"}}})
        check = self.verify(remote=False)
        self.assertTrue(check.failed)
        self.assertEqual(check.detail, f"Gemini CLI is set to a Gemini API key in {self.own / '.gemini/settings.json'}, "
                                       "not Sign in with Google")
        self.assertIn('set "security": {"auth": {"selectedType": "oauth-personal"}}', check.fix)
        # the system's settings come last and win; comments are allowed, as Gemini CLI allows them
        settings(self.own, {"security": {"auth": {"selectedType": "oauth-personal"}}})
        system = self.tmp / "system.json"
        system.write_text('{ // admin\n "security": {"auth": {"selectedType": "vertex-ai"}}}')
        check = self.verify(remote=False, GEMINI_CLI_SYSTEM_SETTINGS_PATH=str(system))
        self.assertIn("is set to Vertex AI", check.detail)

    def test_missing_home_and_missing_google_account(self):
        check = self.verify({"name": "gone", "home": str(self.tmp / "gone"), "account": ACME})
        self.assertEqual((check.detail, check.fix), (f"Gemini CLI folder {self.tmp / 'gone'} does not exist",
                                                     "keyfit add gemini --name gone --new"))
        check = self.verify({"name": "work", "home": str(self.own), "account": None})
        self.assertEqual((check.detail, check.fix), ("the project has no Google account",
                                                     "keyfit add google --email <the project's Google account>"))

    def test_revoked_sign_in(self):
        sign_in(self.own, "revoked", valid=True)
        conn = dict(self.conn, account="revoked@acme.io")
        (self.own / ".gemini" / "oauth_creds.json").write_text(json.dumps({
            "access_token": "ya29.revoked", "id_token": id_token({"email": "revoked@acme.io"}),
            "expiry_date": int((time.time() + 3000) * 1000)}))
        check = self.verify(conn)
        self.assertTrue(check.failed)
        self.assertEqual(check.detail, "Google does not accept the sign-in's token (signed out or revoked)")
        # Gemini CLI signs in afresh when its sign-in no longer works: the sign-in alone fixes it, and is offered
        self.assertEqual(sign_in_command(check.fix)[1], ["gemini", "--prompt-interactive", "/quit"])

    def test_google_not_answering_is_transient(self):
        sign_in(self.own, ACME, valid=True)
        adapter = GeminiAdapter(fetch=mock.Mock(side_effect=OSError("down")), models=models)
        check = adapter.verify(self.conn, self.env())
        self.assertTrue(check.failed and check.transient)

    def test_key_accounts(self):
        conn = {"name": "acme", "home": str(self.own), "key": "env:ACME_GEMINI_KEY"}
        settings(self.own, {"security": {"auth": {"selectedType": "gemini-api-key"}}})
        check = self.verify(conn, ACME_GEMINI_KEY=KEY)
        self.assertEqual((check.status, check.label, check.detail), ("ok", "acme · API key", "key accepted by Google"))
        check = self.verify(conn, ACME_GEMINI_KEY="AIzaWrong")
        self.assertEqual((check.status, check.detail), ("fail", "Google does not accept the API key (invalid, expired "
                                                                "or deleted)"))
        self.assertIn("may not use the Gemini API", self.verify(conn, ACME_GEMINI_KEY="AIzaRestricted").detail)
        self.assertTrue(self.verify(conn, ACME_GEMINI_KEY="AIzaBusy").transient)
        check = self.verify(conn)
        self.assertEqual((check.detail, check.fix), ("the API key ($ACME_GEMINI_KEY) is missing", "keyfit add gemini --key"))

    def test_a_key_account_needs_the_api_key_method(self):
        conn = {"name": "acme", "home": str(self.own), "key": "env:ACME_GEMINI_KEY"}
        check = self.verify(conn, ACME_GEMINI_KEY=KEY)  # nothing chosen: Gemini CLI would ask, and store the key itself
        self.assertTrue(check.failed)
        self.assertIn("has no sign-in method chosen", check.detail)
        settings(self.own, {"security": {"auth": {"selectedType": "oauth-personal"}}})
        self.assertIn("is set to Sign in with Google", self.verify(conn, ACME_GEMINI_KEY=KEY).detail)

    def test_fingerprint_follows_the_sign_in_and_the_key(self):
        sign_in(self.own, ACME)
        before = self.adapter.fingerprint(self.conn, self.env())
        time.sleep(0.01)
        sign_in(self.own, ACME, valid=True)
        self.assertNotEqual(self.adapter.fingerprint(self.conn, self.env()), before)
        conn = {"name": "acme", "home": str(self.own), "key": "env:X"}
        self.assertNotEqual(self.adapter.fingerprint(conn, self.env(GEMINI_API_KEY="a")),
                            self.adapter.fingerprint(conn, self.env(GEMINI_API_KEY="b")))

    def test_no_token_in_any_check(self):
        sign_in(self.own, PERSONAL, valid=True)
        check = self.verify()
        self.assertNotIn("ya29", check.detail + check.fix)


class McpTests(unittest.TestCase):
    """Which servers a Gemini CLI session of a project keeps, from Gemini CLI's settings files and extensions."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.home = self.tmp / "gh"
        self.cwd = self.tmp / "repo"
        self.cwd.mkdir()
        (self.home / ".gemini").mkdir(parents=True)
        (self.home / ".gemini" / "settings.json").write_text(
            '{ // yours\n "mcpServers": {"globex_cloud_eu": {"command": "globex-mcp"}, "paper": {"url": "http://p"},'
            ' "linear": ' + json.dumps(LINEAR) + "}}")
        self.env = {"HOME": str(self.tmp / "home"), "GEMINI_CLI_HOME": str(self.home)}
        self.owners = {"globex": ["globex_cloud_*"]}
        self.project = Project("acme", ["~/acme"], {"linear": {"workspace": "acme", "key": "env:LINEAR_ACME_API_KEY"}})
        self.system = mock.patch.object(gemini, "SYSTEM", self.tmp / "no-system")
        self.system.start()
        self.addCleanup(self.system.stop)

    def hidden(self, project=None, env=None):
        return mcp.gemini_hidden(project or self.project, env or self.env, self.cwd, self.owners)

    def test_other_projects_servers_are_left_out_of_the_allowlist(self):
        self.assertEqual(self.hidden(), (["globex_cloud_eu"], ["paper", "linear"]))

    def test_the_owner_keeps_its_servers(self):
        globex = Project("globex", ["~/globex"], {}, ["globex_cloud_*"])
        hide, kept = self.hidden(globex)
        self.assertEqual(hide, ["linear"])  # a Linear server reading LINEAR_API_KEY: globex has no Linear key
        self.assertEqual(kept, ["globex_cloud_eu", "paper"])

    def test_the_folders_servers_count_unverified(self):
        (self.cwd / ".gemini").mkdir()
        (self.cwd / ".gemini" / "settings.json").write_text(json.dumps({"mcpServers": {
            "team_linear": LINEAR, "globex_cloud_us": {"command": "x"}, "local": {"command": "y"}}}))
        hide, kept = self.hidden()
        self.assertEqual(hide, ["globex_cloud_eu", "globex_cloud_us", "team_linear"])
        self.assertEqual(kept, ["paper", "linear", "local"])

    def test_extensions_and_the_system_settings_count(self):
        extension = self.home / ".gemini" / "extensions" / "globex-tools"
        extension.mkdir(parents=True)
        (extension / "gemini-extension.json").write_text(json.dumps(
            {"name": "globex-tools", "mcpServers": {"globex_cloud_ext": {"command": "x"}}}))
        system = self.tmp / "system.json"
        system.write_text(json.dumps({"mcpServers": {"company": {"command": "x"}}}))
        hide, kept = self.hidden(env=dict(self.env, GEMINI_CLI_SYSTEM_SETTINGS_PATH=str(system)))
        self.assertEqual(hide, ["globex_cloud_eu", "globex_cloud_ext"])
        self.assertIn("company", kept)

    def test_your_own_allowed_and_excluded_settings_stay(self):
        (self.home / ".gemini" / "settings.json").write_text(json.dumps({
            "mcpServers": {"globex_cloud_eu": {"command": "x"}, "paper": {"url": "http://p"}, "noisy": {"command": "y"},
                           "extra": {"command": "z"}},
            "mcp": {"excluded": ["noisy"], "allowed": ["Paper", "noisy", "globex_cloud_eu"]}}))
        self.assertEqual(self.hidden(), (["globex_cloud_eu"], ["paper"]))

    def test_nothing_to_hide(self):
        (self.home / ".gemini" / "settings.json").write_text(json.dumps({"mcpServers": {"paper": {"url": "http://p"}}}))
        self.assertEqual(self.hidden(), ([], []))

    def test_unreadable_files_hide_nothing_from_them(self):
        (self.home / ".gemini" / "settings.json").write_text("{not json")
        self.assertEqual(self.hidden(), ([], []))

    def test_the_allowlist_on_the_command_line(self):
        self.assertEqual(mcp.gemini_allowed(["--allowed-mcp-server-names", "a,b", "--allowed-mcp-server-names=c",
                                             "--allowedMcpServerNames", "d", "--", "--allowed-mcp-server-names", "e"]),
                         ["a", "b", "c", "d"])


class ShimArgsTests(McpTests):
    def adjust(self, *args, project=None, env=None):
        env = dict(env or self.env)
        return adjust_args("gemini", project or self.project, list(args), env, self.cwd, self.owners), env

    def test_sessions_get_the_allowlist(self):
        for args in ([], ["-p", "hi"], ["explain this"], ["-m", "gemini-x"]):
            self.assertEqual(self.adjust(*args)[0], ["--allowed-mcp-server-names=paper,linear", *args])

    def test_gemini_clis_own_commands_run_as_typed(self):
        for args in (["mcp", "list"], ["extensions", "install", "x"], ["skills", "list"], ["hooks", "migrate"]):
            self.assertEqual(self.adjust(*args)[0], args)

    def test_when_nothing_may_stay_the_allowlist_names_no_server(self):
        (self.home / ".gemini" / "settings.json").write_text(json.dumps({"mcpServers": {"globex_cloud_eu": {}}}))
        self.assertEqual(self.adjust()[0], ["--allowed-mcp-server-names=keyfit-none"])

    def test_an_allowlist_of_your_own(self):
        self.assertEqual(self.adjust("--allowed-mcp-server-names", "paper")[0], ["--allowed-mcp-server-names", "paper"])
        with self.assertRaises(Refused) as refused:
            self.adjust("--allowed-mcp-server-names=paper,globex_cloud_eu")
        self.assertIn("refusing --allowed-mcp-server-names globex_cloud_eu: that MCP server belongs to another project",
                      str(refused.exception))

    def test_with_an_account_the_environment_is_scrubbed(self):
        project = Project("acme", ["~/acme"], {"gemini": {"name": "work", "account": ACME}})
        _, env = self.adjust(project=project, env=dict(self.env, GEMINI_API_KEY="x", GOOGLE_CLOUD_PROJECT="y"))
        self.assertEqual((env.get("GEMINI_API_KEY"), env.get("GOOGLE_CLOUD_PROJECT")), (None, None))
        _, env = self.adjust(env=dict(self.env, GEMINI_API_KEY="x"))  # no Gemini CLI account: yours, unchanged
        self.assertEqual(env["GEMINI_API_KEY"], "x")

    def test_the_sign_in_command(self):
        for args in (["--prompt-interactive", "/quit"], ["-i", "/quit"], ["-i=/quit"]):
            self.assertTrue(is_login_command("gemini", args), args)
        for args in ([], ["/quit"], ["-p", "/quit"], ["-i", "/quit", "-y"], ["--prompt-interactive", "hi"]):
            self.assertFalse(is_login_command("gemini", args), args)


class ShimTests(unittest.TestCase):
    """The gemini shim written by `keyfit setup`, run as a real process in temporary folders: project demo uses the
    Google account me@acme.io in a home of its own (work), globex owns globex_cloud_*."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        executable(self.bin / "gemini", FAKE_GEMINI)
        executable(self.bin / "claude", "#!/bin/sh\nenv | grep -E '^(GEMINI_CLI_HOME|GEMINI_API_KEY)=' | sort\n")
        self.config = self.tmp / "keyfit"
        self.work = self.config / "gemini" / "work"
        self.work.mkdir(parents=True)
        servers = {"globex_cloud_eu": {"command": "globex-mcp"}, "paper": {"url": "http://p"}}
        for home in (self.work, self.home):
            settings(home, {"mcpServers": servers})
        self.repo, self.other, self.outside = self.tmp / "repo", self.tmp / "other", self.tmp / "outside"
        for folder in (self.repo, self.other, self.outside):
            folder.mkdir()
        for folder in (self.repo, self.other):
            subprocess.run(["git", "init", "-q"], cwd=folder, check=True)
        with mock.patch.dict(os.environ, {"KEYFIT_CONFIG_DIR": str(self.config)}):
            with contextlib.redirect_stdout(io.StringIO()):
                main(["init", "--yes", "--root", str(self.repo), "--name", "demo"])
                main(["init", "--yes", "--root", str(self.other), "--name", "globex"])
                with mock.patch.object(sys, "platform", "darwin"):
                    main(["setup"])
        reg = self.config / "registry.toml"
        text = reg.read_text().replace(
            '[projects.demo]\nroots = ["' + str(self.repo) + '"]\n',
            '[projects.demo]\nroots = ["' + str(self.repo) + '"]\ngoogle = "work"\ngemini = "work"\n')
        text = text.replace('[projects.globex]\nroots = ["' + str(self.other) + '"]\n',
                            '[projects.globex]\nroots = ["' + str(self.other) + '"]\nmcp_servers = ["globex_cloud_*"]\n')
        reg.write_text(text + f'\n[accounts.google.work]\nemail = "{ACME}"\n\n'
                              f'[accounts.gemini.work]\nhome = "{self.work}"\n')

    def env(self, **extra):
        return {"PATH": f"{self.config / 'bin'}{os.pathsep}{self.bin}{os.pathsep}/usr/bin:/bin",
                "HOME": str(self.home), "KEYFIT_CONFIG_DIR": str(self.config),
                "GEMINI_CLI_SYSTEM_SETTINGS_PATH": str(self.tmp / "no-system.json"),
                "GEMINI_CLI_SYSTEM_DEFAULTS_PATH": str(self.tmp / "no-defaults.json"),
                "HTTPS_PROXY": "http://127.0.0.1:9", **extra}  # should a check ever go online: a closed port

    def run_tool(self, *args, tool="gemini", cwd=None, **extra):
        return subprocess.run([tool, *args], cwd=cwd or self.repo, env=self.env(**extra), capture_output=True,
                              text=True, timeout=60)

    def started(self, out):
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout.splitlines()[-1])

    def test_session_starts_as_the_projects_google_account_with_its_mcp_servers(self):
        sign_in(self.work, ACME)  # an expired token: no online question at all
        seen = self.started(self.run_tool("-p", "hi", GEMINI_API_KEY="AIzaOther", GOOGLE_API_KEY="x",
                                          GOOGLE_CLOUD_PROJECT="other", GOOGLE_GENAI_USE_VERTEXAI="true",
                                          GOOGLE_APPLICATION_CREDENTIALS="/keys/x.json", GOOGLE_MAPS_KEY="kept",
                                          GEMINI_FORCE_ENCRYPTED_FILE_STORAGE="true"))
        self.assertEqual(seen["argv"], ["--allowed-mcp-server-names=paper", "-p", "hi"])
        self.assertEqual(seen["env"], {
            "GEMINI_CLI_HOME": str(self.work), "GEMINI_API_KEY": None, "GOOGLE_API_KEY": None, "GOOGLE_CLOUD_PROJECT": None,
            "GOOGLE_APPLICATION_CREDENTIALS": None, "GOOGLE_GENAI_USE_VERTEXAI": None,
            "GEMINI_DEFAULT_AUTH_TYPE": "oauth-personal", "GEMINI_FORCE_ENCRYPTED_FILE_STORAGE": None,
            "GOOGLE_MAPS_KEY": "kept", "KEYFIT_GEMINI": "work", "KEYFIT_PROJECT": "demo"})

    def test_a_mismatch_blocks_with_the_fix(self):
        creds = sign_in(self.work, PERSONAL)
        out = self.run_tool()
        self.assertEqual((out.returncode, out.stdout), (1, ""))
        self.assertIn(f"keyfit: gemini not started, demo needs attention\n"
                      f"  ✘ gemini     {ACME}   Gemini CLI is signed in as {PERSONAL}, the project's Google account is "
                      f"{ACME}\n    → rm {creds}, then: GEMINI_CLI_HOME={self.work} gemini --prompt-interactive /quit\n",
                      out.stderr)
        self.assertNotIn("Sign in now?", out.stderr)

    def test_not_signed_in_blocks_and_nothing_is_asked_without_a_terminal(self):
        out = self.run_tool("-p", "hi")
        self.assertEqual((out.returncode, out.stdout), (1, ""))
        self.assertIn(f"not signed in with Google in {self.work / '.gemini'}\n"
                      f"    → GEMINI_CLI_HOME={self.work} gemini --prompt-interactive /quit\n", out.stderr)
        self.assertNotIn("Sign in now?", out.stderr)

    def test_unregistered_folder_runs_unchanged(self):
        seen = self.started(self.run_tool("-p", "hi", cwd=self.outside, GEMINI_API_KEY="mine", GOOGLE_API_KEY="mine"))
        self.assertEqual(seen["argv"], ["-p", "hi"])
        self.assertEqual((seen["env"]["GEMINI_CLI_HOME"], seen["env"]["GEMINI_API_KEY"], seen["env"]["GOOGLE_API_KEY"]),
                         (None, "mine", "mine"))

    def test_the_sign_in_runs_unchecked_in_the_projects_home_without_other_projects_servers(self):
        out = self.run_tool("--prompt-interactive", "/quit", FAKE_SIGN_IN_AS=ACME)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("fake sign-in done", out.stderr)
        self.assertTrue((self.work / ".gemini" / "oauth_creds.json").exists())
        self.assertFalse((self.home / ".gemini" / "oauth_creds.json").exists())
        self.assertEqual(self.started(self.run_tool())["argv"], ["--allowed-mcp-server-names=paper"])

    def test_sign_in_now_in_a_terminal(self):
        import pty
        import select

        master, slave = pty.openpty()
        proc = subprocess.Popen(["gemini"], cwd=self.repo, env=self.env(FAKE_SIGN_IN_AS=ACME), stdin=slave,
                                stdout=subprocess.PIPE, stderr=slave)
        os.close(slave)
        seen, sent = b"", False
        while select.select([master], [], [], 30)[0]:
            try:
                chunk = os.read(master, 4096)
            except OSError:
                break
            if not chunk:
                break
            seen += chunk
            if not sent and b"Sign in now? [Y/n] " in seen:
                os.write(master, b"\n")
                sent = True
        proc.wait(timeout=30)
        os.close(master)
        with proc.stdout:
            out, terminal = proc.stdout.read().decode(), seen.decode().replace("\r\n", "\n")
        self.assertEqual(proc.returncode, 0, terminal)
        self.assertIn("fake sign-in done\nkeyfit: signed in, starting gemini\n", terminal)
        self.assertEqual(json.loads(out.splitlines()[-1])["argv"], ["--allowed-mcp-server-names=paper"])

    def test_sign_in_now_as_the_wrong_account_stops_there(self):
        import pty
        import select

        master, slave = pty.openpty()
        proc = subprocess.Popen(["gemini"], cwd=self.repo, env=self.env(FAKE_SIGN_IN_AS=PERSONAL), stdin=slave,
                                stdout=subprocess.PIPE, stderr=slave)
        os.close(slave)
        seen = b""
        while select.select([master], [], [], 30)[0]:
            try:
                chunk = os.read(master, 4096)
            except OSError:
                break
            if not chunk:
                break
            seen += chunk
            if b"Sign in now? [Y/n] " in seen and b"\n" not in seen.rsplit(b"Sign in now?", 1)[1]:
                os.write(master, b"\n")
        proc.wait(timeout=30)
        os.close(master)
        proc.stdout.close()
        terminal = seen.decode().replace("\r\n", "\n")
        self.assertEqual(proc.returncode, 1, terminal)
        self.assertIn("gemini not started, demo still needs attention", terminal)
        self.assertIn(f"Gemini CLI is signed in as {PERSONAL}", terminal)
        self.assertEqual(terminal.count("Sign in now?"), 1)

    def test_other_agents_sessions_get_the_projects_home(self):
        sign_in(self.work, ACME)
        out = self.run_tool("-p", "hi", tool="claude", GEMINI_API_KEY="kept")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout, f"GEMINI_API_KEY=kept\nGEMINI_CLI_HOME={self.work}\n")

    def test_another_agents_session_is_checked_too(self):
        sign_in(self.work, PERSONAL)
        out = self.run_tool("-p", "hi", tool="claude")
        self.assertEqual(out.returncode, 1)
        self.assertIn("keyfit: claude not started, demo needs attention", out.stderr)

    def test_project_without_a_gemini_account_forgets_another_projects(self):
        seen = self.started(self.run_tool(cwd=self.other, GEMINI_CLI_HOME=str(self.work), KEYFIT_GEMINI="work"))
        self.assertEqual(seen["argv"], [])  # globex owns its server: nothing to hide
        self.assertIsNone(seen["env"]["GEMINI_CLI_HOME"])

    def test_dry_run_names_the_variables_only(self):
        sign_in(self.work, ACME)
        out = self.run_tool("-p", "hi", KEYFIT_DRY_RUN="1", GOOGLE_API_KEY="AIzaSecret")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("GEMINI_CLI_HOME", out.stderr)
        self.assertNotIn("AIzaSecret", out.stderr + out.stdout)


class AddTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.home = self.tmp / "home"
        (self.home / ".gemini" / "commands").mkdir(parents=True)
        (self.home / ".gemini" / "GEMINI.md").write_text("mine\n")
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.repo = self.tmp / "acme"
        self.repo.mkdir()
        self.config = self.tmp / "keyfit"
        self.stored = {}
        for patcher in (mock.patch.dict(os.environ, {"HOME": str(self.home), "KEYFIT_CONFIG_DIR": str(self.config),
                                                     "PATH": f"{self.bin}{os.pathsep}/usr/bin:/bin"}, clear=True),
                        mock.patch.object(Path, "home", return_value=self.home),
                        mock.patch.object(gemini, "SYSTEM", self.tmp / "no-system"),
                        mock.patch.object(ADAPTERS["gemini"], "fetch", tokeninfo),
                        mock.patch.object(ADAPTERS["gemini"], "models", models),
                        mock.patch.object(secrets, "SECURITY", "/nonexistent/security"),  # never the real Keychain
                        mock.patch.object(secrets, "store", self.store)):
            patcher.start()
            self.addCleanup(patcher.stop)
        cwd = os.getcwd()
        os.chdir(self.repo)
        self.addCleanup(os.chdir, cwd)
        with contextlib.redirect_stdout(io.StringIO()):
            main(["init", "--yes", "--name", "acme"])

    def store(self, service, account, value):
        self.stored[(service, account)] = value
        return ""

    def run_main(self, *argv, stdin=""):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
                mock.patch.object(sys, "stdin", io.StringIO(stdin)):
            code = main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def registry(self):
        return tomllib.loads((self.config / "registry.toml").read_text())

    def google(self, email=ACME):
        self.assertEqual(self.run_main("add", "google", "--email", email)[0], 0)

    def test_your_own_sign_in_when_it_is_the_projects_account(self):
        self.google()
        sign_in(self.home, ACME)
        code, out, err = self.run_main("add", "gemini")
        self.assertEqual((code, out), (0, "acme: gemini account 'acme' → Gemini CLI in ~/.gemini, signed in as "
                                          f"{ACME}\n"), err)
        reg = self.registry()
        self.assertEqual((reg["projects"]["acme"]["gemini"], reg["accounts"]["gemini"]["acme"]), ("acme", {"home": "~"}))
        self.assertEqual(Registry.load().get("acme").connections["gemini"]["account"], ACME)  # from the Google account

    def test_another_account_in_your_own_home_points_to_a_home_of_its_own(self):
        self.google()
        sign_in(self.home, PERSONAL)
        code, out, err = self.run_main("add", "gemini")
        self.assertEqual((code, out), (1, ""))
        self.assertIn(f"Gemini CLI in ~/.gemini is signed in as {PERSONAL}, and acme's Google account is {ACME}.\n"
                      f"  → a Gemini CLI folder of the project's own: keyfit add gemini --new", err)
        self.assertNotIn("gemini", self.registry()["projects"]["acme"])

    def test_no_google_account_yet(self):
        code, _, err = self.run_main("add", "gemini")
        self.assertEqual(code, 1)
        self.assertIn("acme has no Google account yet, which Gemini CLI's sign-in is checked against.", err)

    def test_a_home_of_its_own(self):
        self.google()
        code, out, err = self.run_main("add", "gemini", "--new")
        home = self.config / "gemini" / "acme"
        self.assertEqual(code, 0, err)
        self.assertIn(f"Created {home} (GEMINI.md, commands from ~/.gemini).\n"
                      f"acme: gemini account 'acme' → Gemini CLI in {home}, signed in as {ACME}\n"
                      f"Sign in there once as {ACME} (Gemini CLI's own dialog, then it quits):\n"
                      f"  GEMINI_CLI_HOME={home} gemini --prompt-interactive /quit", out)
        self.assertEqual(self.registry()["accounts"]["gemini"]["acme"], {"home": str(home)})
        self.assertTrue((home / ".gemini" / "GEMINI.md").is_symlink())
        self.assertFalse((home / ".gemini" / "settings.json").exists())  # Gemini CLI's dialog asks, Google preselected
        self.assertEqual(oct(home.stat().st_mode & 0o777), "0o700")
        # a second project with the same Google account shares that home (one sign-in)
        other = self.tmp / "acme-web"
        other.mkdir()
        with contextlib.redirect_stdout(io.StringIO()):
            main(["init", "--yes", "--root", str(other), "--name", "web"])
        self.run_main("add", "google", "--email", ACME, "--project", "web")
        code, out, _ = self.run_main("add", "gemini", "--new", "--project", "web")
        self.assertIn("gemini account 'acme'", out)
        self.assertEqual(self.registry()["projects"]["web"]["gemini"], "acme")

    def test_a_name_taken_by_another_account_gets_a_number(self):
        self.google()
        reg = Registry.load()
        reg.set_account("gemini", "acme", {"home": "~/elsewhere", "key": "env:X"})
        reg.save()
        code, out, err = self.run_main("add", "gemini", "--new")
        self.assertEqual(code, 0, err)
        self.assertIn("gemini account 'acme-2'", out)
        self.assertEqual(self.registry()["accounts"]["gemini"]["acme"], {"home": "~/elsewhere", "key": "env:X"})

    def test_a_key_from_the_environment(self):
        os.environ["ACME_GEMINI_KEY"] = KEY
        code, out, err = self.run_main("add", "gemini", "--key", "env:ACME_GEMINI_KEY", "--cloud-project", "acme-ai")
        home = self.config / "gemini" / "acme"
        self.assertEqual(code, 0, err)
        self.assertEqual(out, f"Created {home} (GEMINI.md, commands from ~/.gemini).\n"
                              f"acme: gemini account 'acme' → a Gemini API key via $ACME_GEMINI_KEY, Gemini CLI in "
                              f"{home}\n")
        self.assertEqual(self.registry()["accounts"]["gemini"]["acme"],
                         {"home": str(home), "key": "env:ACME_GEMINI_KEY", "cloud_project": "acme-ai"})
        self.assertEqual(json.loads((home / ".gemini" / "settings.json").read_text()),
                         {"security": {"auth": {"selectedType": "gemini-api-key"}}})
        self.assertEqual(self.stored, {})
        self.assertNotIn(KEY, (self.config / "registry.toml").read_text())

    def test_a_pasted_key_is_checked_then_stored(self):
        code, out, err = self.run_main("add", "gemini", "--key", stdin=KEY + "\n")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.stored, {("keyfit", "gemini-acme"): KEY})
        self.assertEqual(self.registry()["accounts"]["gemini"]["acme"]["key"], "keychain:keyfit/gemini-acme")
        self.assertNotIn(KEY, out + err)

    def test_a_key_google_refuses_is_not_stored(self):
        code, out, err = self.run_main("add", "gemini", "--key", stdin="AIzaWrongWrongWrongWrongWrong\n")
        self.assertEqual((code, out), (1, ""))
        self.assertIn("Google does not accept the API key", err)
        self.assertEqual(self.stored, {})
        code, _, err = self.run_main("add", "gemini", "--key", KEY)
        self.assertEqual(code, 1)
        self.assertIn("--key takes where the key lives", err)
        self.assertNotIn(KEY, err)

    def test_init_offers_gemini_after_the_other_google_tools(self):
        kinds = [kind for kind, _, _ in cli.GUIDED]
        self.assertEqual(kinds.index("gemini"), kinds.index("firebase") + 1)
        base = {"registry": None, "project": "acme"}
        with mock.patch.object(cli, "_ask") as ask, contextlib.redirect_stdout(io.StringIO()) as out:
            cli._offer_gemini("acme", base)
        self.assertEqual(out.getvalue(), "Gemini CLI: gemini not found on PATH — skipped\n")
        executable(self.bin / "gemini", "#!/bin/sh\nexit 97\n")
        with mock.patch.object(cli, "_ask") as ask, contextlib.redirect_stdout(io.StringIO()) as out:
            cli._offer_gemini("acme", base)
        self.assertIn("acme's Google account, and it has none yet", out.getvalue())
        ask.assert_not_called()
        self.google()
        sign_in(self.home, PERSONAL)
        with mock.patch.object(cli, "_ask", return_value="y") as ask, contextlib.redirect_stdout(io.StringIO()) as out:
            cli._offer_gemini("acme", base)
        self.assertIn(f"Gemini CLI: ~/.gemini is signed in as {PERSONAL}, not {ACME}.", out.getvalue())
        ask.assert_called_once_with(f"Give acme a Gemini CLI folder of its own (you sign in there as {ACME} once)? [y/N] ")
        self.assertEqual(self.registry()["accounts"]["gemini"]["acme"], {"home": str(self.config / "gemini" / "acme")})
        sign_in(self.home, ACME)
        with mock.patch.object(cli, "_ask", return_value="y") as ask, contextlib.redirect_stdout(io.StringIO()):
            cli._offer_gemini("acme", base)
        ask.assert_called_once_with("Use this Gemini CLI sign-in for acme? [y/N] ")
        self.assertEqual(self.registry()["projects"]["acme"]["gemini"], "acme-2")

    def test_doctor_shows_the_home_and_the_sign_in(self):
        self.google()
        sign_in(self.home, ACME)
        self.run_main("add", "gemini")
        _, out, _ = self.run_main("doctor")
        self.assertIn(f"  ✔ gemini     {ACME}   Google sign-in · home ~/.gemini\n", out)
        self.assertIn("  ! gemini     not found on PATH   needed only if you use it\n"
                      "    → npm install -g @google/gemini-cli\n", out)
        sign_in(self.home, PERSONAL)
        _, out, _ = self.run_main("doctor")
        self.assertIn(f"  ✘ gemini     {ACME}   Gemini CLI is signed in as {PERSONAL}", out)

    def test_doctor_never_reads_a_key(self):
        os.environ["ACME_GEMINI_KEY"] = KEY
        self.run_main("add", "gemini", "--key", "env:ACME_GEMINI_KEY")
        with mock.patch.object(secrets, "resolve", side_effect=AssertionError("doctor read a key")):
            _, out, _ = self.run_main("doctor")
        self.assertIn(f"  ✔ gemini     acme · API key   $ACME_GEMINI_KEY · home {self.config / 'gemini' / 'acme' / '.gemini'}",
                      out)

    def test_a_new_google_account_says_what_gemini_needs_next(self):
        self.google()
        sign_in(self.home, ACME)
        self.run_main("add", "gemini")
        code, out, _ = self.run_main("add", "google", "--email", PERSONAL)
        self.assertEqual(code, 0)
        self.assertIn(f"  next: Gemini CLI is signed in as {ACME}, the project's Google account is {PERSONAL}", out)

    def test_status_checks_it(self):
        self.google()
        sign_in(self.home, ACME, valid=True)
        self.run_main("add", "gemini")
        code, out, _ = self.run_main("status")
        self.assertEqual(code, 0, out)
        self.assertIn(f"  ✔ gemini     {ACME}   Google sign-in · confirmed by Google\n", out)


if __name__ == "__main__":
    unittest.main()
