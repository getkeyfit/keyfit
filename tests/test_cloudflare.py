"""Cloudflare: the adapter, `keyfit add cloudflare` and the wrangler shim, with a stand-in for wrangler (profiles,
folder bindings, `whoami --json`, `auth token --json`) and for Cloudflare's accounts API. A temporary HOME holds
wrangler's config folder; the real one, the Keychain and the network are never touched."""

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

os.environ["KEYFIT_CONFIG_DIR"] = tempfile.mkdtemp()  # tests never touch the real ~/.config/keyfit

from keyfit import cli, environment, secrets, shim  # noqa: E402
from keyfit.adapters import ADAPTERS, cloudflare  # noqa: E402
from keyfit.adapters.cloudflare import CloudflareAdapter  # noqa: E402
from keyfit.cli import main  # noqa: E402
from keyfit.registry import Project, Registry  # noqa: E402
from test_vercel import Executed, executable  # noqa: E402

# Fake account IDs (32 hex digits, like Cloudflare's); never real ones.
ACME_ID, GLOBEX_ID, INITECH_ID = "ac" * 16, "9b" * 16, "1e" * 16
NAMES = {ACME_ID: "Acme", GLOBEX_ID: "Globex", INITECH_ID: "Initech"}
REACH = {"acme": [ACME_ID], "globex": [GLOBEX_ID, INITECH_ID], "default": [INITECH_ID]}  # profile → accounts

UNREACHABLE = ("✘ [ERROR] Not logged in. Your auth token has expired and could not be refreshed because the Cloudflare "
               "auth server could not be reached. This is usually a network problem (connectivity, proxy, or IPv6), "
               "not an invalid login: your stored credentials were left unchanged.\n")
EXPIRED = ("✘ [ERROR] Not logged in. Your auth token has expired and could not be refreshed, and the environment is "
           "non-interactive. Run `wrangler login` in an interactive terminal or set a CLOUDFLARE_API_TOKEN.\n"
           "Run `wrangler whoami` to check your current authentication status.\n")

# A wrangler on PATH for the shim to start; Keyfit's own calls go to FakeWrangler (or, in one test, to this script).
FAKE_WRANGLER = """#!/bin/sh
echo "wrangler ran" >&2
exit 0
"""


def wrangler_profile(bindings: dict, cwd: str) -> str:
    """How wrangler picks a profile without --profile: the longest bound folder at or above cwd, else default."""
    found = [d for d in bindings if cwd == d or cwd.startswith(d + "/")]
    return bindings[max(found, key=len)] if found else "default"


class FakeWrangler:
    """Stands in for `wrangler whoami --json` and `wrangler auth token --json --profile NAME`: each profile has a
    state (ok, signed-out, expired, unreachable, hang) and reaches the accounts in REACH."""

    def __init__(self, store: Path):
        self.store = store
        self.state = {}
        self.calls = []

    def __call__(self, argv, cwd, env, timeout):
        self.calls.append({"argv": argv[1:], "cwd": cwd, "env": dict(env)})
        try:
            bindings = json.loads((self.store / "profiles" / "directory-bindings.json").read_text())
        except OSError:
            bindings = {}
        if argv[1:3] == ["auth", "token"]:
            profile = argv[argv.index("--profile") + 1]
        else:
            profile = wrangler_profile(bindings, cwd)
        state = self.state.get(profile, "ok")
        if state == "hang":
            raise subprocess.TimeoutExpired(argv, timeout)
        if argv[1:] == ["whoami", "--json"]:
            if state == "unreachable":
                return 1, "", UNREACHABLE
            if state == "expired":
                return 1, "", EXPIRED
            if state == "signed-out":
                return 1, '{"loggedIn":false}\n', ""
            accounts = [{"id": a, "name": NAMES[a]} for a in REACH[profile]]
            return 0, json.dumps({"loggedIn": True, "authType": "OAuth Token", "email": f"me@{profile}.io",
                                  "accounts": accounts, "tokenPermissions": ["account:read"]}, indent=2) + "\n", ""
        if argv[1:3] == ["auth", "token"]:
            if state != "ok":
                return 1, "", "✘ [ERROR] Not logged in. Please run `wrangler login` to authenticate.\n"
            return 0, json.dumps({"type": "oauth", "token": f"tok-{profile}"}, indent=2) + "\n", ""
        return 97, "", f"unexpected: {argv[1:]}"


def api(path, token, timeout):
    """Stands in for api.cloudflare.com: GET /accounts lists the accounts a token reaches."""
    profile = token.removeprefix("tok-")
    if profile not in REACH:
        return 401, {"success": False, "errors": [{"code": 10000, "message": "Authentication error"}]}
    if path.startswith("/accounts"):
        return 200, {"success": True, "result": [{"id": a, "name": NAMES[a], "type": "standard"} for a in REACH[profile]]}
    return 404, None


