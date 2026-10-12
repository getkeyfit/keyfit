"""Storing keys through Keyfit: hidden input → Keychain, with a stub `security` (the real Keychain is never written)."""

import io
import json
import os
import subprocess
import sys
import tempfile
import tomllib
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from keyfit import secrets
from keyfit.adapters import ADAPTERS
from keyfit.cli import main
from keyfit.registry import Registry

os.environ["KEYFIT_CONFIG_DIR"] = tempfile.mkdtemp()  # tests never touch the real ~/.config/keyfit

KEY = "lin_api_" + "Q7" * 20  # fake; never a real key
OTHER = "lin_api_" + "Z9" * 20

# Stands in for /usr/bin/security: logs every argv, keeps items in keychain.json next to itself,
# and parses `security -i` lines with a port of split_line() from Apple's SecurityTool/macOS/security.c.
FAKE_SECURITY = r'''#!/usr/bin/env python3
import json, os, sys
here = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(here, "argv.log"), "a") as fh:
    fh.write(json.dumps(sys.argv[1:]) + "\n")
path = os.path.join(here, "keychain.json")
items = json.load(open(path)) if os.path.exists(path) else {}

def split_line(line):
    args, cur, state, quote = [], "", "ws", ""
    for ch in line:
        if state == "ws":
            if ch in " \t\n\v\f\r":
                continue
            if ch in "\"'":
                state, quote, cur = "quoted", ch, ""
                continue
            state, cur = "arg", ""
        if state == "arg":
            if ch == "\\": state = "arg_esc"
            elif ch in " \t\n\v\f\r": args.append(cur); state = "ws"
            else: cur += ch
        elif state == "quoted":
            if ch == "\\": state = "quoted_esc"
            elif ch == quote: args.append(cur); state = "ws"
            else: cur += ch
        elif state == "arg_esc": cur += ch; state = "arg"
        elif state == "quoted_esc": cur += ch; state = "quoted"
    if state != "ws":
        args.append(cur)
    return args

args = sys.argv[1:]
if args == ["-i"]:
    line = sys.stdin.readline()
    if not line.endswith("\n"):
        sys.exit(0)  # like the real tool: a last line without newline is dropped
    args = split_line(line[:-1])
opts, flags, i = {}, set(), 1
while i < len(args):
    if args[i] in ("-U", "-w") and (args[i] == "-U" or i + 1 == len(args)):
        flags.add(args[i]); i += 1
    else:
        opts[args[i]] = args[i + 1]; i += 2
item = opts.get("-s", "") + "/" + opts.get("-a", "")
if args[0] == "add-generic-password":
    if item in items and "-U" not in flags:
        sys.exit(45)
    items[item] = opts["-w"]
    json.dump(items, open(path, "w"))
    sys.exit(0)
if args[0] == "find-generic-password":
    if item not in items:
        sys.exit(44)
    if "-w" in flags:
        print(items[item])
    sys.exit(0)
sys.exit(2)
'''


def fake_fetch(table, calls):
    def fetch(key, timeout):
        calls.append(key)
        if key not in table:
            return 401, {"errors": [{"message": "Authentication required"}]}
        return 200, {"data": {"organization": {"urlKey": table[key], "name": table[key].title()}}}
    return fetch


class TTY:
    """A terminal on stdin: the key must come from getpass, never from reading stdin."""
    def isatty(self):
        return True

    def readline(self):
        raise AssertionError("stdin read although it is a terminal")


class StoreKeysTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.config = self.tmp / "keyfit"
        self.stub = self.tmp / "stub"
        self.stub.mkdir()
        fake = self.stub / "security"
        fake.write_text(FAKE_SECURITY)
        fake.chmod(0o755)
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=self.repo, check=True)
        # a clean environment: no real keys from the shell can reach any check
        env = {"PATH": os.environ["PATH"], "HOME": str(self.tmp), "KEYFIT_CONFIG_DIR": str(self.config)}
        for patcher in (mock.patch.dict(os.environ, env, clear=True),
                        mock.patch.object(secrets, "SECURITY", str(fake)),
                        mock.patch.object(sys, "platform", "darwin")):  # the stub stands in for macOS anywhere
            patcher.start()
            self.addCleanup(patcher.stop)
        self.fetched = []
        patcher = mock.patch.object(ADAPTERS["linear"], "fetch", fake_fetch({KEY: "acme", OTHER: "other"}, self.fetched))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.spawned = []
        spawned, real = self.spawned, subprocess.Popen

        class Recording(real):
            def __init__(self, args, *a, **kw):
                spawned.append([str(x) for x in args] if isinstance(args, (list, tuple)) else [str(args)])
                super().__init__(args, *a, **kw)

        patcher = mock.patch("subprocess.Popen", Recording)
        patcher.start()
        self.addCleanup(patcher.stop)
        old = os.getcwd()
        os.chdir(self.repo)
        self.addCleanup(os.chdir, old)
        self.keyfit("init", "--name", "demo")

    def keyfit(self, *argv, stdin=None, password=None):
        out, err = io.StringIO(), io.StringIO()
        prompts = []

        def getpass(prompt="Password: "):
            prompts.append(prompt)
            return password

        with mock.patch("sys.stdin", stdin if stdin is not None else io.StringIO("")), \
                mock.patch("getpass.getpass", getpass), redirect_stdout(out), redirect_stderr(err):
            code = main(list(argv))
        self.prompts = prompts
        return code, out.getvalue(), err.getvalue()

    def keychain(self) -> dict:
        path = self.stub / "keychain.json"
        return json.loads(path.read_text()) if path.exists() else {}

    def security_calls(self) -> list:
        path = self.stub / "argv.log"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def registry(self) -> dict:
        return tomllib.loads((self.config / "registry.toml").read_text())

    def assert_never_leaked(self, value, *outputs):
        for argv in self.spawned + self.security_calls():
            self.assertFalse(any(value in a for a in argv), f"value in argv of {argv[0]}")
        for text in outputs:
            self.assertNotIn(value, text)
        for path in self.config.rglob("*"):
            if path.is_file():
                self.assertNotIn(value.encode(), path.read_bytes(), f"value in {path}")
        self.assertTrue(self.spawned and (self.config / "registry.toml").is_file())

    def test_paste_key_store_it_and_status_confirms_the_workspace(self):
        code, out, err = self.keyfit("add", "linear", stdin=TTY(), password=KEY)
        self.assertEqual(code, 0, err)
        self.assertIn("demo: linear acme via Keychain item keyfit/linear-acme", out)
        self.assertEqual(self.prompts, ["Linear API key (input hidden): "])
        self.assertEqual(self.fetched, [KEY])  # verified with Linear before storing
        self.assertEqual(self.keychain(), {"keyfit/linear-acme": KEY})
        self.assertIn(["-i"], self.security_calls())  # the value went in through stdin
        reg = self.registry()
        self.assertEqual(reg["projects"]["demo"]["linear"], {"workspace": "acme", "key": "keychain:keyfit/linear-acme"})
        self.assertEqual(reg["secrets"]["keychain"], ["linear-acme"])

        code, status, err2 = self.keyfit("status", str(self.repo))
        self.assertEqual(code, 0, status + err2)
        self.assertRegex(status, r"✔ linear\s+acme\s+workspace confirmed")
        self.assert_never_leaked(KEY, out, err, status, err2, *self.prompts)

    def test_stdin_that_is_not_a_terminal_gives_the_key(self):
        code, out, err = self.keyfit("add", "linear", "--workspace", "acme", stdin=io.StringIO(KEY + "\nignored\n"))
        self.assertEqual(code, 0, err)
        self.assertEqual(self.prompts, [])
        self.assertEqual(self.keychain(), {"keyfit/linear-acme": KEY})
        self.assert_never_leaked(KEY, out, err)

    def test_key_of_another_workspace_is_refused_without_storing(self):
        code, out, err = self.keyfit("add", "linear", "--workspace", "acme", stdin=io.StringIO(OTHER + "\n"))
        self.assertEqual(code, 1)
        self.assertIn("belongs to workspace 'other', not 'acme'", err)
        self.assertEqual(self.keychain(), {})
        self.assertEqual(self.security_calls(), [])
        self.assertNotIn("linear", self.registry()["projects"]["demo"])
        self.assert_never_leaked(OTHER, out, err)

    def test_something_else_pasted_is_never_sent_to_linear(self):
        code, out, err = self.keyfit("add", "linear", stdin=io.StringIO("ghp_notalinearkey\n"))
        self.assertEqual(code, 1)
        self.assertIn("not a Linear API key", err)
        code, _, err = self.keyfit("add", "linear", stdin=io.StringIO(""))
        self.assertIn("no key given", err)
        with mock.patch("getpass.getpass", side_effect=KeyboardInterrupt), mock.patch("sys.stdin", TTY()), \
                redirect_stderr(io.StringIO()) as cancelled:
            self.assertEqual(main(["add", "linear"]), 1)
        self.assertIn("no key given", cancelled.getvalue())
        self.assertEqual(self.fetched, [])
        self.assertEqual(self.security_calls(), [])

    def test_secret_set_quotes_any_printable_value_and_list_shows_names_only(self):
        value = 'p"a s\'s\\w0rd\\ $(x) `y` #z -w "'
        code, out, err = self.keyfit("secret", "set", "OPENAI_API_KEY", stdin=TTY(), password=value)
        self.assertEqual(code, 0, err)
        self.assertIn("keychain:keyfit/OPENAI_API_KEY", out)
        self.assertEqual(self.prompts, ["OPENAI_API_KEY (input hidden): "])
        self.assertEqual(self.keychain(), {"keyfit/OPENAI_API_KEY": value})
        self.assertEqual(secrets.resolve("keychain:keyfit/OPENAI_API_KEY", {}), value)
        self.keyfit("add", "linear", stdin=io.StringIO(KEY + "\n"))
        code, listing, err2 = self.keyfit("secret", "list")
        self.assertEqual(code, 0)
        self.assertEqual([line.split()[0] for line in listing.splitlines()], ["OPENAI_API_KEY", "linear-acme"])
        self.assertNotIn("missing", listing)
        self.assert_never_leaked(value, out, err, listing, err2)
        self.assert_never_leaked(KEY, listing, err2)

    def test_values_the_parser_cannot_carry_are_refused_before_security_runs(self):
        for bad in ("two\nlines", "trailing\n", "tab\there", "cr\r", "naïve", "", "x" * 4000):
            self.assertTrue(secrets.store("keyfit", "NAME", bad), repr(bad[:10]))
        for name in ("a/b", "NAME\n", "-w", ""):  # a slash would break keychain:service/account
            self.assertTrue(secrets.store("keyfit", name, "value"), repr(name))
        self.assertEqual(self.security_calls(), [])
        code, _, err = self.keyfit("secret", "set", "bad name", stdin=io.StringIO("v\n"))
        self.assertEqual(code, 1)

    def test_other_platforms_get_a_clear_message_and_nothing_is_read(self):
        stdin = io.StringIO(KEY + "\n")
        with mock.patch.object(sys, "platform", "linux"):
            for argv in (("add", "linear"), ("secret", "set", "NAME")):
                code, _, err = self.keyfit(*argv, stdin=stdin)
                self.assertEqual(code, 1)
                self.assertIn("Keyfit stores keys in the macOS Keychain; Linux is planned", err)
        self.assertEqual(stdin.tell(), 0)
        self.assertEqual(self.security_calls(), [])

    def test_env_and_keychain_references_work_as_before(self):
        with mock.patch.dict(os.environ, {"KEYFIT_TEST_LINEAR_KEY": KEY}):
            code, out, err = self.keyfit("add", "linear", "--key", "env:KEYFIT_TEST_LINEAR_KEY")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.registry()["projects"]["demo"]["linear"]["key"], "env:KEYFIT_TEST_LINEAR_KEY")
        self.assertNotIn(["-i"], self.security_calls())
        (self.stub / "keychain.json").write_text(json.dumps({"team/linear": OTHER}))
        code, out, err = self.keyfit("add", "linear", "--key", "keychain:team/linear")
        self.assertEqual(code, 0, err)
        self.assertIn("demo: linear other via Keychain item team/linear", out)
        self.assertNotIn("secrets", self.registry())  # only keys Keyfit stored itself are listed

    def test_secrets_table_round_trips_with_settings_and_is_never_a_project(self):
        path = self.config / "registry.toml"
        path.write_text(path.read_text() + '\n[settings]\nshared_aws_profiles = ["ci-worker"]\n')
        self.assertEqual(self.keyfit("add", "linear", stdin=io.StringIO(KEY + "\n"))[0], 0)
        self.assertEqual(self.keyfit("secret", "set", "globex-token", stdin=io.StringIO("v4lue\n"))[0], 0)
        reg = Registry.load(path)
        self.assertEqual(list(reg.projects()), ["demo"])
        self.assertEqual(reg.shared_aws_profiles(), {"ci-worker"})
        self.assertEqual(reg.stored_secrets(), ["linear-acme", "globex-token"])
        before = self.registry()
        self.assertEqual(before["settings"], {"shared_aws_profiles": ["ci-worker"]})
        reg.save()  # a load/save cycle keeps every table as it was
        self.assertEqual(self.registry(), before)


@unittest.skipUnless(sys.platform == "darwin" and os.path.exists("/usr/bin/security"), "macOS only")
class RealParserTests(unittest.TestCase):
    def test_quoting_matches_the_real_security_parser(self):
        """Never touches a keychain: the quoted text is given as an unknown command name, which is echoed back."""
        for text in ('zz-"dq"', "zz-'sq'", "zz-back\\slash\\", "zz-a b ; list-keychains",
                     "zz-" + "".join(chr(c) for c in range(0x20, 0x7f))):
            out = subprocess.run(["/usr/bin/security", "-i"], input=secrets._quote(text) + "\n",
                                 capture_output=True, text=True, timeout=15)
            self.assertEqual(out.stderr.splitlines()[0], f'security: unknown command "{text}"')


if __name__ == "__main__":
    unittest.main()
