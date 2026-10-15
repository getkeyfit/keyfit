"""OpenCode: the adapter, its MCP hiding, `keyfit add opencode` and the opencode shim, with a fake opencode and
temporary SQLite databases shaped like OpenCode 2.0's. The real ~/.local/share/opencode, ~/.config/opencode, the
Keychain and the network are never touched."""

import contextlib
import io
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ["KEYFIT_CONFIG_DIR"] = tempfile.mkdtemp()  # tests never touch the real ~/.config/keyfit

from keyfit import mcp  # noqa: E402
from keyfit import environment  # noqa: E402
from keyfit.adapters.opencode import OpenCodeAdapter, default_db, scrub, signed_in  # noqa: E402
from keyfit.cli import main  # noqa: E402
from keyfit.registry import Project, Registry  # noqa: E402
from keyfit.shim import Refused, adjust_args, sign_in_command  # noqa: E402
from test_vercel import executable  # noqa: E402

SCHEMA = ("CREATE TABLE credential (id text PRIMARY KEY, integration_id text, label text NOT NULL, value text NOT NULL, "
          "connector_id text, method_id text, active integer, time_created integer NOT NULL, "
          "time_updated integer NOT NULL)")
SECRET = "sk-ant-never-shown-0000"  # a fake key in the database: no check may read or print it


def database(path: Path, *logins: tuple[str, str]) -> Path:
    """An OpenCode database (WAL, like OpenCode's) holding sign-ins (provider, label)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute(SCHEMA)
    for i, (provider, label) in enumerate(logins):
        con.execute("INSERT INTO credential VALUES (?, ?, ?, ?, NULL, NULL, 1, ?, ?)",
                    (f"cred_{i}", provider, label, json.dumps({"type": "key", "key": SECRET}), i, i))
    con.commit()
    con.close()
    return path


def add_login(path: Path, provider: str, label: str) -> None:
    con = sqlite3.connect(path)
    con.execute("INSERT INTO credential VALUES (?, ?, ?, '{}', NULL, NULL, 1, 99, 99)", (f"cred_{provider}", provider, label))
    con.commit()
    con.close()


# Fake opencode. `auth list --standalone --format json` lists the sign-ins of the database OPENCODE_DB names (else the
# default one), and provider keys from the environment, as OpenCode 2.0.25 does; it logs what it was started with.
# `auth login PROVIDER --standalone` adds a sign-in to that database. Anything else prints its argv and environment.
FAKE_OPENCODE = """#!/usr/bin/env python3
import json, os, sqlite3, sys
args = sys.argv[1:]
home = os.environ["HOME"]
data = os.environ.get("XDG_DATA_HOME") or os.path.join(home, ".local", "share")
db = os.environ.get("OPENCODE_DB") or os.path.join(data, "opencode", "opencode.db")
with open(os.path.join(home, "opencode-calls.log"), "a") as fh:
    fh.write(json.dumps({"argv": args, "cwd": os.getcwd(), "config_dir": os.environ.get("OPENCODE_CONFIG_DIR"),
                         "no_project_config": os.environ.get("OPENCODE_DISABLE_PROJECT_CONFIG"),
                         "content": os.environ.get("OPENCODE_CONFIG_CONTENT")}) + "\\n")
if args == ["auth", "list", "--standalone", "--format", "json"]:
    found = {}
    if os.path.exists(db):
        for provider, label in sqlite3.connect(db).execute("SELECT integration_id, label FROM credential"):
            found.setdefault(provider, []).append({"type": "credential", "id": "x", "label": label, "method": "key"})
    for var, provider in (("ANTHROPIC_API_KEY", "anthropic"), ("OPENAI_API_KEY", "openai"),
                          ("OPENROUTER_API_KEY", "openrouter"), ("CLOUDFLARE_ACCOUNT_ID", "cloudflare-ai-gateway")):
        if os.environ.get(var):
            found.setdefault(provider, []).append({"type": "env", "name": var})
    print(json.dumps([{"id": p, "name": p.title(), "connections": c} for p, c in sorted(found.items())]))
    sys.exit(0)
if args[:2] == ["auth", "login"] and args[-1] == "--standalone":
    con = sqlite3.connect(db)
    con.execute("INSERT INTO credential VALUES ('cred_new', ?, 'Fresh', '{}', NULL, NULL, 1, 7, 7)",
                (args[2] if len(args) == 4 else "anthropic",))
    con.commit()
    print("fake sign-in done")
    sys.exit(0)
