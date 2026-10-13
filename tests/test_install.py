"""Installed from a wheel, Keyfit needs nothing from the checkout: built offline, installed into a fresh venv,
`keyfit setup` writes shims that run the venv's package (with `-I`, so not even PYTHONPATH brings the checkout back).

Skipped when this Python cannot build offline (setuptools >= 77 and pip >= 22.3 must already be installed)."""

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

os.environ["KEYFIT_CONFIG_DIR"] = tempfile.mkdtemp()  # tests never touch the real ~/.config/keyfit

import keyfit  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
# no network, no prompts, no user pip.conf (index URLs, credentials)
QUIET_PIP = {"PIP_DISABLE_PIP_VERSION_CHECK": "1", "PIP_NO_INPUT": "1", "PIP_CONFIG_FILE": os.devnull}


def _version(text: str) -> tuple:
    return tuple(int(part) for part in text.split(".")[:2] if part.isdigit())


def _cannot_build() -> str:
    # Probe in a child process: importing setuptools here can fail in odd ways (some CI Pythons raise an
    # AssertionError from its distutils shim) and must never break the rest of the test run.
    probe = "import pip, setuptools; print(setuptools.__version__, pip.__version__)"
    try:
        out = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as err:
        return f"could not check for setuptools and pip ({type(err).__name__})"
    if out.returncode != 0 or len(out.stdout.split()) != 2:
        return "building a wheel offline needs importable setuptools and pip in this Python"
    tools, pips = out.stdout.split()
    if _version(tools) < (77,) or _version(pips) < (22, 3):
        return f"building offline needs setuptools>=77 and pip>=22.3 (have {tools}, {pips})"
    return ""


CANNOT_BUILD = _cannot_build()


@unittest.skipIf(CANNOT_BUILD, CANNOT_BUILD)
class InstalledWheelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp()).resolve()
        src = cls.tmp / "src"  # a copy, so the build leaves nothing in the checkout
        shutil.copytree(ROOT / "keyfit", src / "keyfit", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        for name in ("pyproject.toml", "README.md", "LICENSE"):
            if (ROOT / name).exists():
                shutil.copy(ROOT / name, src / name)
        env = {**os.environ, **QUIET_PIP}
        pip = [sys.executable, "-m", "pip"]
        subprocess.run([*pip, "wheel", "--no-deps", "--no-build-isolation", "--no-index", "--no-cache-dir", "-q", "-w",
                        str(cls.tmp / "dist"), str(src)], check=True, capture_output=True, env=env, timeout=300)
        cls.wheel = next((cls.tmp / "dist").glob("keyfit-*.whl"))
        cls.venv = cls.tmp / "venv"
        subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(cls.venv)], check=True, timeout=120)
        subprocess.run([*pip, "--python", str(cls.venv / "bin" / "python"), "install", "--no-deps", "--no-index",
                        "--no-cache-dir", "-q", str(cls.wheel)], check=True, capture_output=True, env=env, timeout=300)
        cls.home = cls.tmp / "home"
        cls.home.mkdir()
        cls.config = cls.tmp / "config"
        cls.stub = cls.tmp / "stub"
        cls.stub.mkdir()
        for tool in ("aws", "claude"):
            exe = cls.stub / tool
            exe.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
            exe.chmod(exe.stat().st_mode | stat.S_IEXEC)

    def run_in(self, cwd, *command, **extra):
        env = {"HOME": str(self.home), "KEYFIT_CONFIG_DIR": str(self.config), "SHELL": "/bin/zsh",
               "PATH": f"{self.config / 'bin'}:{self.stub}:/usr/bin:/bin", **extra}
        out = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        return out

    def test_wheel_holds_the_whole_package(self):
        names = zipfile.ZipFile(self.wheel).namelist()
        for name in ("keyfit/adapters/__init__.py", "keyfit/statusline.py", "keyfit/_shim_entry.py"):
            self.assertIn(name, names)
        self.assertFalse([n for n in names if n.startswith(("bin/", "tests/"))])
        self.assertEqual(self.wheel.name, f"keyfit-{keyfit.__version__}-py3-none-any.whl")

    def test_version(self):
        out = self.run_in(self.tmp, str(self.venv / "bin" / "keyfit"), "--version")
        self.assertEqual(out.stdout.strip(), f"keyfit {keyfit.__version__}")

    @unittest.skipUnless(sys.platform == "darwin", "keyfit setup runs on macOS only")
    def test_setup_writes_shims_that_run_the_installed_package(self):
        self.run_in(self.tmp, str(self.venv / "bin" / "keyfit"), "setup")
        shim = (self.config / "bin" / "aws").read_text()
        site = next(self.venv.glob("lib/python*/site-packages"))
        self.assertEqual(shim.splitlines()[-1], f'exec "{self.venv}/bin/python" -I -S "{site}/keyfit/_shim_entry.py" aws "$@"')

        repo = self.tmp / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        checkout = {"PYTHONPATH": str(ROOT)}  # ignored by the shims' `-I`
        out = self.run_in(repo, "aws", "s3", "ls", KEYFIT_DISABLE="1", **checkout)
        self.assertEqual(out.stdout.split(), ["s3", "ls"])
        self.run_in(repo, str(self.venv / "bin" / "keyfit"), "init", "--name", "demo")
        out = self.run_in(repo, "claude", "-p", "hi", **checkout)
        args = out.stdout.splitlines()
        self.assertEqual((args[0], args[2:]), ("--settings", ["-p", "hi"]))
        command = json.loads(args[1])["statusLine"]["command"]
        self.assertEqual(command, f"{self.venv}/bin/python -I {site}/keyfit/statusline.py")
        line = subprocess.run(["/bin/sh", "-c", command], input="{}", capture_output=True, text=True,
                              env={"KEYFIT_SUMMARY": "demo", **checkout}).stdout
        self.assertEqual(line, "keyfit · demo\n")


class CheckoutRunnerTests(unittest.TestCase):
    def test_bin_keyfit_never_imports_from_the_current_folder(self):
        # `python -m` would put the current folder first on sys.path: a repository's argparse.py would run
        folder = Path(tempfile.mkdtemp()).resolve()
        marker = folder / "ran"
        (folder / "argparse.py").write_text(f"open({str(marker)!r}, 'w').close()\n")
        env = {"PATH": f"{Path(sys.executable).parent}{os.pathsep}/usr/bin:/bin", "HOME": str(folder),
               "KEYFIT_CONFIG_DIR": str(folder / "config"), "PYTHONPATH": str(folder)}
        out = subprocess.run([str(ROOT / "bin" / "keyfit"), "--version"], cwd=folder, env=env,
                             capture_output=True, text=True, timeout=60)
        self.assertEqual(out.stdout.strip(), f"keyfit {keyfit.__version__}", out.stderr)
        self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
