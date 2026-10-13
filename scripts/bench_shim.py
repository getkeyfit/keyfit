#!/usr/bin/env python3
"""How long does a Keyfit shim take before the real tool starts? A reproducible benchmark, standard library only.

    python3 scripts/bench_shim.py                          # 20 runs per case, this checkout
    python3 scripts/bench_shim.py --baseline ../keyfit     # compared with another checkout, run by run
    python3 scripts/bench_shim.py --runs 50 --keyfit /path/to/a/checkout --tools gh,claude

Everything happens in a temporary folder: its own KEYFIT_CONFIG_DIR, HOME, registry and git repositories, shims
written by that checkout's `keyfit setup`, and stub "real" tools that exit at once (they also answer Keyfit's own
`gh auth token`, `aws sts get-caller-identity` and `claude auth status --json`, instantly). PATH holds only the
temporary folders, git and /usr/bin:/bin, so no real tool, account, network or ~/.config/keyfit is involved.
The time measured is therefore Keyfit's own; the real `gh auth token` (about 80 ms) and the network are not in it.

Cases, per tool:
  direct      the stub called by its path: the floor every other case is compared with
  outside     a git repository that is not registered (Keyfit passes straight through)
  warm        the registered project, every check cached
  session     warm, inside a session Keyfit started (KEYFIT_PROJECT and the session's GH_TOKEN inherited)
  version     `<tool> --version` in the registered project
  login       a sign-in command in the registered project (`aws sso login`, `gh auth status`, …)
  cold        the registered project with the AWS and Claude checks not cached: they run, answered by the stubs
              (aws, claude and codex; GitHub, Linear and Vercel are checked over HTTPS, so those stay cached)

Each case runs twice untimed first: macOS scans new executables on their first runs, which is not Keyfit's time.
macOS only, like `keyfit setup`. Numbers move with the machine's load: compare checkouts with --baseline.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

TOOLS = ("aws", "gh", "vercel", "claude", "codex")
LOGIN = {"aws": ["sso", "login"], "gh": ["auth", "status"], "vercel": ["whoami"], "claude": ["auth", "status"],
         "codex": ["login", "status"]}
TOKEN = "gho_" + "0" * 36  # the stub's fake GitHub token
STUB = """#!/bin/sh
case "$1 $2" in
  "auth token") echo %(token)s ;;
  "sts get-caller-identity") echo 123456789012 ;;
  "auth status") [ "$3" = "--json" ] && echo '%(claude)s' ;;
esac
exit 0
"""
REGISTRY = """[projects.bench]
roots = ["%(repo)s"]
aws = { profile = "bench", account = "123456789012" }
linear = { workspace = "bench", key = "env:LINEAR_BENCH_API_KEY" }
github = "bench"
vercel = "bench"
claude = "bench"
codex = "bench"

[accounts.github.bench]
login = "bench-user"
email = "bench@example.com"

[accounts.vercel.bench]
config_dir = "%(vercel)s"
user = "bench-user"
team = "bench"
team_id = "team_bench"

[accounts.claude.bench]
config_dir = "%(claude_dir)s"
org_id = "org-bench"
org_name = "Bench"

[accounts.codex.bench]
codex_home = "%(codex_home)s"
plan = "pro"
"""
# Seeds the check cache the way a first launch would, with GitHub, Linear and Vercel answering from memory;
# prints the cache keys of the checks that go over HTTPS.
WARM = """
import json, os, sys
sys.path.insert(0, sys.argv[1])
from keyfit import cache, environment
from keyfit.adapters import ADAPTERS
from keyfit.registry import Registry
from keyfit.resolve import resolve
ADAPTERS["github"].fetch = lambda token, timeout: (200, {"login": "bench-user"})
ADAPTERS["linear"].fetch = lambda key, timeout: (200, {"data": {"organization": {"urlKey": "bench"}}})
ADAPTERS["vercel"].fetch = lambda path, token, timeout: (
    (200, {"user": {"username": "bench-user"}}) if path == "/v2/user" else (200, {"id": "team_bench", "slug": "bench"}))
project = resolve(".", Registry.load()).project
env = environment.build(project)
failed = [c.lines() for c in environment.verify(project, env, use_cache=True) if c.failed]
if failed:
    sys.exit(f"warm-up failed: {failed}")