print(json.dumps({"argv": args, "env": {k: os.environ.get(k) for k in (
    "OPENCODE_DB", "OPENCODE_CONFIG_CONTENT", "KEYFIT_OPENCODE_CONFIG", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY",
    "GITHUB_TOKEN", "AWS_PROFILE", "KEYFIT_PROJECT")}}))
"""

LINEAR = {"type": "remote", "url": "https://mcp.linear.app/mcp"}


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        executable(self.bin / "opencode", FAKE_OPENCODE)
        self.db = self.tmp / "accounts" / "work" / "opencode.db"
        self.conn = {"name": "work", "db": str(self.db), "logins": ["anthropic"]}
        self.adapter = OpenCodeAdapter(timeout=20)

    def env(self, **extra):
        return {"HOME": str(self.home), "PATH": f"{self.bin}{os.pathsep}/usr/bin:/bin", **extra}

    def verify(self, conn=None, **extra):
        conn = conn or self.conn
        env = self.env(**extra)
        self.adapter.inject(conn, env)
        return env, self.adapter.verify(conn, env)

    def calls(self):
        log = self.home / "opencode-calls.log"
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    def test_inject_points_at_the_database_and_scrub_removes_provider_keys(self):
        keys = {"ANTHROPIC_API_KEY": "sk-a", "OPENAI_API_KEY": "sk-o", "GEMINI_API_KEY": "g", "GOOGLE_API_KEY": "g",
                "GOOGLE_GENERATIVE_AI_API_KEY": "g", "GITHUB_TOKEN": "gh", "OPENCODE_API_KEY": "oc"}
        env = self.env(OPENCODE_DB="/elsewhere/opencode.db", OPENROUTER_API_KEY="kept", **keys)
        self.adapter.inject(self.conn, env)
        self.assertEqual(env["OPENCODE_DB"], str(self.db))
        self.assertEqual(env["KEYFIT_OPENCODE"], "work")
        self.assertLessEqual(keys.items(), env.items())  # Claude and Codex sessions of the project keep them
        scrub(env)  # where OpenCode starts
        self.assertFalse(set(keys) & set(env))
        self.assertEqual(env["OPENROUTER_API_KEY"], "kept")  # not one of the providers Keyfit scrubs

    def test_other_agents_sessions_keep_provider_keys(self):
        project = Project("demo", [str(self.tmp)], {"opencode": self.conn})
        env = environment.build(project, self.env(OPENAI_API_KEY="sk-o", GEMINI_API_KEY="g"))
        self.assertEqual((env["OPENAI_API_KEY"], env["GEMINI_API_KEY"]), ("sk-o", "g"))

    def test_default_database_follows_xdg_data_home(self):
        env = self.env()
        self.adapter.inject({"name": "default"}, env)
        self.assertEqual(env["OPENCODE_DB"], str(self.home / ".local" / "share" / "opencode" / "opencode.db"))
        self.assertEqual(default_db({"HOME": str(self.home), "XDG_DATA_HOME": str(self.tmp / "data")}),
                         self.tmp / "data" / "opencode" / "opencode.db")

    def test_forget_removes_only_what_keyfit_set(self):
        env = self.env()
        self.adapter.inject(self.conn, env)
        self.adapter.forget(env)
        self.assertNotIn("OPENCODE_DB", env)
        own = {"OPENCODE_DB": "/mine/opencode.db"}
        self.adapter.forget(own)
        self.assertEqual(own, {"OPENCODE_DB": "/mine/opencode.db"})

    def test_signed_in_database_is_confirmed_by_opencode(self):
        database(self.db, ("anthropic", "Anthropic"), ("openai", "OpenAI"))
        _, check = self.verify()
        self.assertEqual(check.status, "ok", check.detail)
        self.assertEqual(check.detail, "anthropic, openai signed in · confirmed by OpenCode")
        call = self.calls()[-1]
        self.assertEqual(call["argv"], ["auth", "list", "--standalone", "--format", "json"])
        # no configuration loaded, so no MCP server or plugin starts during a check
        self.assertEqual(call["no_project_config"], "1")
        self.assertEqual(Path(call["config_dir"]).resolve(), Path(call["cwd"]).resolve())
        self.assertFalse(Path(call["config_dir"]).exists())  # a temporary folder, gone after the check

    def test_no_database_gives_the_sign_in_command(self):
        _, check = self.verify()
        self.assertTrue(check.failed)
        self.assertEqual(check.detail, "not signed in (no OpenCode database yet)")
        self.assertEqual(check.fix, f"OPENCODE_DB={self.db} opencode auth login anthropic --standalone")
        self.assertEqual(sign_in_command(check.fix), ({"OPENCODE_DB": str(self.db)},
                                                      ["opencode", "auth", "login", "anthropic", "--standalone"]))

    def test_a_recorded_provider_signed_out_fails(self):
        database(self.db, ("openai", "OpenAI"))
        _, check = self.verify()
        self.assertTrue(check.failed)
        self.assertIn("not signed in to anthropic in", check.detail)
        self.assertEqual(self.calls(), [])  # the local check stops it; OpenCode is not asked

    def test_an_empty_database_fails(self):
        database(self.db, ("mcp_d6c8574dc5a7f608", "Linear"))  # an MCP server's sign-in is not a provider's
        _, check = self.verify(dict(self.conn, logins=[]))
        self.assertTrue(check.failed)
        self.assertIn("holds no sign-in", check.detail)
        self.assertEqual(check.fix, f"OPENCODE_DB={self.db} opencode auth login --standalone")

    def test_default_database_fix_has_no_variable(self):
        db = self.home / ".local" / "share" / "opencode" / "opencode.db"
        check = self.adapter.local({"name": "default", "logins": ["openai"]}, self.env())
        self.assertEqual(check.fix, "opencode auth login openai --standalone")
        self.assertFalse(db.exists())

    def test_shell_key_next_to_the_sign_in_fails(self):
        database(self.db, ("anthropic", "Anthropic"), ("openrouter", "OpenRouter"))
        # Keyfit scrubs the providers that matter; another provider's key from the shell competes with its sign-in
        _, check = self.verify(OPENROUTER_API_KEY="sk-or")
        self.assertTrue(check.failed)
        self.assertEqual(check.detail, "OPENROUTER_API_KEY from your shell would compete with the project's "
                                       "Openrouter sign-in")
        self.assertEqual(check.fix, "unset OPENROUTER_API_KEY (or remove it from your shell startup file)")

    def test_variables_of_providers_without_a_sign_in_are_left_alone(self):
        database(self.db, ("anthropic", "Anthropic"))
        _, check = self.verify(CLOUDFLARE_ACCOUNT_ID="0" * 32, ANTHROPIC_API_KEY="sk-a")
        self.assertEqual(check.status, "ok", check.detail)

    def test_opencode_must_see_the_sign_ins(self):
        database(self.db, ("anthropic", "Anthropic"))
        env = self.env()
        self.adapter.inject(self.conn, env)
        env["OPENCODE_DB"] = str(self.tmp / "other.db")  # as if OpenCode read another database
        check = self.adapter.remote(self.conn, env)
        self.assertTrue(check.failed)
        self.assertIn("OpenCode does not see the anthropic sign-in", check.detail)

    def test_an_opencode_error_is_not_offered_as_a_sign_in(self):
        database(self.db, ("anthropic", "Anthropic"))
        executable(self.bin / "opencode", "#!/bin/sh\necho 'Error: database is locked' >&2\nexit 1\n")
        _, check = self.verify()
        self.assertEqual(check.detail, "Error: database is locked")
        self.assertIsNone(sign_in_command(check.fix))

    def test_missing_opencode_fails_with_the_install_command(self):
        database(self.db, ("anthropic", "Anthropic"))
        _, check = self.verify(PATH="/usr/bin:/bin")
        self.assertTrue(check.failed)
        self.assertEqual((check.detail, check.fix), ("opencode not found on PATH", "brew install opencode"))

    def test_slow_opencode_is_a_network_kind_of_failure(self):
        database(self.db, ("anthropic", "Anthropic"))
        executable(self.bin / "opencode", "#!/bin/sh\nsleep 5\n")
        env = self.env()
        self.adapter.inject(self.conn, env)
        check = OpenCodeAdapter(timeout=0.5).remote(self.conn, env)
        self.assertTrue(check.failed and check.transient)

    def test_credentials_are_never_read(self):
        database(self.db, ("anthropic", "Anthropic"))
        self.assertEqual(signed_in(self.db), ({"anthropic": ["Anthropic"]}, ""))
        _, check = self.verify()
        self.assertNotIn(SECRET, json.dumps(check.__dict__))

    def test_fingerprint_changes_with_a_new_sign_in_and_a_new_key_variable(self):
        database(self.db, ("anthropic", "Anthropic"))
        env = self.env()
        first = self.adapter.fingerprint(self.conn, env)
        add_login(self.db, "openai", "OpenAI")
        second = self.adapter.fingerprint(self.conn, env)
        self.assertNotEqual(first, second)
        third = self.adapter.fingerprint(self.conn, {**env, "OPENROUTER_API_KEY": "x"})
        self.assertNotEqual(second, third)
        self.assertEqual(third, self.adapter.fingerprint(self.conn, {**env, "OPENROUTER_API_KEY": "y"}))  # names only


class McpTests(unittest.TestCase):
    """Which servers an OpenCode session sees: the same rules as Claude's and Codex's."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.home = self.tmp / "home"
        self.config = self.home / ".config" / "opencode"
        self.config.mkdir(parents=True)
        (self.config / "opencode.jsonc").write_text("""{
          // the user's servers
          "mcp": {
            "linear_a": {"type": "remote", "url": "https://mcp.linear.app/mcp",
                         "headers": {"Authorization": "Bearer {env:LINEAR_A_API_KEY}"}},
            "linear_b": {"type": "remote", "url": "https://mcp.linear.app/mcp",
                         "headers": {"Authorization": "Bearer {env:LINEAR_B_API_KEY}"}},
            "globex_cloud_eu": {"type": "local", "command": ["globex-mcp"]},
            "paper": {"type": "remote", "url": "http://127.0.0.1:29979/mcp"},
          },
        }""")
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        self.env = {"HOME": str(self.home)}
        self.registry = Registry(self.tmp / "registry.toml")
        self.registry.add_project("globex", self.tmp / "globex")
        self.registry.add_mcp_servers("globex", ["globex_cloud_*"])
        self.registry.add_project("acme", self.repo)
        self.registry.set_connection("acme", "linear", {"workspace": "a", "key": "env:LINEAR_A_API_KEY"})

    def hide(self, project="acme", env=None):
        env = dict(self.env if env is None else env)
        names = mcp.opencode_environment(self.registry.get(project), env, self.repo, self.registry.mcp_owners())
        return names, env

    def test_other_projects_servers_are_switched_off(self):
        names, env = self.hide()
        self.assertEqual(names, ["globex_cloud_eu", "linear_b"])
        content = json.loads(env["OPENCODE_CONFIG_CONTENT"])
        self.assertEqual(content, {"mcp": {name: {"type": "local", "command": ["false"], "enabled": False}
                                           for name in names}})
        self.assertEqual(env["KEYFIT_OPENCODE_CONFIG"], "")

    def test_owner_sees_its_servers(self):
        names, _ = self.hide("globex")
        self.assertEqual(names, ["linear_a", "linear_b"])  # globex has no Linear account

    def test_linear_server_in_the_folder_is_hidden_like_mcp_json(self):
        (self.repo / "opencode.json").write_text(json.dumps({"mcp": {"linear": {"type": "remote", "url": "https://mcp.linear.app/mcp",
                                                                                 "headers": {"Authorization": "Bearer {env:LINEAR_A_API_KEY}"}}}}))
        names, _ = self.hide()
        self.assertIn("linear", names)  # unverified: a repository may carry any key variable

    def test_folders_above_and_dot_opencode_count_too(self):
        (self.tmp / "opencode.json").write_text(json.dumps({"mcp": {"globex_cloud_us": {"type": "local", "command": ["x"]}}}))
        (self.repo / ".opencode").mkdir()
        (self.repo / ".opencode" / "opencode.jsonc").write_text(
            '{"mcp": {"servers": {"globex_cloud_ap": {"type": "local", "command": ["x"]},},},}')  # OpenCode 2's form
        names, _ = self.hide()
        self.assertEqual(names, ["globex_cloud_ap", "globex_cloud_eu", "globex_cloud_us", "linear_b"])

    def test_opencode_config_dir_replaces_the_users_folder(self):
        # Superset's wrapper sets OPENCODE_CONFIG_DIR, and OpenCode then reads only that folder
        superset = self.tmp / "superset-hooks"
        superset.mkdir()
        names, _ = self.hide(env={**self.env, "OPENCODE_CONFIG_DIR": str(superset)})
        self.assertEqual(names, [])

    def test_nothing_to_hide_leaves_no_content(self):
        (self.config / "opencode.jsonc").write_text('{"mcp": {"paper": {"type": "remote", "url": "http://x"}}}')
        names, env = self.hide()
        self.assertEqual(names, [])
        self.assertNotIn("OPENCODE_CONFIG_CONTENT", env)

    def test_your_own_content_is_kept_and_a_nested_session_starts_from_it(self):
        own = json.dumps({"model": "anthropic/claude", "mcp": {"docs": {"type": "remote", "url": "https://d"}}})
        _, env = self.hide(env={**self.env, "OPENCODE_CONFIG_CONTENT": own})
        content = json.loads(env["OPENCODE_CONFIG_CONTENT"])
        self.assertEqual(content["model"], "anthropic/claude")
        self.assertEqual(sorted(content["mcp"]), ["docs", "globex_cloud_eu", "linear_b"])
        self.assertEqual(env["KEYFIT_OPENCODE_CONFIG"], own)
        # an opencode started inside that session, in globex: from your own content, not acme's list
        names, nested = self.hide("globex", env=env)
        self.assertEqual(sorted(json.loads(nested["OPENCODE_CONFIG_CONTENT"])["mcp"]), ["docs", *names])
        self.assertNotIn("globex_cloud_eu", names)

    def test_content_that_is_not_an_object_stops_the_start(self):
        with self.assertRaises(mcp.Unreadable):
            self.hide(env={**self.env, "OPENCODE_CONFIG_CONTENT": "[1, 2]"})


