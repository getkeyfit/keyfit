"""`keyfit setup`: shims, the zsh and bash PATH files, and which startup file gets the line."""

import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ["KEYFIT_CONFIG_DIR"] = tempfile.mkdtemp()  # tests never touch the real ~/.config/keyfit

from keyfit import cli  # noqa: E402
from keyfit.cli import NOT_MACOS, OLD_PATH_LINE, main  # noqa: E402
from keyfit.realbin import stable_path  # noqa: E402

BASHES = sorted({b for b in ("/bin/bash", shutil.which("bash")) if b and os.access(b, os.X_OK)})  # 3.2 and current
ZSH = shutil.which("zsh")


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.home = self.tmp / "home"
        (self.home / ".superset" / "bin").mkdir(parents=True)
        self.config = self.tmp / "keyfit"
        self.shims = str(self.config / "bin")
        self.ss = str(self.home / ".superset" / "bin")

    def setup(self, *args, shell="/bin/zsh", platform="darwin"):
        env = {"HOME": str(self.home), "KEYFIT_CONFIG_DIR": str(self.config), "SHELL": shell}
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, env), mock.patch.object(sys, "platform", platform), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            os.environ.pop("ZDOTDIR", None)  # restored with the rest of os.environ
            code = main(["setup", *args])
        return code, out.getvalue(), err.getvalue()

    def placed(self, shell: list[str], snippet: str, path: str) -> str:
        """PATH after sourcing the snippet twice in a shell started with *path*."""
        out = subprocess.run([*shell, f'. "{snippet}"; . "{snippet}"; printf %s "$PATH"'], capture_output=True,
                             text=True, env={"HOME": str(self.home), "PATH": path}, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stderr, "")
        return out.stdout

    def cases(self):
        kf, ss = self.shims, self.ss
        return [
            ("/usr/bin:/bin", f"{kf}:/usr/bin:/bin"),
            # a login profile prepended /x after Superset set PATH: wrapper and shims both go to the front
            (f"/x:{ss}:/usr/bin:/bin", f"{ss}:{kf}:/x:/usr/bin:/bin"),
            (f"{kf}:/x:{ss}:/usr/bin:{kf}:/bin", f"{ss}:{kf}:/x:/usr/bin:/bin"),  # moved, never duplicated
            (f"{ss}:/x:{ss}:/bin", f"{ss}:{kf}:/x:/bin"),
            ("/usr/bin::/bin", f"{kf}:/usr/bin:/bin"),
            ("", kf),
        ]

    def test_plain_setup_writes_only_into_the_config_dir(self):
        code, out, _ = self.setup()
        self.assertEqual(code, 0)
        self.assertEqual(sorted(p.name for p in self.config.iterdir()), ["bin", "shell.sh", "shell.zsh"])
        self.assertEqual(sorted(p.name for p in (self.config / "bin").iterdir()),
                         [".keyfit-shims", "aws", "claude", "codex", "gh", "vercel"])
        self.assertIn("Shims written to", out)
        self.assertIn(": claude, codex, aws, gh, vercel\n", out)
        self.assertEqual(sorted(p.name for p in self.home.iterdir()), [".superset"])
        self.assertIn("add this as the LAST line of ~/.zshrc (or run: keyfit setup --rc)", out)
        entry = Path(cli.__file__).resolve().parent / "_shim_entry.py"
        self.assertEqual((self.config / "bin" / "aws").read_text(),
                         f'#!/bin/sh\nexec "{sys.executable}" -I "{entry}" aws "$@"\n')

    def test_not_macos(self):
        code, out, err = self.setup(platform="linux")
        self.assertEqual(code, 1)
        self.assertEqual(err.strip(), NOT_MACOS)
        self.assertEqual(NOT_MACOS, "Keyfit supports macOS for now (it stores keys in the Keychain); Linux is planned.")
        self.assertFalse(self.config.exists())

    @unittest.skipUnless(BASHES, "bash not installed")
    def test_bash_file_parses_and_places_the_shims(self):
        self.setup()
        snippet = str(self.config / "shell.sh")
        for bash in [*BASHES, "/bin/sh"]:
            with self.subTest(shell=bash):
                if bash != "/bin/sh":
                    check = subprocess.run([bash, "-n", snippet], capture_output=True, text=True)
                    self.assertEqual(check.returncode, 0, check.stderr)
                for given, expected in self.cases():
                    self.assertEqual(self.placed([bash, "-c"], snippet, given), expected, given)
                leftovers = subprocess.run([bash, "-c", f'. "{snippet}"; set | grep "^_kf" || true'], capture_output=True,
                                           text=True, env={"HOME": str(self.home), "PATH": "/usr/bin:/bin"})
                self.assertEqual(leftovers.stdout, "")

    @unittest.skipUnless(ZSH, "zsh not installed")
    def test_zsh_file_parses_and_places_the_shims(self):
        self.setup()
        snippet = str(self.config / "shell.zsh")
        check = subprocess.run([ZSH, "-n", snippet], capture_output=True, text=True)
        self.assertEqual(check.returncode, 0, check.stderr)
        for given, expected in self.cases():
            self.assertEqual(self.placed([ZSH, "-f", "-c"], snippet, given), expected, given)

    def test_shell_auto_follows_SHELL(self):
        _, out, _ = self.setup(shell="/opt/homebrew/bin/bash")
        self.assertIn(f'. "{self.config}/shell.sh"  # keyfit:', out)
        self.assertIn("LAST line of ~/.bash_profile (or run: keyfit setup --rc)", out)
        _, out, _ = self.setup("--shell", "zsh", shell="/bin/bash")
        self.assertIn(f'source "{self.config}/shell.zsh"', out)
        self.assertIn("keyfit setup --shell zsh --rc", out)

    def test_unknown_shell(self):
        code, out, _ = self.setup(shell="/usr/local/bin/fish")
        self.assertEqual(code, 0)  # shims and PATH files are written; the user wires up fish
        self.assertIn("put " + self.shims + " first on PATH", out)
        code, _, err = self.setup("--rc", shell="/usr/local/bin/fish")
        self.assertEqual(code, 1)
        self.assertIn("--shell zsh or --shell bash", err)

    def test_zsh_rc(self):
        self.setup("--rc")
        line = f'source "{self.config}/shell.zsh"  # keyfit: claude/codex/aws/gh shims on PATH'
        self.assertEqual((self.home / ".zshrc").read_text(), f"\n{line}\n")
        _, out, _ = self.setup("--rc")
        self.assertIn("~/.zshrc already loads them", out)
        self.assertEqual((self.home / ".zshrc").read_text().count(line), 1)

    def test_zshrc_alias_and_old_line_upgrade(self):
        rc = self.tmp / "custom-zshrc"
        rc.write_text(f"export A=1\n{OLD_PATH_LINE}\nexport B=2\n")
        self.setup("--zshrc", str(rc), shell="/bin/bash")  # --zshrc means zsh, whatever $SHELL says
        self.assertEqual(rc.read_text(), f'export A=1\nsource "{self.config}/shell.zsh"  # keyfit: claude/codex/aws/gh '
                                         f'shims on PATH\nexport B=2\n')

    def test_bash_rc_goes_to_the_login_profile(self):
        line = f'. "{self.config}/shell.sh"  # keyfit: claude/codex/aws/gh shims on PATH'
        self.setup("--rc", shell="/bin/bash")
        self.assertEqual((self.home / ".bash_profile").read_text(), f"\n{line}\n")
        self.assertFalse((self.home / ".bashrc").exists())

    def test_bash_rc_keeps_an_existing_profile_in_charge(self):
        (self.home / ".profile").write_text("export A=1\n")
        self.setup("--rc", shell="/bin/bash")
        self.assertFalse((self.home / ".bash_profile").exists())  # creating it would hide ~/.profile
        self.assertIn("shell.sh", (self.home / ".profile").read_text())

    def test_bash_rc_also_in_bashrc_when_the_profile_sources_it(self):
        (self.home / ".bash_profile").write_text('if [ -f "$HOME/.bashrc" ]; then\n  source "$HOME/.bashrc"\nfi\n')
        _, out, _ = self.setup("--rc", shell="/bin/bash")
        for name in (".bash_profile", ".bashrc"):
            self.assertIn("shell.sh", (self.home / name).read_text())
            self.assertIn(f"Added to the end of ~/{name}", out)

    @unittest.skipUnless(BASHES, "bash not installed")
    def test_bash_login_shell_with_both_lines(self):
        # the profile sources ~/.bashrc first and prepends to PATH afterwards: its own last line puts the shims back
        (self.home / ".bash_profile").write_text("[ -f ~/.bashrc ] && . ~/.bashrc\nPATH=/late:$PATH\n")
        self.setup("--rc", shell="/bin/bash")
        out = subprocess.run([BASHES[0], "-l", "-c", 'printf %s "$PATH"'], capture_output=True, text=True,
                             env={"HOME": str(self.home), "PATH": "/usr/bin:/bin"}, timeout=30)
        entries = out.stdout.split(":")  # /etc/profile may add system folders (macOS path_helper)
        self.assertEqual(entries[:2], [self.shims, "/late"], out.stderr)
        self.assertEqual(entries.count(self.shims), 1)

    def test_rc_path(self):
        rc = self.tmp / "rc"
        self.setup("--shell", "bash", "--rc", str(rc))
        self.assertIn("shell.sh", rc.read_text())
        self.assertFalse((self.home / ".bash_profile").exists())


    # setup --remove ---------------------------------------------------------------------------------------------

    def remove(self, *args, answer=None, **kw):
        tty = mock.Mock(isatty=lambda: answer is not None)
        asked = []
        with mock.patch("sys.stdin", tty), mock.patch.object(cli, "_ask", lambda q: asked.append(q) or answer):
            result = self.setup("--remove", *args, **kw)
        self.asked = asked
        return result

    def keyfit_files(self):
        return sorted(str(p.relative_to(self.config)) for p in self.config.rglob("*")) if self.config.exists() else None

    def test_remove_round_trip_zsh(self):
        (self.home / ".zshrc").write_text("export A=1\n")
        (self.config).mkdir()
        (self.config / "registry.toml").write_text('[projects.acme]\nroots = ["~/acme"]\n')
        self.setup("--rc")
        self.assertIn("# keyfit:", (self.home / ".zshrc").read_text())
        code, out, err = self.remove()
        self.assertEqual(code, 0, err)
        self.assertEqual((self.home / ".zshrc").read_text(), "export A=1\n")  # exactly as before setup
        self.assertEqual(self.keyfit_files(), ["registry.toml"])  # shims and both PATH files gone, registry kept
        self.assertIn("Removed the Keyfit line from ~/.zshrc", out)
        self.assertIn("Kept the registry", out)
        self.assertEqual(sorted(p.name for p in self.home.iterdir()), [".superset", ".zshrc"])

    def test_remove_round_trip_bash_profile_and_bashrc(self):
        profile = 'if [ -f "$HOME/.bashrc" ]; then\n  source "$HOME/.bashrc"\nfi\n'
        (self.home / ".bash_profile").write_text(profile)
        (self.home / ".bashrc").write_text("alias ll='ls -l'")  # no final newline
        self.setup("--rc", shell="/bin/bash")
        self.setup("--rc", shell="/bin/zsh")  # a zsh line too: both shells are undone in one go
        code, out, _ = self.remove(shell="/bin/bash")
        self.assertEqual(code, 0)
        self.assertEqual((self.home / ".bash_profile").read_text(), profile)
        self.assertEqual((self.home / ".bashrc").read_text(), "alias ll='ls -l'\n")
        self.assertEqual((self.home / ".zshrc").read_text(), "")  # setup created it; it is left, empty
        for name in (".bash_profile", ".bashrc", ".zshrc"):
            self.assertIn(f"Removed the Keyfit line from ~/{name}", out)
        self.assertIsNone(self.keyfit_files())  # nothing of Keyfit's was left: the folder is gone too

    def test_remove_takes_out_only_keyfit_lines(self):
        line = f'source "{self.config}/shell.zsh"  # keyfit: claude/codex/aws/gh shims on PATH'
        mine = f'source "{self.config}/shell.zsh"  # my own copy'
        rc = self.tmp / "rc"
        rc.write_text(f"{mine}\n{OLD_PATH_LINE}\nexport B=2\n\n{line}\n")
        (self.home / ".zshrc").write_text(f"\n{line}\n")
        self.setup()
        (self.config / "bin" / "notes.txt").write_text("mine\n")
        (self.config / "bin" / "gh").write_text("#!/bin/sh\necho not a keyfit shim\n")
        code, out, _ = self.remove("--rc", str(rc))
        self.assertEqual(code, 0)
        self.assertEqual(rc.read_text(), f"{mine}\nexport B=2\n")
        self.assertIn(line, (self.home / ".zshrc").read_text())  # --rc PATH: only that file
        self.assertEqual(self.keyfit_files(), ["bin", "bin/gh", "bin/notes.txt"])  # files Keyfit did not write stay
        _, out, _ = self.remove()
        self.assertEqual((self.home / ".zshrc").read_text(), "")
        _, out, _ = self.remove()
        self.assertIn("No Keyfit line in ~/.zshrc", out)

    def test_remove_works_anywhere(self):
        self.setup()
        code, _, _ = self.remove(platform="linux")
        self.assertEqual(code, 0)
        self.assertIsNone(self.keyfit_files())

    def test_purge_asks_first_and_keeps_keychain_items(self):
        self.setup("--rc")
        (self.config / "registry.toml").write_text('[secrets]\nkeychain = ["linear-acme"]\n')
        (self.config / "claude" / "client").mkdir(parents=True)
        before = (self.home / ".zshrc").read_text()
        for answer, said in ((None, "confirm with --yes — nothing changed"), ("n", "Nothing changed.")):
            code, out, err = self.remove("--purge", answer=answer)  # no terminal and no --yes, or a "no"
            self.assertEqual(code, 1)
            self.assertIn(said, out + err)
            self.assertEqual((self.home / ".zshrc").read_text(), before)
            self.assertTrue((self.config / "bin" / "claude").exists())
        code, out, _ = self.remove("--purge", answer="y")
        self.assertEqual(code, 0)
        self.assertEqual(self.asked, [f"Delete {self.config}: the registry of your projects and the Claude account "
                                      "folders client? [y/N] "])
        self.assertFalse(self.config.exists())
        self.assertEqual((self.home / ".zshrc").read_text(), "")
        self.assertIn("Keychain items Keyfit stored are kept (service keyfit): linear-acme", out)

    def test_purge_with_yes_and_only_of_a_keyfit_folder(self):
        self.setup()
        (self.config / "notes.txt").write_text("not Keyfit's\n")
        code, _, err = self.remove("--purge", "--yes")
        self.assertEqual(code, 1)
        self.assertIn("also holds notes.txt, which Keyfit did not write — nothing changed", err)
        self.assertIn("bin", self.keyfit_files())
        (self.config / "notes.txt").unlink()
        self.assertEqual(self.remove("--purge", "--yes")[0], 0)
        self.assertEqual(self.asked, [])
        self.assertFalse(self.config.exists())
        code, _, err = self.setup("--purge")
        self.assertEqual((code, err.strip()), (1, "keyfit setup: --purge goes with --remove"))

class StablePathTests(unittest.TestCase):
    def test_homebrew_cellar_paths_survive_upgrades(self):
        self.assertEqual(stable_path("/opt/homebrew/Cellar/keyfit/0.1.0_1/libexec/bin/python"),
                         "/opt/homebrew/opt/keyfit/libexec/bin/python")
        self.assertEqual(stable_path("/usr/local/Cellar/keyfit/0.2.0/libexec/lib/python3.13/site-packages/keyfit/x.py"),
                         "/usr/local/opt/keyfit/libexec/lib/python3.13/site-packages/keyfit/x.py")
        for other in ("/opt/homebrew/Cellar/python@3.13/3.13.11/bin/python3", "/Users/a/.venv/bin/python"):
            self.assertEqual(stable_path(other), other)


if __name__ == "__main__":
    unittest.main()
