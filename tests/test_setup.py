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
from keyfit.cli import NOT_MACOS, OLD_COMMENT, OLD_PATH_LINE, main  # noqa: E402
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

    def setup(self, *args, shell="/bin/zsh", platform="darwin", **extra):
        env = {"HOME": str(self.home), "KEYFIT_CONFIG_DIR": str(self.config), "SHELL": shell}
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, env), mock.patch.object(sys, "platform", platform), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            os.environ.pop("ZDOTDIR", None)  # restored with the rest of os.environ
            os.environ.update(extra)
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
                         [".keyfit-shims", "aws", "claude", "codex", "convex", "copilot", "firebase", "gcloud", "gemini", "gh",
                          "opencode", "supabase", "vercel", "wrangler"])
        self.assertIn("Shims written to", out)
        self.assertIn(": claude, codex, opencode, copilot, gemini, aws, gh, vercel, supabase, wrangler, convex, gcloud, firebase\n",
                      out)
        self.assertEqual(sorted(p.name for p in self.home.iterdir()), [".superset"])
        self.assertIn("add this as the LAST line of ~/.zshrc (or run: keyfit setup --rc)", out)
        entry = Path(cli.__file__).resolve().parent / "_shim_entry.py"
        self.assertEqual((self.config / "bin" / "aws").read_text().splitlines()[-1],
                         f'exec "{sys.executable}" -I -S "{entry}" aws "$@"')
        self.assertNotIn("Next:", out)  # nothing is active until the line is in a startup file

    def test_setup_again_says_it_is_already_active(self):
        # an upgrade: `keyfit setup` rewrites the shims and doesn't ask for a line that's already there
        self.setup("--rc")
        before = (self.home / ".zshrc").read_text()
        _, out, _ = self.setup()
        self.assertIn("Already active: ~/.zshrc loads them; new starts use them now.", out)
        self.assertNotIn("add this as the LAST line", out)
        self.assertEqual((self.home / ".zshrc").read_text(), before)

    def test_line_of_an_earlier_version_still_counts(self):
        # until KEY-64 the line's comment named four tools: `# keyfit: claude/codex/aws/gh shims on PATH`
        old = f'source "{self.config}/shell.zsh"  {OLD_COMMENT}'
        self.assertEqual(OLD_COMMENT, "# keyfit: claude/codex/aws/gh shims on PATH")
        (self.home / ".zshrc").write_text(f"export A=1\n{old}\nexport B=2\n")
        _, out, _ = self.setup()
        self.assertIn("Already active: ~/.zshrc loads them", out)  # an upgrade: the file is left alone
        self.assertIn(old, (self.home / ".zshrc").read_text())
        _, out, _ = self.setup("--rc")  # rewritten in place, never a second line
        line = f'source "{self.config}/shell.zsh"  # keyfit: tool shims on PATH'
        self.assertEqual((self.home / ".zshrc").read_text(), f"export A=1\n{line}\nexport B=2\n")
        self.assertIn(f"Updated the Keyfit line in ~/.zshrc:\n  {line}", out)

    def test_shim_whose_keyfit_is_gone_says_what_to_do(self):
        # `pipx uninstall keyfit` before `keyfit setup --remove`: no "No such file or directory" from sh, and the
        # tool still does not start unchecked
        self.setup()
        shim = self.config / "bin" / "claude"
        entry = str(Path(cli.__file__).resolve().parent / "_shim_entry.py")
        shim.write_text(shim.read_text().replace(entry, str(self.tmp / "gone" / "_shim_entry.py")))
        out = subprocess.run([str(shim), "--version"], capture_output=True, text=True, timeout=30,
                             env={"HOME": str(self.home), "PATH": "/usr/bin:/bin"})
        self.assertEqual(out.returncode, 127)
        self.assertEqual(out.stdout, "")
        self.assertIn(f"keyfit: claude not started: Keyfit is not installed where its shims expect it "
                      f"({self.tmp / 'gone' / '_shim_entry.py'}).\n  Upgraded or reinstalled Keyfit? Run: keyfit setup\n"
                      "  Uninstalled it? Remove the line ending in '# keyfit: tool shims on PATH' (or, from an earlier "
                      "version, '# keyfit: claude/codex/aws/gh shims on PATH') from your shell startup file, then open "
                      "a new terminal.\n", out.stderr)
        self.assertNotIn("No such file", out.stderr)

    def test_shim_whose_python_is_gone_says_how_to_reinstall_and_can_be_bypassed(self):
        # KEY-83: Homebrew removed the Python pipx's Keyfit ran on. Every tool said "keyfit setup", which can't run
        # either, and KEYFIT_DISABLE=1 couldn't help: the check runs in sh, before Python
        self.setup()
        shim = self.config / "bin" / "claude"
        gone = self.tmp / "gone" / "python3"
        shim.write_text(shim.read_text().replace(sys.executable, str(gone)))
        real = self.tmp / "real"
        real.mkdir()
        (real / "claude").write_text('#!/bin/sh\necho "real claude $*"\n')
        (real / "claude").chmod(0o755)
        (Path(self.ss) / "claude").write_text("#!/bin/sh\necho wrapper\n")  # Superset's: it would call the shim again
        (Path(self.ss) / "claude").chmod(0o755)
        path = f"{self.ss}:{self.shims}:{real}:/usr/bin:/bin"
        run = lambda **extra: subprocess.run([str(shim), "-p", "hi"], capture_output=True, text=True, timeout=30,
                                             env={"HOME": str(self.home), "PATH": path, **extra})
        out = run()
        self.assertEqual((out.returncode, out.stdout), (127, ""))
        self.assertEqual(out.stderr, f"keyfit: claude not started: the Python Keyfit was set up with is gone ({gone}).\n"
                                     "  Reinstall Keyfit (pipx reinstall keyfit, or brew reinstall keyfit), then run: "
                                     "keyfit setup\n  Bypass once: KEYFIT_DISABLE=1 claude …\n")
        out = run(KEYFIT_DISABLE="1")
        self.assertEqual((out.returncode, out.stdout, out.stderr), (0, "real claude -p hi\n", ""))
        (real / "claude").unlink()
        out = run(KEYFIT_DISABLE="1")
        self.assertEqual((out.returncode, out.stderr), (127, "keyfit: claude is not installed (no claude on PATH "
                                                            "besides Keyfit's shim).\n"))

    @unittest.skipUnless(ZSH, "zsh not installed")
    def test_folders_with_shell_characters_stay_one_word(self):
        # KEY-82: paths went into double quotes, so $HOME, ` or " in a folder name made every tool exit 127, and a
        # backtick ran its command at every launch
        weird = self.tmp / 'in $HOME `touch ran` "q" it\'s'
        weird.mkdir()
        (weird / "python").symlink_to(sys.executable)
        (weird / "keyfit").symlink_to(Path(cli.__file__).resolve().parent)
        self.config = weird / "config"
        self.shims = str(self.config / "bin")
        moved = lambda p: str(weird / "python") if str(p) == sys.executable else str(weird / "keyfit" / Path(p).name)
        with mock.patch("keyfit.realbin.stable_path", moved):
            code, out, err = self.setup("--rc")
        self.assertEqual(code, 0, err)
        real = self.tmp / "real"
        real.mkdir()
        (real / "claude").write_text('#!/bin/sh\necho "real claude $*"\n')
        (real / "claude").chmod(0o755)
        out = subprocess.run([f"{self.shims}/claude", "-p", "hi"], capture_output=True, text=True, timeout=30,
                             cwd=self.tmp, env={"HOME": str(self.home), "PATH": f"{real}:/usr/bin:/bin",
                                                "KEYFIT_CONFIG_DIR": str(self.config), "KEYFIT_DISABLE": "1"})
        self.assertEqual((out.returncode, out.stdout, out.stderr), (0, "real claude -p hi\n", ""))  # through Keyfit
        self.assertEqual(cli._check_shims(self.config / "bin").status, "ok")
        for shell, file in (([ZSH, "-f"], self.config / "shell.zsh"), (["/bin/sh"], self.config / "shell.sh"),
                            ([ZSH, "-f"], self.home / ".zshrc")):
            placed = subprocess.run([*shell, "-c", '. "$1"; printf %s "$PATH"', "sh", str(file)], capture_output=True,
                                    text=True, env={"HOME": str(self.home), "PATH": "/usr/bin"}, timeout=30)
            self.assertEqual((placed.stdout, placed.stderr), (f"{self.shims}:/usr/bin", ""), file)
        self.assertEqual(list(self.tmp.rglob("ran")), [])  # the backtick never ran
        (weird / "python").unlink()
        self.assertEqual(cli._check_shims(self.config / "bin").detail,
                         f"claude runs {weird / 'python'}, which no longer exists")
        self.assertIn("Removed the Keyfit line from ~/.zshrc", self.remove()[1])  # found again by its text

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
        _, out, _ = self.setup("--rc")
        line = f'source "{self.config}/shell.zsh"  # keyfit: tool shims on PATH'
        self.assertEqual((self.home / ".zshrc").read_text(), f"\n{line}\n")
        self.assertIn("Next: open a new terminal, go to a project's repository and run: keyfit init\n"
                      "Undo any time: keyfit setup --remove;", out)
        _, out, _ = self.setup("--rc")
        self.assertIn("~/.zshrc already loads them", out)
        self.assertEqual((self.home / ".zshrc").read_text().count(line), 1)

    def test_zshrc_alias_and_old_line_upgrade(self):
        rc = self.tmp / "custom-zshrc"
        rc.write_text(f"export A=1\n{OLD_PATH_LINE}\nexport B=2\n")
        self.setup("--zshrc", str(rc), shell="/bin/bash")  # --zshrc means zsh, whatever $SHELL says
        self.assertEqual(rc.read_text(), f'export A=1\nsource "{self.config}/shell.zsh"  # keyfit: tool shims on PATH\n'
                                         f'export B=2\n')

    def test_bash_rc_goes_to_the_login_profile(self):
        line = f'. "{self.config}/shell.sh"  # keyfit: tool shims on PATH'
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
        _, out, _ = self.setup("--shell", "bash", "--rc", str(rc))
        self.assertIn("shell.sh", rc.read_text())
        self.assertFalse((self.home / ".bash_profile").exists())
        self.assertIn(f"Undo any time: keyfit setup --remove --rc {rc};", out)  # --remove alone would not find it


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
        line = f'source "{self.config}/shell.zsh"  # keyfit: tool shims on PATH'
        old = f'source "{self.config}/shell.zsh"  {OLD_COMMENT}'  # the line of an earlier version
        mine = f'source "{self.config}/shell.zsh"  # my own copy'
        rc = self.tmp / "rc"
        rc.write_text(f"{mine}\n{OLD_PATH_LINE}\nexport B=2\n\n{line}\n")
        (self.home / ".zshrc").write_text(f"\n{old}\n")
        self.setup()
        (self.config / "bin" / "notes.txt").write_text("mine\n")
        (self.config / "bin" / "gh").write_text("#!/bin/sh\necho not a keyfit shim\n")
        code, out, _ = self.remove("--rc", str(rc))
        self.assertEqual(code, 0)
        self.assertEqual(rc.read_text(), f"{mine}\nexport B=2\n")
        self.assertIn(old, (self.home / ".zshrc").read_text())  # --rc PATH: only that file
        self.assertEqual(self.keyfit_files(), ["bin", "bin/gh", "bin/notes.txt"])  # files Keyfit did not write stay
        _, out, _ = self.remove()
        self.assertEqual((self.home / ".zshrc").read_text(), "")
        _, out, _ = self.remove()
        self.assertIn("No Keyfit line in ~/.zshrc", out)

    def test_startup_file_with_bytes_that_are_not_utf8(self):
        # KEY-82: `--rc` raised UnicodeDecodeError; `--remove` said "No Keyfit line" but deleted shell.zsh, which the
        # line still loaded: every new zsh printed an error
        zshrc = self.home / ".zshrc"
        zshrc.write_bytes(b"export A='caf\xe9'\n")  # Latin-1
        code, _, err = self.setup("--rc")
        self.assertEqual((code, err), (0, ""))
        line = f'source "{self.config}/shell.zsh"  # keyfit: tool shims on PATH'
        self.assertEqual(zshrc.read_bytes(), b"export A='caf\xe9'\n\n" + line.encode() + b"\n")
        self.assertEqual(self.setup("--rc")[1].splitlines()[1], "~/.zshrc already loads them")
        code, out, _ = self.remove()
        self.assertIn("Removed the Keyfit line from ~/.zshrc", out)
        self.assertEqual(zshrc.read_bytes(), b"export A='caf\xe9'\n")  # every other byte as it was

    def test_startup_file_keyfit_cannot_change_is_said_not_raised(self):
        # KEY-82: a folder, or a ZDOTDIR that doesn't exist, gave a traceback after the shims were written
        folder = self.tmp / "folder"
        folder.mkdir()
        code, out, err = self.setup("--rc", str(folder))
        self.assertEqual(code, 1)
        line = f'source "{self.config}/shell.zsh"  # keyfit: tool shims on PATH'
        self.assertEqual(err, f"keyfit setup: cannot change {folder} (Is a directory); add this as its last line "
                              f"yourself:\n  {line}\n")
        self.assertTrue((self.config / "bin" / "claude").exists())
        code, out, err = self.setup("--rc", ZDOTDIR=str(self.tmp / "missing"))
        self.assertEqual(code, 1)
        self.assertIn(f"keyfit setup: cannot change {self.tmp}/missing/.zshrc (No such file or directory)", err)
        self.assertNotIn("Traceback", err)

    def test_commented_out_line_loads_nothing(self):
        # KEY-82: found as a substring, `# source "…"  # keyfit: …` counted as active and no line was added
        line = f'source "{self.config}/shell.zsh"  # keyfit: tool shims on PATH'
        old = f'source "{self.config}/shell.zsh"  {OLD_COMMENT}'
        (self.home / ".zshrc").write_text(f"# {line}\n#{old}\n")
        _, out, _ = self.setup()
        self.assertIn("add this as the LAST line of ~/.zshrc", out)
        _, out, _ = self.setup("--rc")
        self.assertIn("Added to the end of ~/.zshrc", out)
        self.assertEqual((self.home / ".zshrc").read_text(), f"# {line}\n#{old}\n\n{line}\n")

    def test_purge_of_a_linked_config_folder_changes_nothing(self):
        # KEY-82: shutil.rmtree refuses a link: a traceback half-way, after the line and the shims were already gone
        real = self.tmp / "real-keyfit"
        real.mkdir()
        self.config.symlink_to(real)
        self.setup("--rc")
        before = (self.home / ".zshrc").read_text()
        code, out, err = self.remove("--purge", "--yes")
        self.assertEqual((code, out), (1, ""))
        self.assertEqual(err, f"{self.config} is a link to {real}, which --purge does not delete — nothing changed; "
                              "run keyfit setup --remove, then delete both yourself if you are sure\n")
        self.assertEqual((self.home / ".zshrc").read_text(), before)
        self.assertTrue((real / "bin" / "claude").exists())

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