print(json.dumps([cache.key(project.name, kind, conn, ADAPTERS[kind].fingerprint(conn, env))
                  for kind, conn in project.connections.items() if kind in ("github", "linear", "vercel")]))
"""


def executable(path: Path, text: str) -> None:
    path.write_text(text)
    path.chmod(0o755)


def git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def build(root: Path, keyfit: Path, python: str) -> dict:
    """Lay out the temporary world; return the environment the shims run in."""
    home, config, stubs, tools = root / "home", root / "keyfit", root / "stubs", root / "tools"
    for folder in (home, config, stubs, tools):
        folder.mkdir(parents=True)
    claude_dir, codex_home = home / ".claude", home / ".codex"
    vercel = home / "Library" / "Application Support" / "com.vercel.cli"
    for folder in (claude_dir, codex_home, vercel, home / ".aws"):
        folder.mkdir(parents=True)
    status = {"loggedIn": True, "authMethod": "claude.ai", "orgId": "org-bench", "orgName": "Bench",
              "subscriptionType": "team", "configDirectory": str(claude_dir)}
    for tool in TOOLS:
        executable(stubs / tool, STUB % {"token": TOKEN, "claude": json.dumps(status)})
    real_git = shutil.which("git", path=os.pathsep.join(
        p for p in os.environ.get("PATH", "").split(os.pathsep) if p and not (Path(p) / ".keyfit-shims").exists()))
    if real_git:
        (tools / "git").symlink_to(real_git)

    (home / ".aws" / "config").write_text("[profile bench]\ncredential_process = /usr/bin/true\n")
    (home / ".claude.json").write_text(json.dumps({
        "oauthAccount": {"organizationUuid": "org-bench"},
        "mcpServers": {"linear_other": {"type": "http", "url": "https://mcp.linear.app/mcp",
                                        "headers": {"Authorization": "Bearer ${LINEAR_OTHER_API_KEY}"}},
                       "docs": {"type": "http", "url": "https://docs.example/mcp"}}}))
    claims = base64.urlsafe_b64encode(json.dumps({"https://api.openai.com/auth": {
        "chatgpt_plan_type": "pro", "chatgpt_account_id": "acc-bench"}}).encode()).decode().rstrip("=")
    (codex_home / "auth.json").write_text(json.dumps({"auth_mode": "chatgpt", "tokens": {"id_token": f"e30.{claims}.x"}}))
    (codex_home / "config.toml").write_text('[mcp_servers.linear_other]\nurl = "https://mcp.linear.app/mcp"\n'
                                            'bearer_token_env_var = "LINEAR_OTHER_API_KEY"\n')
    (vercel / "auth.json").write_text(json.dumps({"token": "vercel-bench"}))
    (vercel / "config.json").write_text(json.dumps({"currentTeam": "team_bench"}))

    repos = {}
    for name in ("repo", "outside"):
        repos[name] = root / name
        repos[name].mkdir()
        git("init", "-q", cwd=repos[name])
    git("config", "user.email", "bench@example.com", cwd=repos["repo"])
    git("remote", "add", "origin", "git@github.com:bench/repo.git", cwd=repos["repo"])
    (config / "registry.toml").write_text(REGISTRY % {
        "repo": repos["repo"], "vercel": vercel, "claude_dir": claude_dir, "codex_home": codex_home})

    env = {"HOME": str(home), "KEYFIT_CONFIG_DIR": str(config), "LINEAR_BENCH_API_KEY": "lin_api_" + "b" * 40,
           "PATH": os.pathsep.join([str(config / "bin"), str(stubs), str(tools), "/usr/bin", "/bin"]),
           "TMPDIR": os.environ.get("TMPDIR", "/tmp"), "LANG": "C"}
    setup = subprocess.run([python, "-I", "-c", "import sys; sys.path.insert(0, sys.argv[1]); from keyfit.cli import "
                            "main; sys.exit(main(['setup']))", str(keyfit)], env=env, capture_output=True, text=True)
    if setup.returncode != 0:
        sys.exit(f"keyfit setup failed: {setup.stderr}")
    warm = subprocess.run([python, "-I", "-c", WARM, str(keyfit)], cwd=repos["repo"], env=env,
                          capture_output=True, text=True)
    if warm.returncode != 0:
        sys.exit(warm.stderr or warm.stdout)
    cache = config / "cache" / "checks.json"
    https = set(json.loads(warm.stdout))
    warm_cache = cache.read_text()
    https_only = json.dumps({k: v for k, v in json.loads(warm_cache).items() if k in https})
    return {"env": env, "repos": repos, "shims": config / "bin", "stubs": stubs,
            "warm": lambda: cache.write_text(warm_cache), "cold": lambda: cache.write_text(https_only)}


def percentile(values: list[float], share: float) -> float:
    ordered = sorted(values)
    return ordered[round(share * (len(ordered) - 1))]


def measure(runs: list[tuple], count: int) -> list[tuple[float, float]]:
    """Time each (argv, env, cwd, before) *count* times, taking turns run by run so that changes in the machine's
    load fall on all of them alike. Returns (median, p90) in ms for each."""
    times = [[] for _ in runs]
    for i in range(count + 2):
        for slot, (argv, env, cwd, before) in enumerate(runs):
            if before:
                before()
            start = time.perf_counter()
            out = subprocess.run(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.PIPE)
            if i >= 2:  # two untimed runs first
                times[slot].append((time.perf_counter() - start) * 1000)
            if out.returncode != 0:
                sys.exit(f"{' '.join(argv)} failed in {cwd}:\n{out.stderr.decode(errors='replace')}")
    return [(statistics.median(t), percentile(t, 0.9)) for t in times]


def cases(world: dict, tool: str) -> list[tuple]:
    env, repos = world["env"], world["repos"]
    stub, shim = str(world["stubs"] / tool), str(world["shims"] / tool)
    session = {**env, "KEYFIT_PROJECT": "bench", "KEYFIT_GITHUB_LOGIN": "bench-user", "GH_TOKEN": TOKEN}
    out = [("direct", [stub], env, repos["repo"], None),
           ("outside", [shim], env, repos["outside"], None),
           ("warm", [shim], env, repos["repo"], None),
           ("session", [shim], session, repos["repo"], None),
           ("version", [shim, "--version"], env, repos["repo"], None),
           ("login", [shim, *LOGIN[tool]], env, repos["repo"], None)]
    if tool in ("aws", "claude", "codex"):
        out.append(("cold", [shim], env, repos["repo"], world["cold"]))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--runs", type=int, default=20)
    parser.add_argument("--keyfit", type=Path, default=Path(__file__).resolve().parents[1],
                        help="the checkout to measure (default: this one)")
    parser.add_argument("--baseline", type=Path, help="another checkout (or a copy of its keyfit/ folder's parent) "
                        "to compare with, run by run")
    parser.add_argument("--python", default=sys.executable, help="the Python the shims run with")
    parser.add_argument("--tools", default=",".join(TOOLS))
    parser.add_argument("--json", type=Path, help="also write the results here")
    args = parser.parse_args()

    checkouts = {"after": args.keyfit.resolve()}
    if args.baseline:
        checkouts = {"before": args.baseline.resolve(), **checkouts}
    root = Path(tempfile.mkdtemp(prefix="keyfit-bench-")).resolve()
    try:
        worlds = {name: build(root / name, path, args.python) for name, path in checkouts.items()}
        results = []
        print(f"python: {args.python}   runs per case: {args.runs}   "
              + "   ".join(f"{name}: {path}" for name, path in checkouts.items()))
        print("milliseconds; 'added' is the median minus the tool's own (direct) median")
        print(f"{'tool':<7} {'case':<8}" + "".join(f" {name + ' median':>14} {'p90':>7} {'added':>7}" for name in worlds))
        for tool in args.tools.split(","):
            per_world = [cases(world, tool) for world in worlds.values()]
            floors = None
            for row in zip(*per_world):
                stats = measure([case[1:] for case in row], args.runs)
                floors = floors or [median for median, _ in stats]
                line = f"{tool:<7} {row[0][0]:<8}"
                for name, (median, p90), floor in zip(worlds, stats, floors):
                    line += f" {median:14.1f} {p90:7.1f} {median - floor:+7.1f}"
                    results.append({"checkout": name, "tool": tool, "case": row[0][0], "median_ms": round(median, 1),
                                    "p90_ms": round(p90, 1), "added_ms": round(median - floor, 1)})
                print(line, flush=True)
                for world in worlds.values():
                    world["warm"]()
        if args.json:
            args.json.write_text(json.dumps({"python": args.python, "runs": args.runs,
                                             "checkouts": {k: str(v) for k, v in checkouts.items()},
                                             "results": results}, indent=2))
    finally:
        shutil.rmtree(root, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
