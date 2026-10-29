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
from keyfit.checks import Check  # noqa: E402
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

# Where an inherited variable or a folder's .env could send a profile's sign-in (KEY-60); no real host.
ELSEWHERE = {"CLOUDFLARE_API_BASE_URL": "https://evil.example/client/v4", "WRANGLER_AUTH_DOMAIN": "evil.example",
             "WRANGLER_AUTH_URL": "https://evil.example/auth", "WRANGLER_TOKEN_URL": "https://evil.example/token",
             "WRANGLER_REVOKE_URL": "https://evil.example/revoke", "WRANGLER_CLIENT_ID": "evil"}

# node_modules/.bin/wrangler as pnpm writes it: a script that runs the package's bin/wrangler.js, not a link to it.
PNPM_BIN = """#!/bin/sh
basedir=$(dirname "$(echo "$0" | sed -e 's,\\\\,/,g')")

case `uname` in
    *CYGWIN*) basedir=`cygpath -w "$basedir"`;;
esac

if [ -x "$basedir/node" ]; then
  exec "$basedir/node"  "$basedir/../wrangler/bin/wrangler.js" "$@"
else
  exec node  "$basedir/../wrangler/bin/wrangler.js" "$@"
fi
"""

# A wrangler on PATH for the shim to start; Keyfit's own calls go to FakeWrangler (or, in one test, to this script).
FAKE_WRANGLER = """#!/bin/sh
echo "wrangler ran" >&2
exit 0
"""


class Terminal(io.StringIO):
    """stdin or stderr as a terminal: Keyfit asks its questions only there."""

    def isatty(self):
        return True


def wrangler_profile(bindings: dict, cwd: str) -> str:
    """How wrangler picks a profile without --profile: the longest bound folder at or above cwd, else default."""
    found = [d for d in bindings if cwd == d or cwd.startswith(d + "/")]
    return bindings[max(found, key=len)] if found else "default"


