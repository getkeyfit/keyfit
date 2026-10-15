# Contributing to Keyfit

Thanks for helping. Keyfit sits in front of tools people run all day and handles their
credentials, so every change has to hold to a few rules (below): no secret ever leaks, no
user repository is ever written to, and `claude` never feels slower.

## What you need

macOS, Python 3.11 or newer, git and zsh. Nothing to install: Keyfit and its tests use only
the Python standard library.

## Run the tests

```bash
python3 -m unittest discover -s tests                         # everything, exactly as CI runs it
python3 -m unittest discover -s tests -p 'test_shim.py'       # one file
python3 -m unittest discover -s tests -k test_remote_timeout  # tests whose name matches
```

The tests use fake `aws`, `claude`, `gh`, `opencode`, `copilot`, `vercel`, `supabase`, `wrangler`, `convex`, `gcloud`, `firebase`, `npx` and `security` binaries and a temporary config
directory. They never reach the network, your accounts, `~/.config/keyfit`, wrangler's config folder, OpenCode's database, Copilot CLI's `~/.copilot`, gcloud's `~/.config/gcloud`, the firebase CLI's `~/.config/configstore` or your Keychain.
Keep it that way: a new test module sets `KEYFIT_CONFIG_DIR` to a temporary directory at the
top, like the existing ones.

`tests/test_install.py` builds a wheel offline, installs it into a temporary virtualenv and runs
its `keyfit setup` and shims; it is skipped when this Python has no setuptools 77+ or pip.

CI (`.github/workflows/ci.yml`) runs the same command on macOS with Python 3.11, 3.12 and 3.13,
then runs `keyfit setup` into temporary folders and syntax-checks every generated `shell.*` file.

## Run from a checkout

```bash
bin/keyfit --help
bin/keyfit which -v      # which project this folder belongs to, and why
bin/keyfit status        # check every connection of that project
```

`bin/keyfit` runs the package from the checkout without installing it.

The shims that `keyfit setup` writes run the checkout they were written from. If your own
shims point at the checkout you are editing, every branch switch there changes what your
`claude`, `codex`, `opencode`, `copilot`, `aws`, `gh`, `vercel`, `supabase`, `wrangler`, `convex`, `gcloud` and `firebase` run. Work in a separate clone or worktree, and when you try
`setup` itself, point Keyfit at throwaway folders:

```bash
export KEYFIT_CONFIG_DIR="$(mktemp -d)"
bin/keyfit setup         # shims and shell files go into $KEYFIT_CONFIG_DIR; no rc file is changed
```

## Rules

1. **Standard library only at runtime.** Keyfit must run on a plain Python 3.11+. No
   third-party packages in `keyfit/` and no dependencies in `pyproject.toml`. The tests stay
   on `unittest` too, so they run on a fresh machine.

2. **No secret value ever appears in output, logs, argv or the registry.**
   - The registry stores where a secret lives (`env:NAME`, `keychain:service/account`), never
     the value. The same goes for the check cache.
   - A secret is read only when it is needed and reaches the launched tool only through its
     environment. Never put one on a command line: every process on the machine can read
     argv with `ps`.
   - Messages, dry runs and status output name the variable or the Keychain item
     (`secrets.describe`), never the value.
   - MCP configuration refers to variables (`${LINEAR_API_KEY}`), not keys.
   - A test that touches a secret must assert it does not leak: the value is absent from
     stdout and stderr, from the registry and cache files, and from the arguments any fake tool
     received. See `test_mcp_config_holds_no_secret` in `tests/test_linear.py`.

3. **Never write into users' repositories.** Keyfit's state lives in `~/.config/keyfit` (or
   `KEYFIT_CONFIG_DIR`). Reading a project's `.envrc`, `.mcp.json` or git config is fine.
   Creating or changing files in a project folder, its worktrees or its `.git` is not; per-session
   settings reach the tool through its environment or command line (for example Claude's
   `--settings`). Shell rc files change only when the user asks for it explicitly.

4. **Keep the shims fast.** Every `claude`, `codex`, `opencode`, `copilot`, `aws`, `gh`, `vercel`, `supabase`, `wrangler`, `convex`, `gcloud` and `firebase` call goes through a shim.
   With checks cached, a launch should cost at most a few tenths of a second.
   - In a folder that is not registered, the shim looks up the registry (and asks git which
     repository it is in) and hands over to the real tool. Nothing else.
   - No network on the warm path: remote checks go through the cache, run in parallel and have
     timeouts.
   - Avoid a new subprocess per launch, and import heavy modules inside the function that needs
     them, not at the top of a module the shim loads.
   - Time a change to the shim path before and after, for example
     `time KEYFIT_DRY_RUN=1 claude` in a registered project (checks run, the tool does not), or
     `python3 scripts/bench_shim.py --baseline <a checkout of main>`: it compares two checkouts run by
     run, per tool and case, in a throwaway folder with stub tools (no accounts, no network).

## Pull requests

- Keep one change per pull request, with tests for it. Run the full suite before you push;
  CI must pass.
- Add user-visible changes to `CHANGELOG.md` under the unreleased version.
- Never paste real tokens, account IDs or private project names into issues, commits or tests.