class CloudflareCase(unittest.TestCase):
    """A temporary HOME with wrangler's config folder (profiles default, acme and globex), a wrangler on PATH and
    three projects: client (profile acme, bound to its folder), mine (globex, bound) and plain (no Cloudflare)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.store = self.home / ".wrangler"  # wrangler's older place, used when it exists: the same on every OS
        (self.store / "config").mkdir(parents=True)
        for profile, ext in (("default", "toml"), ("acme", "enc"), ("globex", "enc")):
            (self.store / "config" / f"{profile}.{ext}").write_text('{"v": 1, "ciphertext": "not a real sign-in"}')
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        executable(self.bin / "wrangler", FAKE_WRANGLER)
        self.config = self.tmp / "config"
        self.repos = {}
        for name in ("client", "mine", "plain"):
            repo = self.tmp / name
            repo.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            self.repos[name] = repo
        self.outside = self.tmp / "outside"
        self.outside.mkdir()
        self.bind({str(self.repos["client"]): "acme", str(self.repos["mine"]): "globex"})
        self.env = {"HOME": str(self.home), "PATH": f"{self.bin}{os.pathsep}/usr/bin:/bin",
                    "KEYFIT_CONFIG_DIR": str(self.config)}
        self.wrangler = FakeWrangler(self.store)
        self.fetched = []

        def fetch(path, token, timeout):
            self.fetched.append(path)
            return api(path, token, timeout)
        adapter = ADAPTERS["cloudflare"]
        for patcher in (mock.patch("urllib.request.urlopen", side_effect=AssertionError("no network in tests")),
                        mock.patch.object(adapter, "run", self.wrangler), mock.patch.object(adapter, "fetch", fetch),
                        mock.patch.object(secrets, "SECURITY", "/nonexistent/security"),
                        mock.patch.dict(os.environ, self.env, clear=True)):
            patcher.start()
            self.addCleanup(patcher.stop)
        old = os.getcwd()
        self.addCleanup(os.chdir, old)
        os.chdir(self.repos["client"])

    def bind(self, bindings: dict):
        (self.store / "profiles").mkdir(exist_ok=True)
        (self.store / "profiles" / "directory-bindings.json").write_text(json.dumps(bindings, indent="\t"))

    def bindings(self) -> dict:
        return json.loads((self.store / "profiles" / "directory-bindings.json").read_text())

    def register(self):
        reg = Registry(self.config / "registry.toml")
        for name, repo in self.repos.items():
            reg.add_project(name, repo)
        reg.set_account("cloudflare", "acme", {"profile": "acme", "account_id": ACME_ID, "account_name": "Acme"})
        reg.set_account("cloudflare", "globex", {"profile": "globex", "account_id": GLOBEX_ID, "account_name": "Globex"})
        reg.set_connection("client", "cloudflare", "acme")
        reg.set_connection("mine", "cloudflare", "globex")
        reg.save()
        return reg

    def conn(self, **changes):
        return dict({"name": "acme", "profile": "acme", "account_id": ACME_ID, "account_name": "Acme"}, **changes)


class AdapterTests(CloudflareCase):
    def setUp(self):
        super().setUp()
        self.cf = CloudflareAdapter(run=self.wrangler, fetch=api, cli_timeout=5)

    def check(self, conn=None, folder=None, **extra):
        conn = conn or self.conn()
        os.chdir(folder or self.repos["client"])
        env = dict(self.env, **extra)
        self.cf.inject(conn, env)
        return self.cf.verify(conn, env)

    def test_inject_scrubs_credentials_that_beat_profiles_and_pins_the_account(self):
        env = dict(self.env, CLOUDFLARE_API_TOKEN="inherited", CF_API_TOKEN="old", CLOUDFLARE_API_KEY="k",
                   CLOUDFLARE_EMAIL="e@x.io", CF_EMAIL="e", CF_API_KEY="k", CLOUDFLARE_ACCOUNT_ID=GLOBEX_ID,
                   CF_ACCOUNT_ID=GLOBEX_ID)
        self.cf.inject(self.conn(), env)
        for var in ("CLOUDFLARE_API_TOKEN", "CF_API_TOKEN", "CLOUDFLARE_API_KEY", "CF_API_KEY", "CLOUDFLARE_EMAIL",
                    "CF_EMAIL", "CF_ACCOUNT_ID"):
            self.assertNotIn(var, env)
        self.assertEqual((env["CLOUDFLARE_ACCOUNT_ID"], env["KEYFIT_CLOUDFLARE"]), (ACME_ID, "acme"))

    def test_agent_sessions_get_the_account_for_npx_wrangler(self):
        client = Project("client", [str(self.repos["client"])], {"cloudflare": self.conn()})
        session = environment.build(client, dict(self.env, CLOUDFLARE_API_TOKEN="inherited"))  # what claude starts with
        self.assertEqual(session["CLOUDFLARE_ACCOUNT_ID"], ACME_ID)
        self.assertNotIn("CLOUDFLARE_API_TOKEN", session)
        self.assertIn("cloudflare acme → Acme", session["KEYFIT_SUMMARY"])
        plain = Project("plain", [str(self.repos["plain"])], {})
        nested = environment.build(plain, session)  # a tool of a project without Cloudflare, started from inside
        self.assertNotIn("CLOUDFLARE_ACCOUNT_ID", nested)
        own = environment.build(plain, dict(self.env, CLOUDFLARE_ACCOUNT_ID=INITECH_ID))  # the user's own: kept
        self.assertEqual(own["CLOUDFLARE_ACCOUNT_ID"], INITECH_ID)

    def test_bound_folder_and_its_subfolders_are_confirmed(self):
        check = self.check()
        self.assertEqual((check.status, check.label), ("ok", "acme → Acme"))
        self.assertEqual(check.detail, f"bound at {self.repos['client']} · profile and account confirmed")
        sub = self.repos["client"] / "apps" / "worker"
        sub.mkdir(parents=True)
        self.assertEqual(self.check(folder=sub).status, "ok")
        call = self.wrangler.calls[-1]
        self.assertEqual((call["argv"], call["cwd"]), (["whoami", "--json"], str(sub)))  # whoami runs in the folder
        self.assertNotIn("CLOUDFLARE_API_TOKEN", call["env"])
        self.assertEqual(call["env"]["WRANGLER_SEND_METRICS"], "false")

    def test_unbound_folder_fails_with_the_activate_command(self):
        self.bind({str(self.repos["mine"]): "globex"})
        check = self.check()
        self.assertTrue(check.failed)
        self.assertEqual(check.detail, f"{self.repos['client']} is not bound to wrangler profile acme (wrangler would "
                                       f"use its default login)")
        self.assertEqual(check.fix, f"wrangler auth activate acme {self.repos['client']}")
        self.assertEqual(self.wrangler.calls, [])  # local checks decide without running wrangler
        sub = self.repos["client"] / "src"
        sub.mkdir()
        self.assertEqual(self.check(folder=sub).fix, f"wrangler auth activate acme {self.repos['client']}")  # the top

    def test_subfolder_bound_to_another_profile_shadows_the_project(self):
        sub = self.repos["client"] / "scanner"
        sub.mkdir()
        self.bind({str(self.repos["client"]): "acme", str(sub): "globex"})
        check = self.check(folder=sub)
        self.assertTrue(check.failed)
        self.assertEqual(check.detail, f"{sub} uses wrangler profile globex (bound at {sub}), project expects acme")
        self.assertEqual(check.fix, f"wrangler auth activate acme {sub}")
        self.assertEqual(self.check().status, "ok")  # the project's own folder is still fine

    def test_folder_bound_to_another_projects_profile(self):
        self.bind({str(self.tmp): "globex"})  # an ancestor of every repository
        check = self.check()
        self.assertEqual(check.detail, f"{self.repos['client']} uses wrangler profile globex (bound at {self.tmp}), "
                                       f"project expects acme")
        self.assertEqual(check.fix, f"wrangler auth activate acme {self.repos['client']}")  # never the ancestor

    def test_home_folder_is_never_offered_for_binding(self):
        check = self.check(folder=self.home)
        self.assertEqual((check.detail, check.fix), (f"wrangler in ~ would use profile default, not acme",
                                                     "run wrangler inside the project's folder"))
        self.assertEqual(self.check(folder=self.tmp).fix, "run wrangler inside the project's folder")

    def test_paths_with_spaces_are_quoted_for_the_shell(self):
        spaced = self.tmp / "GTM Engine" / "blocklist"
        spaced.mkdir(parents=True)
        check = self.check(folder=spaced)
        self.assertEqual(check.fix, f"wrangler auth activate acme {self.tmp}/GTM\\ Engine/blocklist")

    def test_missing_profile_fails_with_the_sign_in(self):
        (self.store / "config" / "acme.enc").unlink()
        check = self.check()
        self.assertEqual((check.detail, check.fix), ("wrangler has no profile acme on this Mac", "wrangler auth create acme"))

    def test_account_id_in_the_wrangler_config(self):
        repo = self.repos["client"]
        (repo / "wrangler.toml").write_text(f'name = "app"\naccount_id = "{ACME_ID}"\n')
        self.assertEqual(self.check().status, "ok")
        (repo / "wrangler.toml").write_text(f'name = "app"\naccount_id = "{ACME_ID}"\n[env.staging]\n'
                                            f'account_id = "{GLOBEX_ID}"\n')
        check = self.check()
        self.assertTrue(check.failed)
        self.assertEqual(check.detail, f"{repo / 'wrangler.toml'} sets account_id {GLOBEX_ID} in [env.staging], "
                                       f"project expects Acme ({ACME_ID})")
        self.assertEqual(check.fix, f'set account_id = "{ACME_ID}" there; if the project moved: keyfit add cloudflare '
                                    f"--profile acme --account {GLOBEX_ID}")

    def test_jsonc_config_with_comments_and_trailing_commas(self):
        sub = self.repos["client"] / "app"
        sub.mkdir()
        (sub / "wrangler.jsonc").write_text('{\n  // the scanner\n  "name": "scan", /* see https://x.io//y */\n'
                                            f'  "account_id": "{INITECH_ID}",\n  "routes": ["a/*",],\n}}\n')
        check = self.check(folder=sub)
        self.assertIn(f"wrangler.jsonc sets account_id {INITECH_ID}, project expects Acme", check.detail)

    def test_config_is_found_like_wrangler(self):
        # wrangler looks for wrangler.json in every folder upwards before it looks for wrangler.jsonc, then .toml
        sub = self.repos["client"] / "app"
        sub.mkdir()
        (sub / "wrangler.toml").write_text(f'account_id = "{GLOBEX_ID}"\n')
        (self.repos["client"] / "wrangler.json").write_text(json.dumps({"account_id": ACME_ID}))
        self.assertEqual(cloudflare.find_config(sub), self.repos["client"] / "wrangler.json")
        self.assertEqual(self.check(folder=sub).status, "ok")

    def test_wrangler_too_old_for_profiles(self):
        package = self.repos["client"] / "node_modules" / "wrangler"
        (package / "bin").mkdir(parents=True)
        (package / "package.json").write_text(json.dumps({"name": "wrangler", "version": "4.100.0"}))
        executable(package / "bin" / "wrangler.js", "#!/bin/sh\n")
        (self.repos["client"] / "node_modules" / ".bin").mkdir()
        (self.repos["client"] / "node_modules" / ".bin" / "wrangler").symlink_to("../wrangler/bin/wrangler.js")
        check = self.check()
        self.assertTrue(check.failed)
        self.assertIn("is wrangler 4.100.0, which ignores profiles (4.106 or newer needed)", check.detail)
        self.assertEqual(check.fix, "npm install --save-dev wrangler@latest")
        (package / "package.json").write_text(json.dumps({"name": "wrangler", "version": "4.131.1"}))
        self.assertEqual(self.check().status, "ok")
        self.assertTrue(self.wrangler.calls[-1]["argv"] == ["whoami", "--json"])

    def test_account_the_profile_cannot_reach(self):
        check = self.check(self.conn(account_id=GLOBEX_ID, account_name="Globex"))
        self.assertTrue(check.failed)
        self.assertEqual(check.detail, f"wrangler profile acme has no access to account Globex ({GLOBEX_ID}); it "
                                       f"reaches Acme ({ACME_ID})")
        self.assertEqual(check.fix, "wrangler auth create acme   (as a member of Globex)")

    def test_signed_out_and_expired_profiles_fail_with_the_sign_in(self):
        for state, detail in (("signed-out", "wrangler profile acme: not signed in"),
                              ("expired", "wrangler profile acme: the sign-in expired and wrangler could not renew it")):
            self.wrangler.state["acme"] = state
            check = self.check()
            self.assertEqual((check.detail, check.fix, check.transient), (detail, "wrangler auth create acme", False))

    def test_no_answer_is_transient(self):
        for state in ("unreachable", "hang"):
            self.wrangler.state["acme"] = state
            check = self.check()
            self.assertTrue(check.failed and check.transient, state)
            self.assertEqual(check.fix, "check the network and retry")

    def test_default_profile(self):
        conn = self.conn(name="own", profile="default", account_id=INITECH_ID, account_name="Initech")
        self.assertEqual(self.check(conn, folder=self.repos["plain"]).detail,
                         "default login · profile and account confirmed")
        check = self.check(conn)  # client is bound to acme
        self.assertEqual(check.detail, f"{self.repos['client']} uses wrangler profile acme (bound at "
                                       f"{self.repos['client']}), project expects wrangler's default login")
        self.assertEqual(check.fix, f"wrangler auth deactivate {self.repos['client']}")
        self.wrangler.state["default"] = "signed-out"
        self.assertEqual(self.check(conn, folder=self.repos["plain"]).fix, "wrangler login")

    def test_no_wrangler_to_check_with_is_a_warning(self):
        (self.bin / "wrangler").unlink()
        check = self.check()
        self.assertEqual(check.status, "warn")
        self.assertIn("not checked online: no wrangler here", check.detail)

    def test_broken_records_fail_before_anything_runs(self):
        self.assertEqual(self.check(self.conn(profile=None)).detail, "no wrangler profile recorded")
        self.assertEqual(self.check(self.conn(account_id="acme")).detail,
                         "the expected account must be a 32-character Cloudflare account ID")
        self.assertEqual(self.wrangler.calls, [])

    def test_fingerprint_follows_the_profiles_sign_in(self):
        before = self.cf.fingerprint(self.conn(), self.env)
        (self.store / "config" / "acme.enc").write_text('{"v": 1, "ciphertext": "a new sign-in, longer"}')
        self.assertNotEqual(self.cf.fingerprint(self.conn(), self.env), before)

    def test_config_folder_like_wrangler(self):
        env = {"HOME": str(self.tmp / "nobody")}
        with mock.patch.object(sys, "platform", "darwin"):
            self.assertEqual(cloudflare.store(env), self.tmp / "nobody" / "Library" / "Preferences" / ".wrangler")
            self.assertEqual(cloudflare.store(dict(env, XDG_CONFIG_HOME="/x")), Path("/x/.wrangler"))
        self.assertEqual(cloudflare.store(self.env), self.store)  # ~/.wrangler first when it exists
        self.assertEqual(cloudflare.profiles(self.env), ["default", "acme", "globex"])

    def test_options_and_words_are_read_like_wrangler(self):
        args = ["--cwd", "apps/x", "-c=w.toml", "--profile", "p", "-e", "prod", "deploy", "--minify", "--", "--profile"]
        self.assertEqual(cloudflare.options(args), [("--cwd", "apps/x"), ("--config", "w.toml"), ("--profile", "p")])
        self.assertEqual(cloudflare.words(args), ["deploy"])
        self.assertEqual(cloudflare.words(["--x-provision", "auth", "activate", "acme", "."]),
                         ["auth", "activate", "acme", "."])
        self.assertEqual(cloudflare.words(["auth", "create", "--callback-port", "8976", "--scopes", "d1:write", "x"]),
                         ["auth", "create", "x"])

    def test_whoami_output_of_a_real_process(self):
        script = self.tmp / "wrangler-script"
        executable(script, f"#!{sys.executable}\nimport json, os, sys\n"
                           "open(os.environ['SEEN'], 'w').write(json.dumps([sys.argv[1:], sorted(os.environ)]))\n"
                           "print('some banner line')\n"
                           f"print(json.dumps({{'loggedIn': True, 'accounts': [{{'id': '{ACME_ID}', 'name': 'Acme'}}]}}))\n")
        seen = self.tmp / "seen.json"
        env = dict(self.env, SEEN=str(seen), CLOUDFLARE_API_TOKEN="inherited")
        accounts, reason, state = CloudflareAdapter(cli_timeout=30).whoami(str(script), str(self.repos["client"]), env)
        self.assertEqual((accounts, state), ({ACME_ID: "Acme"}, "ok"), reason)
        argv, names = json.loads(seen.read_text())
        self.assertEqual(argv, ["whoami", "--json"])
        self.assertNotIn("CLOUDFLARE_API_TOKEN", names)
        self.assertIn("WRANGLER_SEND_METRICS", names)


class ShimTests(CloudflareCase):
    """The wrangler shim, run in-process with os.execve caught."""

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
                code = shim.main(["wrangler", *args])
            except Executed:
                code = None
        self.stderr = err.getvalue()
        return code, seen.get("argv"), seen.get("env")

    def test_each_project_gets_its_account_and_no_inherited_token(self):
        code, argv, env = self.run_shim("deploy", CLOUDFLARE_API_TOKEN="inherited", CLOUDFLARE_ACCOUNT_ID=GLOBEX_ID)
        self.assertIsNone(code, self.stderr)
        self.assertEqual(argv, ["wrangler", "deploy"])
        self.assertEqual(env["CLOUDFLARE_ACCOUNT_ID"], ACME_ID)
        self.assertNotIn("CLOUDFLARE_API_TOKEN", env)
        code, argv, env = self.run_shim("deploy", cwd=self.repos["mine"])
        self.assertEqual(env["CLOUDFLARE_ACCOUNT_ID"], GLOBEX_ID)

    def test_project_without_cloudflare_runs_as_before(self):
        code, argv, env = self.run_shim("dev", cwd=self.repos["plain"], CLOUDFLARE_API_TOKEN="yours")
        self.assertIsNone(code, self.stderr)
        self.assertEqual((argv, env["CLOUDFLARE_API_TOKEN"]), (["wrangler", "dev"], "yours"))
        self.assertEqual(self.stderr, "")

    def test_unregistered_folder_passes_through(self):
        code, argv, env = self.run_shim("--profile", "globex", "deploy", cwd=self.outside, CLOUDFLARE_API_TOKEN="yours")
        self.assertEqual(argv, ["wrangler", "--profile", "globex", "deploy"])
        self.assertEqual(env["CLOUDFLARE_API_TOKEN"], "yours")
        self.assertEqual(self.wrangler.calls, [])

    def test_mismatch_blocks_with_the_fix(self):
        self.bind({})
        code, argv, _ = self.run_shim("deploy")
        self.assertEqual((code, argv), (1, None))
        self.assertIn("keyfit: wrangler not started, client needs attention", self.stderr)
        self.assertIn(f"✘ cloudflare acme → Acme   {self.repos['client']} is not bound to wrangler profile acme",
                      self.stderr)
        self.assertIn(f"→ wrangler auth activate acme {self.repos['client']}", self.stderr)

    def test_another_profile_is_refused(self):
        code, argv, _ = self.run_shim("--profile", "globex", "deploy")
        self.assertEqual((code, argv), (1, None))
        self.assertIn(f"keyfit: this folder belongs to client (wrangler profile acme, Cloudflare account Acme); "
                      f"refusing --profile globex", self.stderr)
        code, argv, _ = self.run_shim("deploy", "--profile=acme")
        self.assertEqual(argv, ["wrangler", "deploy", "--profile=acme"])
        code, argv, _ = self.run_shim("auth", "token", "--profile", "globex")  # another project's token: no
        self.assertEqual(code, 1)

    def test_cwd_or_config_pointing_elsewhere_is_refused(self):
        code, argv, _ = self.run_shim("--cwd", "../mine", "deploy")
        self.assertEqual(code, 1)
        self.assertIn(f"refusing --cwd ../mine: {self.repos['mine']} uses wrangler profile globex", self.stderr)
        (self.repos["client"] / "other.toml").write_text(f'account_id = "{GLOBEX_ID}"\n')
        code, argv, _ = self.run_shim("deploy", "-c", "other.toml")
        self.assertEqual(code, 1)
        self.assertIn(f"refusing --config other.toml: {self.repos['client'] / 'other.toml'} sets account_id "
                      f"{GLOBEX_ID}", self.stderr)
        sub = self.repos["client"] / "app"
        sub.mkdir()
        (sub / "wrangler.toml").write_text(f'account_id = "{ACME_ID}"\n')
        code, argv, _ = self.run_shim("deploy", "--config", "app/wrangler.toml", "--cwd", ".")
        self.assertIsNone(code, self.stderr)

    def test_login_and_logout_are_refused_in_a_project_with_a_named_profile(self):
        for args in (["login"], ["--x-provision", "login"], ["logout"]):
            code, argv, _ = self.run_shim(*args)
            self.assertEqual((code, argv), (1, None), args)
        self.assertIn("keyfit: this folder belongs to client, which uses wrangler profile acme; `wrangler logout` would "
                      "change only wrangler's default login, which client does not use.\n  Sign in again as acme: "
                      "wrangler auth create acme", self.stderr)
        code, argv, _ = self.run_shim("login", cwd=self.repos["plain"])  # no Cloudflare account: wrangler's own login
        self.assertEqual(argv, ["wrangler", "login"])
        self.assertEqual(self.wrangler.calls, [])  # no check ran

    def test_sign_in_and_binding_commands_run_without_checks(self):
        self.wrangler.state["acme"] = "signed-out"
        for args in (["auth", "create", "acme"], ["auth", "activate", "acme"], ["auth", "list"], ["whoami"],
                     ["auth", "activate", "acme", "."], ["auth", "create", "brand-new"]):
            code, argv, env = self.run_shim(*args, CLOUDFLARE_API_TOKEN="inherited")
            self.assertEqual(argv, ["wrangler", *args], self.stderr)
            self.assertNotIn("CLOUDFLARE_API_TOKEN", env)  # wrangler refuses `auth create` while one is set
        self.assertEqual(self.wrangler.calls, [])

    def test_rebinding_a_registered_folder_to_another_profile_is_refused(self):
        for args, shown in ((["auth", "activate", "globex"], f"refusing `wrangler auth activate globex` for "
                                                               f"{self.repos['client']}: that folder belongs to client"),
                            (["auth", "activate", "acme", "../mine"], "that folder belongs to mine, which uses wrangler "
                                                                      "profile globex"),
                            (["auth", "deactivate"], f"refusing `wrangler auth deactivate` for {self.repos['client']}: "
                                                     f"client uses wrangler profile acme there"),
                            (["auth", "create", "globex"], "refusing `wrangler auth create globex`: that profile is "
                                                           "the sign-in of mine"),
                            (["auth", "create", "--callback-port", "8976", "globex"], "the sign-in of mine"),
                            (["auth", "delete", "acme"], "refusing `wrangler auth delete acme`: client uses that "
                                                         "profile")):
            code, argv, _ = self.run_shim(*args)
            self.assertEqual((code, argv), (1, None), args)
            self.assertIn(shown, self.stderr)
        self.bind({str(self.repos["client"]): "globex"})  # a wrong binding may go: that is the fix
        code, argv, _ = self.run_shim("auth", "deactivate")
        self.assertEqual(argv, ["wrangler", "auth", "deactivate"])

    def test_session_keeps_wrangler_on_its_project(self):
        code, argv, _ = self.run_shim("deploy", cwd=self.repos["plain"], KEYFIT_PROJECT="client")
        self.assertEqual(code, 1)  # wrangler would use another sign-in there: the binding check says so
        self.assertIn(f"{self.repos['plain']} is not bound to wrangler profile acme", self.stderr)
        code, argv, _ = self.run_shim("auth", "activate", "acme", str(self.repos["mine"]), cwd=self.outside,
                                      KEYFIT_PROJECT="client")
        self.assertEqual(code, 1)  # an agent of client cannot move mine's folder either
        self.assertIn("that folder belongs to mine", self.stderr)

    def test_sign_in_now_runs_wrangler_auth_create(self):
        self.wrangler.state["acme"] = "expired"
        ran = []
        real_run = subprocess.run

        def run(argv, *args, **kwargs):
            if kwargs.get("executable") == str(self.bin / "wrangler"):
                ran.append((list(argv), "CLOUDFLARE_API_TOKEN" in kwargs["env"]))
                self.wrangler.state["acme"] = "ok"
                return subprocess.CompletedProcess(argv, 0)
            return real_run(argv, *args, **kwargs)

        class Terminal(io.StringIO):
            def isatty(self):
                return True
        err = Terminal()
        with mock.patch.object(sys, "stdin", Terminal("\n")), mock.patch("subprocess.run", run), \
                contextlib.redirect_stderr(err), mock.patch("os.execve", side_effect=Executed()):
            with self.assertRaises(Executed):
                shim.main(["wrangler", "deploy"])
        self.assertIn("✘ cloudflare acme → Acme   wrangler profile acme: the sign-in expired and wrangler could not "
                      "renew it\n    → wrangler auth create acme\n  Sign in now? [Y/n] ", err.getvalue())
        self.assertEqual(ran, [(["wrangler", "auth", "create", "acme"], False)])
        self.assertIn("keyfit: signed in, starting wrangler", err.getvalue())

    def test_dry_run_names_only_the_variables(self):
        code, _, _ = self.run_shim("deploy", KEYFIT_DRY_RUN="1", CLOUDFLARE_API_TOKEN="inherited")
        self.assertEqual(code, 0)
        self.assertIn("environment changes (names only): CLOUDFLARE_ACCOUNT_ID, CLOUDFLARE_API_TOKEN, KEYFIT_CLOUDFLARE",
                      self.stderr)
        self.assertNotIn("inherited", self.stderr)


class AddCloudflareTests(CloudflareCase):
    def setUp(self):
        super().setUp()
        with contextlib.redirect_stdout(io.StringIO()):
            main(["init", "--yes", "--name", "client"])

    def add(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
                mock.patch.object(sys, "stdin", io.StringIO("")):
            code = main(["add", "cloudflare", *args])
        return code, out.getvalue(), err.getvalue()

    def registry(self):
        return tomllib.loads((self.config / "registry.toml").read_text())

    def test_the_folders_profile_and_its_only_account(self):
        code, out, err = self.add()
        self.assertEqual(code, 0, err)
        self.assertEqual(out, f"client: cloudflare account 'acme' → wrangler profile acme, account Acme ({ACME_ID})\n")
        reg = self.registry()
        self.assertEqual(reg["projects"]["client"]["cloudflare"], "acme")
        self.assertEqual(reg["accounts"]["cloudflare"]["acme"],
                         {"profile": "acme", "account_id": ACME_ID, "account_name": "Acme"})
        call = self.wrangler.calls[0]
        self.assertEqual(call["argv"], ["auth", "token", "--json", "--profile", "acme"])
        self.assertNotEqual(call["cwd"], str(self.repos["client"]))  # never where a .env could hold another token
        self.assertEqual(self.fetched, ["/accounts?per_page=50"])
        self.assertNotIn("tok-acme", out + err + (self.config / "registry.toml").read_text())

    def test_several_accounts_need_a_choice_or_the_configs_account_id(self):
        code, out, err = self.add("--profile", "globex")
        self.assertEqual(code, 1)
        self.assertIn(f"wrangler profile globex reaches 2 accounts (Globex ({GLOBEX_ID}), Initech ({INITECH_ID})): pick "
                      f"one with --account ID", err)
        code, out, err = self.add("--profile", "globex", "--account", "initech")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.registry()["accounts"]["cloudflare"]["globex"]["account_id"], INITECH_ID)
        self.assertIn(f"  next: {self.repos['client']} uses wrangler profile acme (bound at {self.repos['client']}), "
                      f"project expects globex\n  → wrangler auth activate globex {self.repos['client']}\n", out)
        self.assertEqual(self.bindings()[str(self.repos["client"])], "acme")  # nothing ran without a terminal
        (self.repos["client"] / "wrangler.toml").write_text(f'account_id = "{GLOBEX_ID}"\n')
        code, out, err = self.add("--profile", "globex", "--name", "g")
        self.assertEqual(self.registry()["accounts"]["cloudflare"]["g"]["account_id"], GLOBEX_ID)

    def test_binding_is_offered_at_a_terminal(self):
        self.bind({})
        ran = []
        real_run = subprocess.run

        def run(argv, *args, **kwargs):
            if argv[0] == str(self.bin / "wrangler"):
                ran.append(argv)
                return subprocess.CompletedProcess(argv, 0)
            return real_run(argv, *args, **kwargs)
        stdin = io.StringIO("")
        stdin.isatty = lambda: True
        with mock.patch.object(cli, "_ask", return_value="y"), mock.patch("subprocess.run", run), \
                mock.patch.object(sys, "stdin", stdin), contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(main(["add", "cloudflare", "--profile", "acme"]), 0)
        self.assertIn(f"→ wrangler auth activate acme {self.repos['client']}", out.getvalue())
        self.assertEqual(ran, [[str(self.bin / "wrangler"), "auth", "activate", "acme", str(self.repos["client"])]])

    def test_unknown_or_signed_out_profile_and_no_profiles(self):
        code, out, err = self.add("--profile", "nope")
        self.assertEqual(code, 1)
        self.assertIn("wrangler has no profile nope on this Mac (it has default, acme, globex)\n"
                      "  → sign in: wrangler auth create nope", err)
        self.wrangler.state["acme"] = "signed-out"
        code, out, err = self.add()
        self.assertEqual(code, 1)
        self.assertIn("could not read the accounts of wrangler profile acme: wrangler profile acme is not signed in (or "
                      "its sign-in expired)\n  → wrangler auth create acme", err)
        for f in (self.store / "config").iterdir():
            f.unlink()
        code, out, err = self.add()
        self.assertEqual(code, 1)
        self.assertIn("wrangler has no sign-ins on this Mac yet.\n  → sign in with a profile for client: wrangler auth "
                      "create client, then: keyfit add cloudflare", err)
        self.assertNotIn("cloudflare", self.registry()["projects"]["client"])

    def test_guided_init_offers_the_profile_bound_to_the_folder(self):
        with mock.patch.object(cli, "_ask", return_value="y"), contextlib.redirect_stdout(io.StringIO()) as out:
            cli._offer_cloudflare("client", {"registry": None, "project": "client"})
        self.assertIn(f"Cloudflare: {self.repos['client']} uses wrangler profile acme (bound at {self.repos['client']})",
                      out.getvalue())
        self.assertIn("client: cloudflare account 'acme' → wrangler profile acme, account Acme", out.getvalue())
        self.bind({})
        with mock.patch.object(cli, "_ask", return_value="n"), contextlib.redirect_stdout(io.StringIO()) as out:
            cli._offer_cloudflare("client", {"registry": None, "project": "client"})
        self.assertEqual(out.getvalue(), "Cloudflare: no wrangler profile bound here and no wrangler config — skipped\n")

    def test_status_and_doctor_show_profile_binding_and_account(self):
        self.add()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            main(["status"])
        self.assertIn(f"✔ cloudflare acme → Acme   bound at {self.repos['client']} · profile and account confirmed",
                      out.getvalue())
        with contextlib.redirect_stdout(io.StringIO()) as out, mock.patch.object(secrets, "keychain_reachable",
                                                                                 return_value=""):
            main(["doctor"])
        self.assertIn(f"✔ wrangler  {self.bin / 'wrangler'}", out.getvalue())
        self.assertIn(f"✔ cloudflare acme → Acme   bound at {self.repos['client']} · account {ACME_ID}\n", out.getvalue())


if __name__ == "__main__":
    unittest.main()