class FakeWrangler:
    """Stands in for `wrangler whoami --json`, `wrangler auth token --json --profile NAME` and `wrangler auth activate
    NAME DIR`: each profile has a state (ok, signed-out, expired, unreachable, hang) and reaches the accounts in REACH;
    activate binds DIR in the temporary store (activation "ok"), fails ("error") or does nothing ("ignored")."""

    def __init__(self, store: Path):
        self.store = store
        self.state = {}
        self.activation = "ok"
        self.calls = []

    def __call__(self, argv, cwd, env, timeout):
        self.calls.append({"argv": argv[1:], "cwd": cwd, "env": dict(env)})
        try:
            bindings = json.loads((self.store / "profiles" / "directory-bindings.json").read_text())
        except OSError:
            bindings = {}
        if argv[1:3] == ["auth", "activate"]:
            name, folder = argv[3], os.path.abspath(os.path.join(cwd, argv[4]))
            if "CLOUDFLARE_API_TOKEN" in env:  # as wrangler does
                return 1, "", "✘ [ERROR] Cannot manage auth profiles while CLOUDFLARE_API_TOKEN is set.\n"
            if self.activation == "error" or not any((self.store / "config" / f"{name}.{ext}").exists()
                                                      for ext in ("toml", "enc")):
                return 1, " ⛅️ wrangler 4.147.0\n───────────────────\n", (
                    f'✘ [ERROR] Profile "{name}" does not exist. Run `wrangler auth create {name}` first.\n\n\n'
                    f'🪵  Logs were written to "{self.store}/logs/wrangler.log"\n')
            if self.activation == "ok":
                bindings[folder] = name
                (self.store / "profiles").mkdir(exist_ok=True)
                (self.store / "profiles" / "directory-bindings.json").write_text(json.dumps(bindings, indent="\t"))
            return 0, f'Profile "{name}" activated for "{folder}".\n', ""
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
        package = self.tmp / "lib" / "node_modules" / "wrangler"  # as npm installs it: bin/wrangler links into it
        (package / "bin").mkdir(parents=True)
        (package / "package.json").write_text(json.dumps({"name": "wrangler", "version": "4.152.0"}))
        executable(package / "bin" / "wrangler.js", FAKE_WRANGLER)
        (self.bin / "wrangler").symlink_to(package / "bin" / "wrangler.js")
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
        for patcher in (mock.patch("keyfit.http.urlopen", side_effect=AssertionError("no network in tests")),
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

    def test_inject_pins_the_api_host_and_removes_sign_in_endpoints(self):
        env = dict(self.env, **ELSEWHERE)
        self.cf.inject(self.conn(), env)
        self.assertEqual(env["CLOUDFLARE_API_BASE_URL"], "https://api.cloudflare.com/client/v4")
        self.assertFalse([name for name in env if name.startswith("WRANGLER_")])
        self.cf.forget(env)  # a tool of a project without Cloudflare, started from that session
        self.assertNotIn("CLOUDFLARE_API_BASE_URL", env)
        own = dict(self.env, CLOUDFLARE_API_BASE_URL="https://api.staging.example/client/v4")
        self.cf.forget(own)  # set by you, not by Keyfit: kept
        self.assertEqual(own["CLOUDFLARE_API_BASE_URL"], "https://api.staging.example/client/v4")

    def test_dotenv_that_sets_the_api_host_or_sign_in_fails_before_wrangler_runs(self):
        """wrangler loads .env, .env.local and .env.NAME(.local) into its environment (KEY-60): one that sets where
        the sign-in goes fails the local check, named by file and variable, never by value, and wrangler never runs."""
        client = self.repos["client"]
        for name, variable, line in (
                (".env", "CLOUDFLARE_API_BASE_URL", "CLOUDFLARE_API_BASE_URL=https://evil.example/client/v4"),
                (".env.local", "WRANGLER_TOKEN_URL", "export WRANGLER_TOKEN_URL='https://evil.example/token'"),
                (".env.staging", "WRANGLER_AUTH_DOMAIN", "  WRANGLER_AUTH_DOMAIN = evil.example # comment"),
                (".env.staging.local", "WRANGLER_AUTH_URL", "WRANGLER_AUTH_URL: https://evil.example/auth"),
                (".env", "WRANGLER_REVOKE_URL", "\ufeffWRANGLER_REVOKE_URL=https://evil.example/revoke"),
                (".env", "WRANGLER_CLIENT_ID", 'A="x\nWRANGLER_CLIENT_ID=evil"')):  # in a quoted value: a false alarm
            (client / name).write_text(f"OTHER=1\n{line}\n")
            check = self.check()
            self.assertTrue(check.failed, line)
            self.assertEqual(check.detail, f"{client / name} sets {variable}, which would decide where wrangler sends "
                                           f"the profile's sign-in", line)
            self.assertEqual(check.fix, f"remove {variable} from {client / name}   (Keyfit keeps wrangler on "
                                        f"Cloudflare's own API host and sign-in)")
            self.assertNotIn("evil", check.detail + check.fix)
            (client / name).unlink()
        self.assertEqual(self.wrangler.calls, [])
        (client / ".env").write_text("WRANGLER_TOKEN_URL=x\nWRANGLER_AUTH_URL=y\n")
        (client / ".env.local").write_text("CLOUDFLARE_API_BASE_URL=z\n")
        self.assertEqual(self.check().detail, f"{client / '.env'} sets WRANGLER_AUTH_URL, WRANGLER_TOKEN_URL; "
                                              f"{client / '.env.local'} sets CLOUDFLARE_API_BASE_URL, which would "
                                              f"decide where wrangler sends the profile's sign-in")
        for path in (client / ".env", client / ".env.local"):
            path.unlink()
        for name, text in ((".envrc", "export WRANGLER_TOKEN_URL=x\n"),  # direnv's, not wrangler's
                           (".env", "# WRANGLER_TOKEN_URL=x\nCLOUDFLARE_API_TOKEN_URL=x\nMY_WRANGLER_AUTH_URL=x\n")):
            (client / name).write_text(text)
        (client / ".env.d").mkdir()  # a folder named like one
        self.assertEqual(self.check().status, "ok")
        sub = client / "apps" / "worker"  # wrangler loads the .env of the folder it runs in, not of those above
        sub.mkdir(parents=True)
        (sub / ".env").write_text("WRANGLER_TOKEN_URL=x\n")
        self.assertEqual(self.check().status, "ok")
        self.assertTrue(self.check(folder=sub).failed)

    def test_a_fake_wrangler_sees_cloudflares_own_host_and_sign_in(self):
        """The online check starts wrangler with CLOUDFLARE_API_BASE_URL set to Cloudflare's own (it beats the folder's
        .env) and without inherited sign-in endpoints."""
        seen = self.tmp / "seen.json"
        answer = json.dumps({"loggedIn": True, "accounts": [{"id": ACME_ID}]})
        executable(self.bin / "wrangler", f"#!{sys.executable}\nimport json, os\n"
                                          "open(os.environ['SEEN'], 'w').write(json.dumps(dict(os.environ)))\n"
                                          f"print({answer!r})\n")
        env = dict(self.env, SEEN=str(seen), **ELSEWHERE)
        cf = CloudflareAdapter(cli_timeout=30)
        self.enterContext(mock.patch.dict(os.environ, env))  # the shell's: the check starts wrangler with it
        for prepared in (False, True):  # the environment as it is, and as the shim builds it
            if prepared:
                cf.inject(self.conn(), env)
            self.assertEqual(cf.verify(self.conn(), env).status, "ok")
            started = json.loads(seen.read_text())
            self.assertEqual(started["CLOUDFLARE_API_BASE_URL"], "https://api.cloudflare.com/client/v4")
            self.assertEqual(set(ELSEWHERE) & set(started), {"CLOUDFLARE_API_BASE_URL"})

    def test_agent_sessions_get_the_account_for_npx_wrangler(self):
        client = Project("client", [str(self.repos["client"])], {"cloudflare": self.conn()})
        session = environment.build(client, dict(self.env, CLOUDFLARE_API_TOKEN="inherited"))  # what claude starts with
        self.assertEqual(session["CLOUDFLARE_ACCOUNT_ID"], ACME_ID)
        self.assertEqual(session["CLOUDFLARE_API_BASE_URL"], cloudflare.API)  # also for npx wrangler in subfolders
        self.assertNotIn("CLOUDFLARE_API_TOKEN", session)
        self.assertIn("cloudflare acme → Acme", session["KEYFIT_SUMMARY"])
        plain = Project("plain", [str(self.repos["plain"])], {})
        nested = environment.build(plain, session)  # a tool of a project without Cloudflare, started from inside
        self.assertNotIn("CLOUDFLARE_ACCOUNT_ID", nested)
        self.assertNotIn("CLOUDFLARE_API_BASE_URL", nested)
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
        spaced = self.tmp / "Acme Corp" / "blocklist"
        spaced.mkdir(parents=True)
        check = self.check(folder=spaced)
        self.assertEqual(check.fix, f"wrangler auth activate acme {self.tmp}/Acme\\ Corp/blocklist")

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
        self.assertEqual(cloudflare.options(args), [("--cwd", "apps/x"), ("--config", "w.toml"), ("--profile", "p"),
                                                    ("--env", "prod")])
        self.assertEqual(cloudflare.words(args), ["deploy"])
        self.assertEqual(cloudflare.words(["--x-provision", "auth", "activate", "acme", "."]),
                         ["auth", "activate", "acme", "."])
        self.assertEqual(cloudflare.words(["auth", "create", "--callback-port", "8976", "--scopes", "d1:write", "x"]),
                         ["auth", "create", "x"])

    def test_short_options_are_read_like_yargs(self):
        """wrangler's parser (yargs-parser) reads -c in a group of short options and with the value attached."""
        for args, config in ((["-c/abs/w.toml", "deploy"], "/abs/w.toml"),  # attached: starts with a non-word character
                             (["-c~/w.toml", "deploy"], "~/w.toml"),
                             (["-ec./w.toml", "deploy"], "./w.toml"),  # in a group, attached relative path
                             (["-ec", "w.toml", "deploy"], "w.toml"),  # a group ending in c
                             (["-vc", "w.toml", "deploy"], "w.toml"),
                             (["-c=w.toml", "deploy"], "w.toml"), (["-ec=w.toml", "deploy"], "w.toml"),
                             (["--c", "w.toml", "deploy"], "w.toml"), (["--c=w.toml", "deploy"], "w.toml"),
                             (["-c123", "deploy"], "123"), (["-c-", "deploy"], "-")):
            self.assertEqual([o for o in cloudflare.options(args) if o[0] == "--config"], [("--config", config)], args)
            self.assertEqual(cloudflare.words(args), ["deploy"], args)
        self.assertEqual(cloudflare.options(["-cc", "w.toml", "deploy"]), [("--config", None), ("--config", "w.toml")])
        # `-cw.toml` is the flags c, w, ., t, o, m and l; `-c./w.toml` a dotted key, and wrangler gets an object for
        # its config (it stops), with the next argument as the object's value
        self.assertEqual(cloudflare.options(["-cw.toml", "deploy"]), [("--config", None)])
        self.assertEqual((cloudflare.options(["-c./w.toml", "deploy"]), cloudflare.words(["-c./w.toml", "deploy"])),
                         ([], []))
        self.assertEqual(cloudflare.words(["--env-file", "a.env", "b.env", "--profile", "p", "auth", "list"]),
                         ["auth", "list"])  # --env-file takes every argument up to the next option
        self.assertEqual(cloudflare.words(["-1", "--no-x-provision", "login", "--json", "true"]), ["-1", "login"])

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


    # Configs, versions and the check's environment (KEY-74) -----------------------------------------------------

    def test_a_deploy_redirect_to_a_generated_config_is_checked(self):
        """deploy, dev and versions upload follow .wrangler/deploy/config.json to the config a build generated (the
        Vite plugin's dist/<name>/wrangler.json): a stale one keeps another account after wrangler.jsonc was fixed."""
        repo = self.repos["client"]
        (repo / "wrangler.jsonc").write_text(json.dumps({"account_id": ACME_ID}))
        generated = repo / "dist" / "app" / "wrangler.json"
        generated.parent.mkdir(parents=True)
        generated.write_text(json.dumps({"account_id": GLOBEX_ID}))
        redirect = repo / ".wrangler" / "deploy" / "config.json"
        redirect.parent.mkdir(parents=True)
        redirect.write_text('{"configPath": "../../dist/app/wrangler.json"}')
        check = self.check()
        self.assertTrue(check.failed)
        self.assertEqual(check.detail, f"{generated} (where {redirect} sends wrangler deploy) sets account_id "
                                       f"{GLOBEX_ID}, project expects Acme ({ACME_ID})")
        self.assertEqual(check.fix, f"build again, or delete {redirect} if a build left it behind")
        sub = repo / "src"
        sub.mkdir()
        self.assertTrue(self.check(folder=sub).failed)  # looked for upwards, like the config
        app = repo / "app"
        app.mkdir()
        (app / "wrangler.jsonc").write_text(json.dumps({"account_id": ACME_ID}))
        self.assertEqual(self.check(folder=app).status, "ok")  # not beside the config found there: wrangler stops
        redirect.write_text('{"configPath": "../../dist/app/wrangler.json",}  // a build wrote it')
        self.assertTrue(self.check().failed)  # read as JSON with comments, like wrangler
        redirect.write_text("{not json")
        check = self.check()
        self.assertEqual((check.detail, check.fix),
                         (f"Keyfit cannot read {redirect}, so it cannot tell which account_id wrangler would use",
                          f"fix {redirect}: Keyfit reads it as JSON with comments, or delete it if a build left it "
                          f"behind"))
        redirect.write_text('{"configPath": "../../dist/app/wrangler.json"}')
        generated.write_text(json.dumps({"account_id": ACME_ID}))  # built again
        self.assertEqual(self.check().status, "ok")
        self.assertEqual(self.wrangler.calls[-1]["argv"], ["whoami", "--json"])

    def test_a_config_keyfit_cannot_parse_fails_the_check(self):
        """wrangler drops a byte order mark and replaces bytes that are not UTF-8, and its TOML parser reads newer
        syntax than tomllib: a config Keyfit cannot parse fails the check instead of skipping it."""
        repo = self.repos["client"]
        toml = repo / "wrangler.toml"
        for what, start in (("a byte order mark", b"\xef\xbb\xbf"), ("bytes that are not UTF-8", b"# caf\xe9\n")):
            toml.write_bytes(start + f'account_id = "{GLOBEX_ID}"\n'.encode())
            self.assertEqual(self.check().detail,
                             f"{toml} sets account_id {GLOBEX_ID}, project expects Acme ({ACME_ID})", what)
        toml.write_text(f'account_id = "{GLOBEX_ID}"\nlimits = {{ cpu_ms = 50, }}\n')  # TOML 1.1: a trailing comma
        check = self.check()
        self.assertEqual((check.detail, check.fix),
                         (f"Keyfit cannot read {toml}, so it cannot tell which account_id wrangler would use",
                          f"fix {toml}: Keyfit reads it as TOML 1.0 (not yet TOML 1.1)"))
        toml.unlink()
        jsonc = repo / "wrangler.jsonc"
        jsonc.write_text(f'{{\n  // the worker\r  "account_id": "{GLOBEX_ID}"\n}}\n')  # a comment ended by a lone CR
        self.assertIn(f"wrangler.jsonc sets account_id {GLOBEX_ID}", self.check().detail)
        jsonc.write_text('{"account_id": ')
        self.assertIn("Keyfit cannot read", self.check().detail)
        self.assertEqual(self.wrangler.calls, [])
        other = repo / "w.yaml"  # wrangler reads settings only from .toml, .json and .jsonc
        other.write_text(f'account_id = "{GLOBEX_ID}"\n')
        self.assertEqual(cloudflare.account_ids(other), [])

    def test_an_entry_files_config_is_looked_for_like_wrangler(self):
        """With an entry file, wrangler looks for its config from the file's folder; wrangler 4.152 starts that search
        a folder deeper (path.resolve(dir, dir)), so Keyfit checks what both searches find."""
        other = self.tmp / "other"
        (other / "src").mkdir(parents=True)
        (other / "wrangler.toml").write_text(f'account_id = "{GLOBEX_ID}"\n')
        client = self.repos["client"]
        self.assertEqual(cloudflare.configs(client, "../other/src/index.ts"), [(other / "wrangler.toml", None)])
        (other / "src" / "wrangler.json").write_text("{}")
        self.assertEqual(cloudflare.configs(client, "../other/src/index.ts"),
                         [(other / "src" / "wrangler.json", None), (other / "wrangler.toml", None)])
        self.assertEqual(cloudflare.configs(client, str(other / "src" / "index.ts")),
                         [(other / "src" / "wrangler.json", None)])
        self.assertEqual(cloudflare.configs(client, "index.ts"), cloudflare.configs(client))
        mismatch = cloudflare.mismatch(self.conn(), client, self.env, entry="../other/src/index.ts")
        self.assertEqual(mismatch[0], f"{other / 'wrangler.toml'} sets account_id {GLOBEX_ID}, project expects Acme "
                                      f"({ACME_ID})")

    def test_pnpm_layouts_and_the_wrangler_on_path_are_version_checked(self):
        """pnpm's node_modules/.bin/wrangler is a script, not a link; `wrangler` runs the one on PATH while agents'
        `npx wrangler` runs the project's: both must be new enough, and a version Keyfit cannot tell is a warning."""
        client = self.repos["client"]
        store = client / "node_modules" / ".pnpm" / "wrangler@4.100.0" / "node_modules" / "wrangler"
        (store / "bin").mkdir(parents=True)
        (store / "package.json").write_text(json.dumps({"name": "wrangler", "version": "4.100.0"}))
        executable(store / "bin" / "wrangler.js", "#!/bin/sh\n")
        (client / "node_modules" / "wrangler").symlink_to(store)
        (client / "node_modules" / ".bin").mkdir()
        executable(client / "node_modules" / ".bin" / "wrangler", PNPM_BIN)
        check = self.check()
        self.assertEqual((check.detail, check.fix),
                         (f"{client / 'node_modules/.bin/wrangler'} is wrangler 4.100.0, which ignores profiles "
                          f"(4.106 or newer needed)", "npm install --save-dev wrangler@latest"))
        (store / "package.json").write_text(json.dumps({"name": "wrangler", "version": "4.152.0"}))
        self.assertEqual(self.check().status, "ok")
        package = self.tmp / "lib" / "node_modules" / "wrangler"  # the one `wrangler` runs, on PATH
        (package / "package.json").write_text(json.dumps({"name": "wrangler", "version": "4.90.1"}))
        script = package / "bin" / "wrangler.js"  # what PATH's wrangler links to, named in messages
        check = self.check()
        self.assertEqual((check.detail, check.fix),
                         (f"{script} is wrangler 4.90.1, which ignores profiles (4.106 or newer needed)",
                          "npm install -g wrangler@latest"))
        (package / "package.json").unlink()
        check = self.check()
        self.assertEqual(check.status, "warn")  # wrangler still starts
        self.assertEqual(check.detail, f"bound at {client} · could not tell which wrangler {script} is · profile and "
                                       f"account confirmed")
        self.assertEqual(check.fix, f"{script} --version must be 4.106 or newer (older ones ignore profiles)")

    def test_the_projects_own_wrangler_is_looked_for_up_to_its_top_only(self):
        # these folders are outside HOME: a node_modules/.bin another user made in a shared folder above never runs
        shared = self.tmp / "node_modules" / ".bin"
        shared.mkdir(parents=True)
        executable(shared / "wrangler", FAKE_WRANGLER)
        package = self.outside / "package"  # no repository: up to the nearest package.json
        (package / "src").mkdir(parents=True)
        (package / "package.json").write_text("{}")
        loose = self.outside / "loose" / "deeper"  # no repository and no package.json: that folder only
        loose.mkdir(parents=True)
        for start in (self.repos["client"], package / "src", loose):
            with self.subTest(start=start):
                self.assertEqual(cloudflare.wrangler_bin(self.env, start), str(self.bin / "wrangler"))
        app = self.repos["client"] / "app"
        app.mkdir()
        (app / "package.json").write_text("{}")  # a monorepo's package: the wrangler hoisted to the top still counts
        for top, start in ((self.repos["client"], app), (package, package / "src")):
            (top / "node_modules" / ".bin").mkdir(parents=True)
            executable(top / "node_modules" / ".bin" / "wrangler", FAKE_WRANGLER)
            self.assertEqual(cloudflare.wrangler_bin(self.env, start), str(top / "node_modules" / ".bin" / "wrangler"))

    def test_the_online_check_gets_none_of_the_projects_other_tokens(self):
        """At an agent start the environment holds every account of the project. The repository's own wrangler, which
        the online check runs, gets the shell's environment and the Cloudflare account only."""
        client = self.repos["client"]
        seen = self.tmp / "seen.json"
        (client / "node_modules" / ".bin").mkdir(parents=True)
        answer = json.dumps({"loggedIn": True, "accounts": [{"id": ACME_ID}]})
        executable(client / "node_modules" / ".bin" / "wrangler",
                   f"#!{sys.executable}\nimport json, os\n"
                   "open(os.environ['SEEN'], 'w').write(json.dumps(sorted(os.environ)))\n"  # names only
                   f"print({answer!r})\n")
        self.enterContext(mock.patch.dict(os.environ, SEEN=str(seen), YOURS="from the shell"))
        env = dict(os.environ, CONVEX_OVERRIDE_ACCESS_TOKEN="the project's", GH_TOKEN="the project's",
                   KEYFIT_SHIM_DEPTH="1")  # what Keyfit set for the project's other accounts
        cf = CloudflareAdapter(cli_timeout=30)
        cf.inject(self.conn(), env)
        check = cf.verify(self.conn(), env)
        self.assertFalse(check.failed, check.detail)
        self.assertIn("profile and account confirmed", check.detail)
        names = json.loads(seen.read_text())
        self.assertNotIn("CONVEX_OVERRIDE_ACCESS_TOKEN", names)
        self.assertNotIn("GH_TOKEN", names)
        for name in ("YOURS", "CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_BASE_URL", "KEYFIT_SHIM_DEPTH"):
            self.assertIn(name, names)

    def test_dotenv_that_moves_wranglers_config_folder_fails(self):
        """XDG_CONFIG_HOME picks wrangler's config folder (profiles and bindings) when ~/.wrangler does not exist."""
        client = self.repos["client"]
        (client / ".env").write_text("XDG_CONFIG_HOME=./.config\n")
        check = self.check()
        self.assertEqual((check.detail, check.fix),
                         (f"{client / '.env'} sets XDG_CONFIG_HOME, which would decide where wrangler keeps its "
                          f"profiles", f"remove XDG_CONFIG_HOME from {client / '.env'}   (Keyfit keeps wrangler on the "
                                       f"profiles it checks)"))
        (client / ".env").write_text("XDG_CONFIG_HOME=x\nWRANGLER_AUTH_URL=y\n")
        self.assertEqual(self.check().detail, f"{client / '.env'} sets WRANGLER_AUTH_URL, XDG_CONFIG_HOME, which would "
                                              f"decide where wrangler sends the profile's sign-in and where wrangler "
                                              f"keeps its profiles")
        self.assertEqual(self.wrangler.calls, [])

    def test_values_of_options_that_require_one_are_read_like_yargs(self):
        """wrangler declares --config, --cwd, --env, --profile and --script with requiresArg, which yargs reads like
        nargs 1: the next argument is the value unless it starts with `-` and a non-digit (KEY-74)."""
        for args, value in ((["--config", "-", "deploy"], "-"), (["--config", "-0x.toml", "deploy"], "-0x.toml"),
                            (["-c", "-0x.toml", "deploy"], "-0x.toml"), (["-ec", "-", "deploy"], "-"),
                            (["--config", "--minify", "deploy"], None), (["--config", "-x", "deploy"], None)):
            self.assertEqual([o for o in cloudflare.options(args) if o[0] == "--config"], [("--config", value)], args)
            self.assertEqual(cloudflare.words(args), ["deploy"], args)
        self.assertEqual(cloudflare.options(["--env", "-1", "--cwd", "-", "--profile", "-p", "deploy"]),
                         [("--env", "-1"), ("--cwd", "-"), ("--profile", None)])

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

    def test_dry_run_prints_a_project_without_cloudflare_instead_of_running_it(self):
        code, argv, _ = self.run_shim("deploy", cwd=self.repos["plain"], KEYFIT_DRY_RUN="1")
        self.assertEqual((code, argv), (0, None), self.stderr)  # never started
        self.assertIn("keyfit: project plain has no cloudflare account mapped: wrangler runs without checks, as before "
                      "Keyfit\n  would run: ", self.stderr)
        self.assertIn("wrangler deploy\n  environment changes (names only): ", self.stderr)

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

    def test_config_in_every_short_option_spelling_is_refused(self):
        """yargs' attached and grouped spellings of -c (KEY-59), alone and after --cwd: refused like `-c FILE`."""
        other = self.repos["client"] / "other.toml"
        other.write_text(f'account_id = "{GLOBEX_ID}"\n')
        app = self.repos["client"] / "app"
        app.mkdir()
        (app / "wrangler.toml").write_text(f'account_id = "{ACME_ID}"\n')
        (self.repos["mine"] / "wrangler.toml").write_text(f'account_id = "{GLOBEX_ID}"\n')
        spellings = (("attached absolute path", [f"-c{other}"], f"--config {other}"),
                     ("attached relative path", ["-ec./other.toml"], "--config ./other.toml"),
                     ("group ending in c", ["-ec", "other.toml"], "--config other.toml"),
                     ("-c=", ["-c=other.toml"], "--config other.toml"),
                     ("--c", ["--c", "other.toml"], "--config other.toml"))
        for what, spelling, shown in spellings:
            code, argv, _ = self.run_shim("deploy", *spelling)
            self.assertEqual((code, argv), (1, None), what)
            self.assertIn(f"refusing {shown}: {other} sets account_id {GLOBEX_ID}", self.stderr, what)
        moved = (("attached absolute path", [f"-c{self.repos['mine']}/wrangler.toml"],
                  f"--config {self.repos['mine']}/wrangler.toml", "uses wrangler profile globex"),
                 ("attached relative path", ["-ec../other.toml"], "--config ../other.toml",
                  f"{other} sets account_id {GLOBEX_ID}"),
                 ("group ending in c", ["-vc", "../../mine/wrangler.toml"], "--config ../../mine/wrangler.toml",
                  "uses wrangler profile globex"),
                 ("-c=", ["-c=../other.toml"], "--config ../other.toml", f"{other} sets account_id {GLOBEX_ID}"))
        for what, spelling, shown, why in moved:
            code, argv, _ = self.run_shim("--cwd", "app", "deploy", *spelling)
            self.assertEqual((code, argv), (1, None), what)
            self.assertIn(f"refusing --cwd app {shown}: ", self.stderr, what)
            self.assertIn(why, self.stderr, what)
        for spelling in (["-ec", "app/wrangler.toml"], ["-c=app/wrangler.toml"], [f"-c{app}/wrangler.toml"]):
            code, argv, _ = self.run_shim("deploy", *spelling)  # the project's own config: runs
            self.assertEqual(argv, ["wrangler", "deploy", *spelling], self.stderr)
        code, argv, _ = self.run_shim("dev", "-c", "app/wrangler.toml", "-c", "other.toml")  # dev takes several
        self.assertEqual(code, 1)
        self.assertIn(f"refusing --config other.toml: {other} sets account_id", self.stderr)

    def test_wrangler_runs_with_cloudflares_own_host_and_sign_in(self):
        for args in (["deploy"], ["auth", "create", "acme"], ["whoami"]):  # sign-ins too
            code, argv, env = self.run_shim(*args, **ELSEWHERE)
            self.assertEqual(argv, ["wrangler", *args], self.stderr)
            self.assertEqual(env["CLOUDFLARE_API_BASE_URL"], "https://api.cloudflare.com/client/v4")
            self.assertEqual(set(ELSEWHERE) & set(env), {"CLOUDFLARE_API_BASE_URL"}, args)
        code, argv, env = self.run_shim("dev", cwd=self.repos["plain"], **ELSEWHERE)  # no Cloudflare account: yours
        self.assertEqual(env["WRANGLER_TOKEN_URL"], "https://evil.example/token")

    def test_dotenv_with_the_api_host_or_sign_in_stops_wrangler_and_its_sign_ins(self):
        client = self.repos["client"]
        (client / ".env").write_text("WRANGLER_TOKEN_URL=https://evil.example/token\n")
        code, argv, _ = self.run_shim("deploy")
        self.assertEqual((code, argv), (1, None))
        self.assertIn(f"✘ cloudflare acme → Acme   {client / '.env'} sets WRANGLER_TOKEN_URL, which would decide where "
                      f"wrangler sends the profile's sign-in\n    → remove WRANGLER_TOKEN_URL from {client / '.env'}",
                      self.stderr)
        self.wrangler.state["acme"] = "expired"
        self.bind({})
        code, argv = self.run_in_terminal("deploy")  # nothing is offered while the file is there
        self.assertEqual((code, argv), (1, None))
        self.assertNotIn("now?", self.stderr)
        for args in (["auth", "create", "acme"], ["whoami"], ["login"], ["--cwd", ".", "auth", "list"]):
            code, argv, _ = self.run_shim(*args)  # sign-in commands run without checks, but not with these
            self.assertEqual((code, argv), (1, None), args)
            self.assertIn(f"keyfit: this folder belongs to client; refusing wrangler: {client / '.env'} sets "
                          f"WRANGLER_TOKEN_URL, which would decide where wrangler sends the profile's sign-in.\n"
                          f"  → remove WRANGLER_TOKEN_URL from {client / '.env'}", self.stderr)
        self.assertNotIn("evil", self.stderr)
        self.assertEqual(self.wrangler.calls, [])
        (self.repos["plain"] / ".env").write_text("WRANGLER_TOKEN_URL=https://evil.example/token\n")
        code, argv, _ = self.run_shim("dev", cwd=self.repos["plain"])  # no Cloudflare account: as before Keyfit
        self.assertEqual(argv, ["wrangler", "dev"])

    def test_dotenv_of_a_cwd_folder_or_an_env_file_is_read_too(self):
        client = self.repos["client"]
        app = client / "app"
        app.mkdir()
        (app / ".env.local").write_text("CLOUDFLARE_API_BASE_URL=https://evil.example/client/v4\n")
        (client / "custom.env").write_text("WRANGLER_AUTH_DOMAIN=evil.example\n")
        (client / "clean.env").write_text("API_KEY=x\n")
        for args, path in ((["--cwd", "app", "deploy"], app / ".env.local"),
                           (["deploy", "--env-file", "custom.env"], client / "custom.env"),
                           (["--env-file=clean.env", "--env-file", "custom.env", "--"], client / "custom.env"),
                           (["--cwd", "app", "auth", "create", "acme"], app / ".env.local")):
            code, argv, _ = self.run_shim(*args)
            self.assertEqual((code, argv), (1, None), args)
            self.assertIn(f"refusing wrangler: {path} sets ", self.stderr)
        code, argv, _ = self.run_shim("deploy", "--env-file", "clean.env")
        self.assertEqual(argv, ["wrangler", "deploy", "--env-file", "clean.env"], self.stderr)

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

    def test_a_shared_profile_can_be_signed_in_again_from_each_of_its_projects(self):
        reg = Registry.load(self.config / "registry.toml")
        reg.set_connection("plain", "cloudflare", "acme")  # two projects of the same client, one wrangler profile
        reg.save()
        self.bind({str(self.repos["client"]): "acme", str(self.repos["plain"]): "acme", str(self.repos["mine"]): "globex"})
        for repo in ("client", "plain"):
            code, argv, _ = self.run_shim("auth", "create", "acme", cwd=self.repos[repo])
            self.assertEqual(argv, ["wrangler", "auth", "create", "acme"], self.stderr)
        code, argv, _ = self.run_shim("auth", "create", "globex", cwd=self.repos["plain"])  # another project's: no
        self.assertEqual((code, argv), (1, None))
        self.assertIn("refusing `wrangler auth create globex`: that profile is the sign-in of mine", self.stderr)

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

    # Bind this folder now? (KEY-58) ---------------------------------------------------------------------------

    def run_in_terminal(self, *args, answers="\n", cwd=None, **extra):
        """The shim as when you type the command: a terminal on stdin and stderr, *answers* typed into it."""
        seen = {}

        def execve(path, argv, env):
            seen.update(argv=argv)
            raise Executed()
        os.chdir(cwd or self.repos["client"])
        err = Terminal()
        with mock.patch.dict(os.environ, extra), mock.patch.object(sys, "stdin", Terminal(answers)), \
                mock.patch("os.execve", execve), contextlib.redirect_stderr(err):
            try:
                code = shim.main(["wrangler", *args])
            except Executed:
                code = None
        self.stderr = err.getvalue()
        return code, seen.get("argv")

    def activations(self):
        return [call for call in self.wrangler.calls if call["argv"][:2] == ["auth", "activate"]]

    def superset_worktree(self, repo: str, name: str, holder: str | None = None) -> Path:
        """A Superset workspace of *repo*: a worktree at ~/.superset/worktrees/<holder, default repo>/<name>."""
        folder = self.home / ".superset" / "worktrees" / (holder or repo) / name
        git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com"]
        subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "x"], cwd=self.repos[repo], check=True)
        subprocess.run([*git, "worktree", "add", "-q", "-b", name, str(folder)], cwd=self.repos[repo], check=True)
        return folder

    def test_bind_this_folder_now_binds_it_and_starts_wrangler(self):
        client = self.repos["client"]
        self.bind({str(self.repos["mine"]): "globex"})
        code, argv = self.run_in_terminal("deploy", CLOUDFLARE_API_TOKEN="inherited")
        self.assertEqual((code, argv), (None, ["wrangler", "deploy"]), self.stderr)
        self.assertIn(f"    → wrangler auth activate acme {client}\n  Bind this folder now? [Y/n] ", self.stderr)
        self.assertEqual(self.bindings(), {str(self.repos["mine"]): "globex", str(client): "acme"})
        call, = self.activations()
        self.assertEqual(call["argv"], ["auth", "activate", "acme", str(client)])
        self.assertNotEqual(call["cwd"], str(client))  # away from the folder: no CLOUDFLARE_API_TOKEN from its .env
        self.assertNotIn("CLOUDFLARE_API_TOKEN", call["env"])
        self.assertIn(f"keyfit: bound {client} to wrangler profile acme, starting wrangler\n", self.stderr)
        self.assertNotIn("Sign in now?", self.stderr)

    def test_declined_binding_changes_nothing(self):
        self.bind({})
        code, argv = self.run_in_terminal("deploy", answers="n\n")
        self.assertEqual((code, argv), (1, None))
        self.assertEqual((self.bindings(), self.activations()), ({}, []))
        self.assertIn("Bind this folder now? [Y/n]   (bypass once: KEYFIT_DISABLE=1 wrangler …)\n", self.stderr)

    def test_agents_and_scripts_get_the_printed_fix(self):
        self.bind({})
        code, argv, _ = self.run_shim("deploy")  # no terminal
        self.assertEqual((code, argv), (1, None))
        self.assertTrue(self.stderr.endswith(f"→ wrangler auth activate acme {self.repos['client']}\n"
                                             f"  (bypass once: KEYFIT_DISABLE=1 wrangler …)\n"), self.stderr)
        code, argv = self.run_in_terminal("deploy", CI="1")
        self.assertEqual((code, argv), (1, None))
        self.assertNotIn("Bind", self.stderr)
        self.assertEqual((self.bindings(), self.activations()), ({}, []))

    def test_another_projects_folder_is_never_bound(self):
        # a session of client (KEYFIT_PROJECT) in mine's folder, in plain's (no Cloudflare) or outside every project
        before = self.bindings()
        for folder in (self.repos["mine"], self.repos["plain"], self.outside):
            code, argv = self.run_in_terminal("deploy", cwd=folder, KEYFIT_PROJECT="client")
            self.assertEqual((code, argv), (1, None), folder)
            self.assertIn(f"→ wrangler auth activate acme {folder}\n", self.stderr)
            self.assertNotIn("Bind", self.stderr)
        code, argv = self.run_in_terminal("deploy", cwd=self.home, KEYFIT_PROJECT="client")
        self.assertIn("→ run wrangler inside the project's folder", self.stderr)
        self.assertNotIn("Bind", self.stderr)
        self.assertEqual((self.bindings(), self.activations()), (before, []))

    def test_only_the_projects_profile_on_its_own_folder_below_home(self):
        client = self.repos["client"]
        reg = Registry.load(self.config / "registry.toml")
        project = reg.get("client")

        def binding(fix, **env):
            check = Check("cloudflare", "fail", "acme → Acme", "", fix)
            with mock.patch.dict(os.environ, env):
                return shim.binding_command(check, project, reg, dict(os.environ))
        self.assertEqual(binding(f"wrangler auth activate acme {client}"), ("acme", str(client)))
        for fix in (f"wrangler auth activate globex {client}", f"wrangler auth activate default {client}",
                    f"wrangler auth activate acme {client} --x", "wrangler auth activate acme client",
                    f"wrangler auth activate acme {client / 'missing'}", f"wrangler auth deactivate {client}",
                    f"wrangler auth activate acme {self.repos['mine']}", "wrangler auth activate acme /",
                    "wrangler auth create acme", f"wrangler auth activate acme '{client}"):
            self.assertIsNone(binding(fix), fix)
        sub = client / "home"
        sub.mkdir()
        # a home folder inside the project: neither it nor a folder above it (the project's own) is ever bound
        for fix in ("wrangler auth activate acme ~", f"wrangler auth activate acme {client}"):
            self.assertIsNone(binding(fix, HOME=str(sub)), fix)

    def test_after_the_binding_an_expired_sign_in_is_offered_too(self):
        self.bind({})
        self.wrangler.state["acme"] = "expired"  # shows only once the folder is bound and the online check runs
        ran = []
        real_run = subprocess.run

        def run(argv, *args, **kwargs):
            if kwargs.get("executable") == str(self.bin / "wrangler"):
                ran.append(list(argv))
                self.wrangler.state["acme"] = "ok"
                return subprocess.CompletedProcess(argv, 0)
            return real_run(argv, *args, **kwargs)
        with mock.patch("subprocess.run", run):
            code, argv = self.run_in_terminal("deploy", answers="\n\n")
        self.assertEqual((code, argv), (None, ["wrangler", "deploy"]), self.stderr)
        self.assertIn("Bind this folder now? [Y/n] keyfit: wrangler not started, client still needs attention\n",
                      self.stderr)
        self.assertIn("    → wrangler auth create acme\n  Sign in now? [Y/n] keyfit: signed in, starting wrangler\n",
                      self.stderr)
        self.assertEqual(ran, [["wrangler", "auth", "create", "acme"]])

    def test_a_fix_that_did_not_help_is_not_offered_again(self):
        self.bind({})
        self.wrangler.activation = "ignored"  # wrangler said yes and bound nothing
        code, argv = self.run_in_terminal("deploy", answers="\n\n")
        self.assertEqual((code, argv), (1, None))
        self.assertEqual(self.stderr.count("Bind this folder now?"), 1)
        self.assertIn("keyfit: wrangler not started, client still needs attention\n", self.stderr)

    def test_failed_binding_is_reported(self):
        self.bind({})
        self.wrangler.activation = "error"
        code, argv = self.run_in_terminal("deploy")
        self.assertEqual((code, argv), (1, None))
        self.assertIn('keyfit: wrangler auth activate did not finish: Profile "acme" does not exist. Run `wrangler auth '
                      'create acme` first.\n  (bypass once: KEYFIT_DISABLE=1 wrangler …)\n', self.stderr)

    def test_sign_ins_and_the_binding_are_asked_one_after_the_other(self):
        client = self.repos["client"]
        self.bind({})
        executable(self.bin / "claude", "#!/bin/sh\nexit 0\n")
        reg = Registry.load(self.config / "registry.toml")
        failed = [Check("claude", "fail", "Work", "not signed in", "claude auth login"),
                  Check("cloudflare", "fail", "acme → Acme", "not bound", f"wrangler auth activate acme {client}")]
        ran = []
        real_run = subprocess.run

        def run(argv, *args, **kwargs):
            if kwargs.get("executable") == str(self.bin / "claude"):
                ran.append(list(argv))
                return subprocess.CompletedProcess(argv, 0)
            return real_run(argv, *args, **kwargs)
        for answers, done, asked in (("n\n", None, "  Sign in now? [Y/n] "),
                                     ("\n\n", f"signed in, bound {client} to wrangler profile acme",
                                      "  Sign in now? [Y/n]   Bind this folder now? [Y/n] ")):
            err = Terminal()
            with mock.patch.object(sys, "stdin", Terminal(answers)), contextlib.redirect_stderr(err), \
                    mock.patch("subprocess.run", run):
                self.assertEqual(shim.offer_fixes(failed, reg.get("client"), dict(os.environ), reg, set()), done)
            self.assertEqual(err.getvalue(), asked)
        self.assertEqual(ran, [["claude", "auth", "login"]])
        self.assertEqual(self.bindings(), {str(client): "acme"})

    def test_superset_worktree_offers_the_folder_that_holds_them(self):
        workspace = self.superset_worktree("client", "ws1")
        holder = workspace.parent
        code, argv = self.run_in_terminal("deploy", cwd=workspace)
        self.assertEqual((code, argv), (None, ["wrangler", "deploy"]), self.stderr)
        self.assertIn("    → wrangler auth activate acme ~/.superset/worktrees/client/ws1\n  Bind "
                      "~/.superset/worktrees/client now, for every Superset worktree of client? [Y/n] ", self.stderr)
        self.assertIn("keyfit: bound ~/.superset/worktrees/client to wrangler profile acme, starting wrangler\n",
                      self.stderr)
        self.assertEqual(self.bindings()[str(holder)], "acme")
        self.assertNotIn(str(workspace), self.bindings())
        code, argv, _ = self.run_shim("deploy", cwd=self.superset_worktree("client", "ws2"))  # the next one just starts
        self.assertEqual((code, argv), (None, ["wrangler", "deploy"]), self.stderr)

    def test_superset_folder_holding_another_projects_worktree_binds_only_this_one(self):
        workspace = self.superset_worktree("client", "ws1")
        self.superset_worktree("mine", "stray", holder="client")
        code, argv = self.run_in_terminal("deploy", cwd=workspace)
        self.assertEqual((code, argv), (None, ["wrangler", "deploy"]), self.stderr)
        self.assertIn("    → wrangler auth activate acme ~/.superset/worktrees/client/ws1\n  Bind this folder now? [Y/n] ",
                      self.stderr)
        self.assertEqual(self.bindings()[str(workspace)], "acme")
        self.assertNotIn(str(workspace.parent), self.bindings())

    def test_dry_run_names_only_the_variables(self):
        code, _, _ = self.run_shim("deploy", KEYFIT_DRY_RUN="1", CLOUDFLARE_API_TOKEN="inherited")
        self.assertEqual(code, 0)
        self.assertIn("environment changes (names only): CLOUDFLARE_ACCOUNT_ID, CLOUDFLARE_API_BASE_URL, "
                      "CLOUDFLARE_API_TOKEN, KEYFIT_CLOUDFLARE",
                      self.stderr)
        self.assertNotIn("inherited", self.stderr)


    # Configs and binding commands Keyfit did not check (KEY-74) ------------------------------------------------

    def other_worker(self) -> Path:
        """A Worker outside every project whose config names another account: ../other/src/index.ts from client."""
        other = self.tmp / "other"
        (other / "src").mkdir(parents=True, exist_ok=True)
        (other / "src" / "index.ts").write_text("export default {}\n")
        (other / "wrangler.toml").write_text(f'account_id = "{GLOBEX_ID}"\n')
        return other

    def test_an_entry_file_whose_folder_has_another_config_is_refused(self):
        """`wrangler deploy ../other/src/index.ts` reads ../other/wrangler.toml, whose account_id beats the pinned
        CLOUDFLARE_ACCOUNT_ID; so do versions upload, dev and preview, and --script."""
        other = self.other_worker()
        for args in (["deploy", "../other/src/index.ts"], ["versions", "upload", "../other/src/index.ts"],
                     ["dev", "../other/src/index.ts"], ["preview", "../other/src/index.ts"],
                     ["deploy", "--script", "../other/src/index.ts"], ["deploy", "--path=../other/src/index.ts"],
                     ["deploy", "--name", "x", "../other/src/index.ts"],  # --name's value is taken for one too
                     ["d1", "list", "--script", "../other/src/index.ts"]):
            code, argv, _ = self.run_shim(*args)
            self.assertEqual((code, argv), (1, None), args)
            self.assertIn(f"refusing ../other/src/index.ts: {other / 'wrangler.toml'} sets account_id {GLOBEX_ID}, "
                          f"project expects Acme ({ACME_ID}).\n  → run wrangler on client's own folders and configs "
                          f"({other / 'src'} is outside every project)", self.stderr, args)
        (other / "public").mkdir()
        (self.repos["client"] / "src").mkdir()
        for args in (["deploy", "src/index.ts"], ["deploy", "../other/public"],  # a folder: static assets
                     ["versions", "upload", "../other/public"],
                     ["deploy", "-c", "wrangler.toml", "../other/src/index.ts"],
                     ["d1", "list", "../other/src/index.ts"],  # the config, not the entry file, decides
                     ["deploy", "--", "../other/src/index.ts"]):  # after `--`: not the entry file (yargs)
            code, argv, _ = self.run_shim(*args)
            self.assertEqual(argv, ["wrangler", *args], self.stderr)

    def test_cwd_through_a_symlink_is_resolved_like_wrangler(self):
        """wrangler moves to --cwd with process.chdir, so it reads the binding and the config of the folder a symlink
        points at; the advice never binds another project's folder."""
        client, mine = self.repos["client"], self.repos["mine"]
        (client / "link").symlink_to(mine)
        for args, shown in ((["--cwd", "link", "deploy"], "--cwd link"),
                            (["--cwd", "../mine", "deploy"], "--cwd ../mine")):
            code, argv, _ = self.run_shim(*args)
            self.assertEqual((code, argv), (1, None), args)
            self.assertIn(f"refusing {shown}: {mine} uses wrangler profile globex (bound at {mine}), project expects "
                          f"acme.\n  → run wrangler on client's own folders and configs ({mine} belongs to mine)\n",
                          self.stderr)
        (mine / ".env").write_text("WRANGLER_TOKEN_URL=https://evil.example/token\n")
        (client / "up").symlink_to(mine / "src", target_is_directory=True)
        (mine / "src").mkdir()
        code, argv, _ = self.run_shim("--cwd", "up/..", "deploy")  # client/up/.. is mine, not client
        self.assertEqual((code, argv), (1, None))
        self.assertIn(f"refusing wrangler: {mine / '.env'} sets WRANGLER_TOKEN_URL", self.stderr)
        (mine / ".env").unlink()
        code, argv, _ = self.run_shim("--cwd", "up/..", "auth", "activate", "acme")
        self.assertEqual((code, argv), (1, None))
        self.assertIn(f"refusing `wrangler auth activate acme` for {mine}: that folder belongs to mine", self.stderr)

    def test_binding_commands_outside_every_project_are_checked_too(self):
        client = self.repos["client"]
        rel = os.path.relpath(client, self.outside)
        activate = f"refusing `wrangler auth activate globex` for {client}: that folder belongs to client"
        for args, shown in ((["auth", "activate", "globex", str(client)], activate),
                            (["auth", "deactivate", str(client)], f"refusing `wrangler auth deactivate` for {client}: "
                                                                  f"client uses wrangler profile acme there"),
                            (["auth", "delete", "acme"],
                             "refusing `wrangler auth delete acme`: client uses that profile"),
                            (["--cwd", rel, "auth", "activate", "globex"], activate)):
            code, argv, _ = self.run_shim(*args, cwd=self.outside)
            self.assertEqual((code, argv), (1, None), args)
            self.assertIn(shown, self.stderr, args)
        for args in (["auth", "activate", "acme", str(client)], ["auth", "create", "acme"], ["auth", "list"], ["login"],
                     ["auth", "activate", "globex"], ["deploy"]):
            code, argv, env = self.run_shim(*args, cwd=self.outside, CLOUDFLARE_API_TOKEN="yours")
            self.assertEqual(argv, ["wrangler", *args], self.stderr)
            self.assertEqual(env["CLOUDFLARE_API_TOKEN"], "yours")  # outside every project: unchanged
        self.assertEqual(self.wrangler.calls, [])

    def test_a_folder_that_holds_a_projects_folder_is_checked(self):
        """`..`, ~/code or / hold projects' folders: binding them to another profile, or unbinding them, moves every
        folder that uses their binding (a folder bound itself keeps its own: the longest binding wins)."""
        client, tmp = self.repos["client"], self.tmp
        self.bind({str(tmp): "acme", str(self.repos["mine"]): "globex"})  # client is acme through the folder above
        for args, shown in ((["auth", "activate", "globex", ".."], "`wrangler auth activate globex`"),
                            (["auth", "deactivate", ".."], "`wrangler auth deactivate`"),
                            (["--cwd", "..", "auth", "activate", "globex"], "`wrangler auth activate globex`")):
            for cwd in (client, self.outside):
                code, argv, _ = self.run_shim(*args, cwd=cwd)
                self.assertEqual((code, argv), (1, None), (args, cwd))
                self.assertIn(f"refusing {shown} for {tmp}: it holds {client}, where client uses wrangler profile "
                              f"acme.", self.stderr)
        code, argv, _ = self.run_shim("auth", "activate", "acme", "..")  # the profile it has
        self.assertEqual(argv, ["wrangler", "auth", "activate", "acme", ".."], self.stderr)
        self.bind({str(client): "acme", str(self.repos["mine"]): "globex"})
        code, argv, _ = self.run_shim("auth", "activate", "globex", "..")  # client and mine are bound themselves
        self.assertEqual(argv, ["wrangler", "auth", "activate", "globex", ".."], self.stderr)
        reg = Registry.load(self.config / "registry.toml")
        reg.set_account("cloudflare", "own",
                        {"profile": "default", "account_id": INITECH_ID, "account_name": "Initech"})
        reg.set_connection("plain", "cloudflare", "own")  # on wrangler's default login: no binding of its own
        reg.save()
        code, argv, _ = self.run_shim("auth", "activate", "globex", "..")
        self.assertEqual((code, argv), (1, None))
        self.assertIn(f"it holds {self.repos['plain']}, where plain uses wrangler profile default.", self.stderr)

    def test_the_folder_that_holds_superset_worktrees_is_checked(self):
        """Binding ~/.superset/worktrees/<repo> covers every worktree of the repository (Bind … now? offers it)."""
        workspace = self.superset_worktree("client", "ws1")
        holder = workspace.parent
        self.bind({str(holder): "acme", str(self.repos["client"]): "acme", str(self.repos["mine"]): "globex"})
        for args in (["auth", "activate", "globex", str(holder)], ["auth", "deactivate", str(holder)]):
            for cwd in (self.repos["client"], self.outside, workspace):
                code, argv, _ = self.run_shim(*args, cwd=cwd)
                self.assertEqual((code, argv), (1, None), (args, cwd))
                self.assertIn(f"for ~/.superset/worktrees/client: it holds ~/.superset/worktrees/client/ws1, where "
                              f"client uses wrangler profile acme.", self.stderr)
        code, argv, _ = self.run_shim("auth", "activate", "acme", str(holder), cwd=self.outside)
        self.assertEqual(argv, ["wrangler", "auth", "activate", "acme", str(holder)], self.stderr)

    def test_env_and_config_values_are_read_like_wrangler(self):
        """`--env NAME` loads .env.NAME as path.resolve reads it (`--env /../x` loads x); `--config -0x.toml` names a
        file, as yargs reads options declared with requiresArg."""
        client = self.repos["client"]
        (client / "deploy-settings.txt").write_text("WRANGLER_TOKEN_URL=https://evil.example/token\n")
        for args in (["deploy", "--env", "/../deploy-settings.txt"], ["deploy", "-e=/../deploy-settings.txt"],
                     ["-e", "/../deploy-settings.txt", "auth", "create", "acme"]):
            code, argv, _ = self.run_shim(*args)
            self.assertEqual((code, argv), (1, None), args)
            self.assertIn(f"refusing wrangler: {client / 'deploy-settings.txt'} sets WRANGLER_TOKEN_URL", self.stderr)
        (client / "deploy-settings.txt").unlink()
        (client / "-0x.toml").write_text(f'account_id = "{GLOBEX_ID}"\n')
        for args in (["deploy", "--config", "-0x.toml"], ["deploy", "-c", "-0x.toml"]):
            code, argv, _ = self.run_shim(*args)
            self.assertEqual((code, argv), (1, None), args)
            self.assertIn(f"refusing --config -0x.toml: {client / '-0x.toml'} sets account_id {GLOBEX_ID}", self.stderr)
        (client / "w.yaml").write_text(f'account_id = "{GLOBEX_ID}"\n')
        code, argv, _ = self.run_shim("deploy", "--config", "w.yaml")  # wrangler reads no settings from it
        self.assertEqual(argv, ["wrangler", "deploy", "--config", "w.yaml"], self.stderr)

    def test_a_dotenv_that_moves_wranglers_config_folder_stops_its_sign_ins(self):
        """With XDG_CONFIG_HOME from the folder's .env, `wrangler auth create` (Sign in now?) would write the new
        sign-in where Keyfit does not look."""
        client = self.repos["client"]
        (client / ".env").write_text("XDG_CONFIG_HOME=./.config\n")
        for args in (["auth", "create", "acme"], ["deploy"]):
            code, argv, _ = self.run_shim(*args)
            self.assertEqual((code, argv), (1, None), args)
            self.assertIn(f"{client / '.env'} sets XDG_CONFIG_HOME, which would decide where wrangler keeps its "
                          f"profiles", self.stderr)
        self.assertEqual(self.wrangler.calls, [])

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
        client = self.repos["client"]
        self.bind({})
        with mock.patch.object(cli, "_ask", return_value="y"), mock.patch.object(sys, "stdin", Terminal("")), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(main(["add", "cloudflare", "--profile", "acme"]), 0)
        self.assertIn(f"→ wrangler auth activate acme {client}\n  bound {client} to wrangler profile acme\n", out.getvalue())
        self.assertEqual(self.bindings(), {str(client): "acme"})
        call = self.wrangler.calls[-1]  # the same runner as the shim's Bind this folder now?
        self.assertEqual(call["argv"], ["auth", "activate", "acme", str(client)])
        self.assertNotEqual(call["cwd"], str(client))

    def test_run_it_now_binds_the_project_not_the_checkout_above_it(self):
        """A project registered at a subfolder of a repository (~/mono/apps/web), whose checkout holds another
        project: the fix and `Run it now?` bind the project's own folder, never the checkout's top (KEY-74)."""
        mono = self.tmp / "mono"
        web, api = mono / "apps" / "web", mono / "apps" / "api"
        for folder in (web, api):
            folder.mkdir(parents=True)
        subprocess.run(["git", "init", "-q"], cwd=mono, check=True)
        reg = Registry.load(self.config / "registry.toml")
        reg.add_project("web", web)
        reg.add_project("api", api)
        reg.set_account("cloudflare", "globex", {"profile": "globex", "account_id": GLOBEX_ID, "account_name": "Globex"})
        reg.set_connection("api", "cloudflare", "globex")
        reg.save()
        self.bind({})
        os.chdir(web)
        with mock.patch.object(cli, "_ask", return_value="y"), mock.patch.object(sys, "stdin", Terminal("")), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(main(["add", "cloudflare", "--profile", "acme"]), 0)
        self.assertIn(f"  next: {web} is not bound to wrangler profile acme (wrangler would use its default login)\n"
                      f"  → wrangler auth activate acme {web}\n  bound {web} to wrangler profile acme\n",
                      out.getvalue())
        self.assertEqual(self.bindings(), {str(web): "acme"})
        self.bind({})
        os.chdir(self.outside)  # --project from elsewhere: the project's root, which binding fixes
        with mock.patch.object(cli, "_ask", return_value="y"), mock.patch.object(sys, "stdin", Terminal("")), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(main(["add", "cloudflare", "--profile", "acme", "--project", "web"]), 0)
        self.assertEqual(self.bindings(), {str(web): "acme"})

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
        self.assertIn(f"✔ wrangler   {self.bin / 'wrangler'}", out.getvalue())
        self.assertIn(f"✔ cloudflare acme → Acme   bound at {self.repos['client']} · account {ACME_ID}\n", out.getvalue())


if __name__ == "__main__":
    unittest.main()