class SwitcherOfferTests(unittest.TestCase):
    """The first `keyfit setup` in a terminal offers claude-swap and codex-multi-auth. Every tool on PATH is a stub
    that logs how it was run, so nothing is installed."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.config = self.tmp / "keyfit"
        self.stubs = self.tmp / "stubs"
        self.stubs.mkdir()
        self.log = self.tmp / "ran.log"

    def stub(self, *names, code=0):
        for name in names:
            exe = self.stubs / name
            exe.write_text(f'#!/bin/sh\necho "$(basename "$0") $*" >> "{self.log}"\nexit {code}\n')
            exe.chmod(0o755)

    def ran(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def setup(self, *args, answers=(), tty=True):
        answers, self.asked = list(answers), []

        def ask(question):
            self.asked.append(question)
            answer = answers.pop(0)
            if isinstance(answer, BaseException):
                raise answer
            return answer

        class Terminal(io.StringIO):
            def isatty(self):
                return tty

        env = {"HOME": str(self.home), "KEYFIT_CONFIG_DIR": str(self.config), "SHELL": "/bin/zsh",
               "PATH": f"{self.stubs}:/usr/bin:/bin"}
        out, err = Terminal(), io.StringIO()
        with mock.patch.dict(os.environ, env), mock.patch.object(sys, "platform", "darwin"), \
                mock.patch("sys.stdin", mock.Mock(isatty=lambda: tty)), mock.patch.object(cli, "_ask", ask), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            os.environ.pop("ZDOTDIR", None)
            code = main(["setup", *args])
        return code, out.getvalue(), err.getvalue()

    def test_first_setup_offers_both_and_installs_on_yes(self):
        self.stub("claude", "codex", "uv", "pipx", "npm")
        code, out, _ = self.setup("--rc", answers=["y", "yes"])
        self.assertEqual(code, 0)
        self.assertEqual(self.asked, ["Install it now (uv tool install claude-swap)? [y/N] ",
                                      "Install it now (npm install -g codex-multi-auth)? [y/N] "])
        self.assertEqual(self.ran(), ["uv tool install claude-swap", "npm install -g codex-multi-auth"])
        self.assertIn("\nclaude-swap switches between your Claude logins (https://github.com/realiti4/claude-swap).\n"
                      "claude-swap installed. Save each Claude login with: cswap add\n", out)
        self.assertIn("\ncodex-multi-auth switches between your ChatGPT logins in Codex "
                      "(https://github.com/ndycode/codex-multi-auth).\n"
                      "codex-multi-auth installed. Add your ChatGPT logins with: codex-multi-auth login\n\n"
                      "Next: open a new terminal", out)  # what to do next stays last, below the installers' output
        self.assertLess(out.index("Added to the end of ~/.zshrc"), out.index("claude-swap switches"))

    def test_enter_skips(self):
        self.stub("claude", "codex", "uv", "npm")
        code, out, _ = self.setup(answers=["", "n"])
        self.assertEqual((code, len(self.asked), self.ran()), (0, 2, []))
        self.assertNotIn("installed.", out)
        self.assertIn("To activate, add this as the LAST line", out)

    def test_pipx_without_uv_and_how_to_get_an_installer(self):
        self.stub("claude", "codex", "pipx")
        _, out, _ = self.setup(answers=["y"])
        self.assertEqual(self.asked, ["Install it now (pipx install claude-swap)? [y/N] "])
        self.assertEqual(self.ran(), ["pipx install claude-swap"])
        self.assertIn("To install it: brew install node && npm install -g codex-multi-auth\n", out)
        (self.stubs / "pipx").unlink()
        self.setup("--remove")
        _, out, _ = self.setup()
        self.assertEqual(self.asked, [])
        self.assertIn("To install it: brew install uv && uv tool install claude-swap\n", out)

    def test_only_for_agents_that_are_installed_without_their_switcher(self):
        self.stub("claude", "cswap", "uv", "npm")  # no codex, and claude-swap is installed already
        _, out, _ = self.setup()
        self.assertEqual((self.asked, self.ran()), ([], []))
        self.assertNotIn("claude-swap", out)
        self.assertNotIn("codex-multi-auth", out)

    def test_only_the_first_setup_and_only_in_a_terminal(self):
        self.stub("claude", "codex", "uv", "npm")
        self.setup(tty=False)
        self.assertEqual(self.asked, [])
        self.setup()  # a rerun (an upgrade, or a repair doctor asks for) rewrites the shims and asks nothing
        self.assertEqual(self.asked, [])
        self.setup("--remove")
        self.setup(answers=["n", "n"])  # after --remove, setup is a first one again
        self.assertEqual(len(self.asked), 2)

    def test_a_failed_install_leaves_setup_done(self):
        self.stub("claude", "codex")
        self.stub("uv", "npm", code=1)
        code, out, err = self.setup("--rc", answers=["y", "y"])
        self.assertEqual(code, 0)
        self.assertIn("claude-swap was not installed (uv tool install claude-swap did not finish); "
                      "see https://github.com/realiti4/claude-swap\n", err)
        self.assertIn("codex-multi-auth was not installed (npm install -g codex-multi-auth did not finish)", err)
        self.assertIn("# keyfit: tool shims on PATH", (self.home / ".zshrc").read_text())
        self.assertIn("Next: open a new terminal", out)

    def test_ctrl_c_stops_only_the_offers(self):
        self.stub("claude", "codex", "uv", "npm")
        code, out, _ = self.setup("--rc", answers=[KeyboardInterrupt()])
        self.assertEqual((code, len(self.asked), self.ran()), (0, 1, []))
        self.assertIn("\nStopped. Keyfit itself is set up.\n\nNext: open a new terminal", out)


class OldPythonTests(unittest.TestCase):
    def test_too_old_python_gets_a_sentence_not_a_traceback(self):
        # macOS's own python3 is 3.9; `bin/keyfit` from a checkout runs whatever python3 comes first
        code = "import sys; sys.version_info = (3, 9, 6, 'final', 0); sys.path.insert(0, sys.argv[1]); import keyfit"
        root = str(Path(cli.__file__).resolve().parents[1])
        out = subprocess.run([sys.executable, "-I", "-c", code, root], capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 1)
        self.assertEqual(out.stderr, f"keyfit needs Python 3.11 or newer, but this is Python 3.9 ({sys.executable}).\n"
                                     "  Install a newer one (brew install python@3.13), then run Keyfit with it, "
                                     "for example: pipx install --python python3.13 keyfit\n")


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