class ShimTests(unittest.TestCase):
    """The opencode shim written by `keyfit setup`, run as a real process in temporary folders."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.home = self.tmp / "home"
        (self.home / ".aws").mkdir(parents=True)
        (self.home / ".aws" / "config").write_text("[profile iam]\ncredential_process = granted credential-process --profile=iam\n")
        config = self.home / ".config" / "opencode"
        config.mkdir(parents=True)
        (config / "opencode.json").write_text(json.dumps({"mcp": {
            "globex_cloud_eu": {"type": "local", "command": ["globex-mcp"]}, "paper": {"type": "remote", "url": "http://p"}}}))
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        executable(self.bin / "opencode", FAKE_OPENCODE)
        executable(self.bin / "aws", "#!/bin/sh\necho 444444444444\n")
        self.config = self.tmp / "keyfit"
        self.repo = self.tmp / "repo"
        self.other = self.tmp / "other"
        self.outside = self.tmp / "outside"
        for folder in (self.repo, self.other, self.outside):
            folder.mkdir()
        for folder in (self.repo, self.other):
            subprocess.run(["git", "init", "-q"], cwd=folder, check=True)
        self.db = self.config / "opencode" / "work" / "opencode.db"
        with mock.patch.dict(os.environ, {"KEYFIT_CONFIG_DIR": str(self.config)}):
            with contextlib.redirect_stdout(io.StringIO()):
                main(["init", "--yes", "--root", str(self.repo), "--name", "demo"])
                main(["init", "--yes", "--root", str(self.other), "--name", "globex"])
                with mock.patch.object(sys, "platform", "darwin"):
                    main(["setup"])
        reg = self.config / "registry.toml"
        text = reg.read_text().replace(
            '[projects.demo]\nroots = ["' + str(self.repo) + '"]\n',
            '[projects.demo]\nroots = ["' + str(self.repo) + '"]\nopencode = "work"\n'
            'aws = { profile = "iam", account = "444444444444" }\n')
        text = text.replace('[projects.globex]\nroots = ["' + str(self.other) + '"]\n',
                            '[projects.globex]\nroots = ["' + str(self.other) + '"]\nmcp_servers = ["globex_cloud_*"]\n')
        reg.write_text(text + f'\n[accounts.opencode.work]\ndb = "{self.db}"\nlogins = ["anthropic"]\n')

    def run_tool(self, *args, cwd=None, **extra):
        env = {"PATH": f"{self.config / 'bin'}{os.pathsep}{self.bin}{os.pathsep}/usr/bin:/bin",
               "HOME": str(self.home), "KEYFIT_CONFIG_DIR": str(self.config), **extra}
        return subprocess.run(["opencode", *args], cwd=cwd or self.repo, env=env, capture_output=True, text=True,
                              timeout=60)

    def started(self, out):
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout.splitlines()[-1])

    def test_session_starts_with_the_projects_accounts_on_a_private_server(self):
        database(self.db, ("anthropic", "Anthropic"))
        seen = self.started(self.run_tool(ANTHROPIC_API_KEY="sk-inherited", AWS_PROFILE="other"))
        self.assertEqual(seen["argv"], ["--standalone"])
        env = seen["env"]
        self.assertEqual((env["OPENCODE_DB"], env["KEYFIT_PROJECT"], env["AWS_PROFILE"]), (str(self.db), "demo", "iam"))
        self.assertIsNone(env["ANTHROPIC_API_KEY"])
        self.assertEqual(sorted(json.loads(env["OPENCODE_CONFIG_CONTENT"])["mcp"]), ["globex_cloud_eu"])

    def test_run_and_its_message(self):
        database(self.db, ("anthropic", "Anthropic"))
        self.assertEqual(self.started(self.run_tool("run", "fix", "it"))["argv"], ["run", "fix", "it", "--standalone"])
        self.assertEqual(self.started(self.run_tool("run", "--", "--standalone"))["argv"],
                         ["run", "--standalone", "--", "--standalone"])  # after --, words are the message

    def test_not_signed_in_blocks_with_the_fix(self):
        out = self.run_tool("run", "hi")
        self.assertEqual((out.returncode, out.stdout), (1, ""))
        self.assertIn("keyfit: opencode not started, demo needs attention\n"
                      "  ✘ opencode   work   not signed in (no OpenCode database yet)\n"
                      f"    → OPENCODE_DB={self.db} opencode auth login anthropic --standalone\n", out.stderr)
        self.assertNotIn("Sign in now?", out.stderr)  # no terminal: nothing is asked

    def test_sign_in_goes_to_the_projects_database_without_checks(self):
        self.db.parent.mkdir(parents=True)
        database(self.db)
        out = self.run_tool("auth", "login", "openai")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout, "fake sign-in done\n")
        self.assertEqual(signed_in(self.db)[0], {"openai": ["Fresh"]})

    def test_expired_sign_in_is_offered_in_a_terminal(self):
        import pty
        import select

        env = {"PATH": f"{self.config / 'bin'}{os.pathsep}{self.bin}{os.pathsep}/usr/bin:/bin",
               "HOME": str(self.home), "KEYFIT_CONFIG_DIR": str(self.config)}
        self.db.parent.mkdir(parents=True)
        database(self.db)
        master, slave = pty.openpty()
        proc = subprocess.Popen(["opencode", "run", "hi"], cwd=self.repo, env=env, stdin=slave,
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
        self.assertIn("    → OPENCODE_DB=", terminal)
        self.assertIn("keyfit: signed in, starting opencode\n", terminal)
        self.assertEqual(json.loads(out.splitlines()[-1])["argv"], ["run", "hi", "--standalone"])

    def test_background_server_commands_run_unchanged(self):
        out = self.run_tool("mcp", "list", ANTHROPIC_API_KEY="sk-kept")
        seen = self.started(out)
        self.assertEqual(seen["argv"], ["mcp", "list"])
        self.assertEqual((seen["env"]["OPENCODE_DB"], seen["env"]["ANTHROPIC_API_KEY"]), (None, "sk-kept"))

    def test_dry_run_prints_opencodes_own_commands_instead_of_running_them(self):
        for cwd in (self.repo, self.outside):  # decided before the folder is looked at
            out = self.run_tool("serve", "--port", "4096", cwd=cwd, KEYFIT_DRY_RUN="1")
            self.assertEqual((out.returncode, out.stdout), (0, ""), out.stderr)  # no server started
            self.assertEqual(out.stderr, "keyfit: opencode serve is OpenCode's own command: it runs without a "
                                         "project's accounts or checks\n"
                                         f"  would run: {self.bin / 'opencode'} serve --port 4096\n"
                                         "  environment changes (names only): none\n")

    def test_inside_a_session_the_background_server_never_gets_the_projects_environment(self):
        # a session's environment holds its project's accounts; a background server started from it would keep them
        # for every folder
        session = {"KEYFIT_PROJECT": "demo", "OPENCODE_DB": str(self.db), "KEYFIT_OPENCODE": "work"}
        for args in (["mcp", "list"], ["debug", "config"], ["service", "start"]):
            out = self.run_tool(*args, **session)
            self.assertEqual((out.returncode, out.stdout), (1, ""), args)
            self.assertIn("could start OpenCode's background server with that project's accounts", out.stderr)
        self.assertEqual(self.started(self.run_tool("api", "GET", "/api/config", **session))["argv"],
                         ["api", "GET", "/api/config", "--standalone"])
        self.assertEqual(self.started(self.run_tool("upgrade", **session))["argv"], ["upgrade"])
        # a folder outside every project, inside the session: a private server too
        self.assertEqual(self.started(self.run_tool("run", "hi", cwd=self.outside, **session))["argv"],
                         ["run", "hi", "--standalone"])
        self.assertEqual(self.started(self.run_tool("--server", "http://x", cwd=self.outside, **session))["argv"],
                         ["--server", "http://x"])  # connects to the server named; starts none
        out = self.run_tool("acp", "--login", cwd=self.outside, **session)
        self.assertEqual((out.returncode, out.stdout), (1, ""))

    def test_unregistered_folder_runs_unchanged(self):
        seen = self.started(self.run_tool(cwd=self.outside, ANTHROPIC_API_KEY="sk-kept"))
        self.assertEqual(seen["argv"], [])
        self.assertEqual(seen["env"]["ANTHROPIC_API_KEY"], "sk-kept")

    def test_another_server_is_refused(self):
        database(self.db, ("anthropic", "Anthropic"))
        for args in (["--server", "http://127.0.0.1:4096"], ["auth", "login", "--server=http://127.0.0.1:4096"]):
            out = self.run_tool(*args)
            self.assertEqual((out.returncode, out.stdout), (1, ""), args)
            self.assertIn("refusing --server", out.stderr)

    def test_acp_login_is_refused_in_a_project(self):
        database(self.db, ("anthropic", "Anthropic"))
        out = self.run_tool("acp", "--login")
        self.assertEqual((out.returncode, out.stdout), (1, ""))
        self.assertIn("Sign in with: opencode auth login", out.stderr)
        self.assertEqual(self.started(self.run_tool("acp"))["argv"], ["acp"])

    def test_servers_are_hidden_for_the_folder_opencode_opens(self):
        database(self.db, ("anthropic", "Anthropic"))
        sub = self.repo / "web"
        sub.mkdir()
        (sub / "opencode.json").write_text(json.dumps({"mcp": {"globex_cloud_web": {"type": "local", "command": ["x"]}}}))
        seen = self.started(self.run_tool("web"))
        self.assertEqual(sorted(json.loads(seen["env"]["OPENCODE_CONFIG_CONTENT"])["mcp"]),
                         ["globex_cloud_eu", "globex_cloud_web"])

    def test_a_directory_of_another_project_is_refused(self):
        database(self.db, ("anthropic", "Anthropic"))
        for folder, cwd in ((self.other, self.repo), (self.repo, self.outside)):
            out = self.run_tool(str(folder), cwd=cwd)
            self.assertEqual((out.returncode, out.stdout), (1, ""), folder)
            self.assertIn(f"Start it there: cd {folder} && opencode", out.stderr)
        sub = self.repo / "src"
        sub.mkdir()
        self.assertEqual(self.started(self.run_tool(str(sub)))["argv"], [str(sub), "--standalone"])

    def test_project_without_an_opencode_account_forgets_another_projects_database(self):
        seen = self.started(self.run_tool(cwd=self.other, OPENCODE_DB=str(self.db), KEYFIT_OPENCODE="work"))
        self.assertIsNone(seen["env"]["OPENCODE_DB"])
        self.assertEqual(seen["argv"], ["--standalone"])
        self.assertIsNone(seen["env"]["OPENCODE_CONFIG_CONTENT"])  # globex owns its server: nothing to hide


class ArgumentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.project = Project("demo", [str(self.tmp)], {})
        self.env = {"HOME": str(self.tmp)}

    def args(self, *given):
        return adjust_args("opencode", self.project, list(given), dict(self.env), self.tmp)

    def test_standalone_only_where_opencode_takes_it(self):
        self.assertEqual(self.args("-c"), ["-c", "--standalone"])
        self.assertEqual(self.args("models"), ["models", "--standalone"])
        self.assertEqual(self.args("session", "list"), ["session", "list", "--standalone"])
        self.assertEqual(self.args("session"), ["session"])  # its help takes no --standalone
        self.assertEqual(self.args("acp"), ["acp"])  # always a private server
        self.assertEqual(self.args("--standalone", "run", "x"), ["--standalone", "run", "x"])

    def test_server_option_is_refused(self):
        for given in (["--server", "http://x"], ["run", "--server=http://x", "hi"]):
            with self.assertRaises(Refused):
                self.args(*given)
        self.assertEqual(self.args("run", "--", "--server"), ["run", "--standalone", "--", "--server"])


class AddTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.home = self.tmp / "home"
        self.repo = self.tmp / "acme"
        self.repo.mkdir(parents=True)
        self.config = self.tmp / "keyfit"
        for patcher in (mock.patch.dict(os.environ, {"HOME": str(self.home), "KEYFIT_CONFIG_DIR": str(self.config),
                                                     "PATH": "/usr/bin:/bin"}, clear=True),
                        mock.patch.object(Path, "home", return_value=self.home)):
            patcher.start()
            self.addCleanup(patcher.stop)
        cwd = os.getcwd()
        os.chdir(self.repo)
        self.addCleanup(os.chdir, cwd)
        with contextlib.redirect_stdout(io.StringIO()):
            main(["init", "--yes", "--name", "acme"])

    def run_main(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def registry(self):
        import tomllib

        return tomllib.loads((self.config / "registry.toml").read_text())

    def test_records_the_providers_of_the_default_database(self):
        database(self.home / ".local" / "share" / "opencode" / "opencode.db", ("openai", "OpenAI"), ("anthropic", "Anthropic"))
        code, out, err = self.run_main("add", "opencode")
        self.assertEqual(code, 0, err)
        self.assertEqual(out, "acme: opencode account 'default' → anthropic, openai in "
                              "~/.local/share/opencode/opencode.db\n")
        reg = self.registry()
        self.assertEqual(reg["projects"]["acme"]["opencode"], "default")
        self.assertEqual(reg["accounts"]["opencode"]["default"],
                         {"db": "~/.local/share/opencode/opencode.db", "logins": ["anthropic", "openai"]})

    def test_no_sign_ins_names_the_sign_in(self):
        code, out, err = self.run_main("add", "opencode")
        self.assertEqual((code, out), (1, ""))
        self.assertIn("no OpenCode sign-ins in ~/.local/share/opencode/opencode.db (not signed in (no OpenCode "
                      "database yet))\n  sign in with: opencode auth login --standalone\n", err)

    def test_new_creates_a_private_folder_for_other_sign_ins(self):
        code, out, err = self.run_main("add", "opencode", "--new", "--name", "globex")
        self.assertEqual(code, 0, err)
        folder = self.config / "opencode" / "globex"
        self.assertEqual(folder.stat().st_mode & 0o777, 0o700)
        self.assertIn(f"  OPENCODE_DB={folder / 'opencode.db'} opencode auth login --standalone\n"
                      f"  keyfit add opencode --name globex --db {folder / 'opencode.db'}\n", out)
        self.assertEqual(self.registry()["accounts"]["opencode"]["globex"], {"db": str(folder / "opencode.db")})
        database(folder / "opencode.db", ("anthropic", "Anthropic"))
        code, out, _ = self.run_main("add", "opencode", "--name", "globex", "--db", str(folder / "opencode.db"))
        self.assertEqual(code, 0)
        self.assertEqual(self.registry()["accounts"]["opencode"]["globex"]["logins"], ["anthropic"])

    def test_init_offers_the_sign_ins_opencode_has(self):
        from keyfit import cli

        bin_ = self.tmp / "bin"
        bin_.mkdir()
        executable(bin_ / "opencode", "#!/bin/sh\nexit 97\n")
        os.environ["PATH"] = f"{bin_}{os.pathsep}/usr/bin:/bin"
        base = {"registry": None, "project": "acme"}
        with mock.patch.object(cli, "_ask", return_value="y") as ask, contextlib.redirect_stdout(io.StringIO()) as out:
            cli._offer_opencode("acme", base)
        self.assertEqual(out.getvalue(), "OpenCode: no sign-ins in ~/.local/share/opencode/opencode.db — sign in "
                                         "(opencode auth login --standalone), then: keyfit add opencode\n")
        ask.assert_not_called()
        database(self.home / ".local" / "share" / "opencode" / "opencode.db", ("anthropic", "Anthropic"))
        with mock.patch.object(cli, "_ask", return_value="y") as ask, contextlib.redirect_stdout(io.StringIO()) as out:
            cli._offer_opencode("acme", base)
        ask.assert_called_once_with("Use these OpenCode sign-ins for acme? [y/N] ")
        self.assertIn("OpenCode: signed in to anthropic in ~/.local/share/opencode/opencode.db\n", out.getvalue())
        self.assertEqual(self.registry()["projects"]["acme"]["opencode"], "default")

    def test_doctor_shows_the_projects_database(self):
        database(self.home / ".local" / "share" / "opencode" / "opencode.db", ("anthropic", "Anthropic"))
        self.run_main("add", "opencode")
        _, out, _ = self.run_main("doctor")
        self.assertIn("  ✔ opencode   default   anthropic signed in · ~/.local/share/opencode/opencode.db\n", out)
        self.assertIn("  ! opencode   not found on PATH   needed only if you use it\n    → brew install opencode\n", out)


if __name__ == "__main__":
    unittest.main()
