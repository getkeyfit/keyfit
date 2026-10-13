# Changelog

All notable changes to Keyfit are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **`Sign in now? [Y/n]`.** When you start a tool yourself in a terminal and every failed check is fixed
  by a plain sign-in (`aws login`, `aws sso login`, `claude auth login`, `codex login`, `vercel login`),
  Keyfit offers to run it, checks again and starts the tool. Without a terminal (agents, scripts, CI)
  nothing is asked. gh's sign-in is never run for you, since it switches gh's active account.

### Changed

- **Faster starts, about 30–55 ms per launch.** urllib and TLS load only when a check goes online,
  shims run `python3 -I -S`, GitHub's two git questions run side by side, and `gh` inside a session
  Keyfit started keeps the token its project's check confirmed in the last 30 minutes (until gh's
  sign-ins change). Run `keyfit setup` again to rewrite your shims. `scripts/bench_shim.py` measures
  what a shim adds on your Mac.
- **First run reads like a guide.** `keyfit` alone prints its help with a "start here" list; `setup` and
  `init` come first in it, and every command's `--help` says what it does. After `keyfit setup --rc`,
  Keyfit says what to do next; with `--rc FILE` it prints the matching `--remove --rc FILE`.
  Running `keyfit setup` again (after an upgrade) says when your startup file already loads the shims.
- **`keyfit init` refuses your home folder** (and any folder above it), and a `--root` that doesn't
  exist: every folder under it would have belonged to one project. Outside git, it says that every
  folder below belongs to the project. It also says when Keyfit isn't active in this terminal yet.
- **`keyfit status`** says which of `aws`, `gh` and `vercel` won't start in the project until their
  account is mapped, and how to register a folder that isn't.
- **Every error names the next command.** A tool that isn't installed shows how to install it; an
  unmapped `gh` shows `keyfit add github --login <login>`; `keyfit add aws` lists the profiles there
  are; a rejected AWS access key points at `aws configure --profile P`, which runs inside the project.

### Fixed

- A mistake in `registry.toml` (a typo, `roots` written as a string, a root that is the home folder)
  is reported with its place and the fix instead of a Python traceback, in `keyfit` commands and in
  the shims. A connection that names a missing account, or a misspelled one, fails only its own
  project's check.
- With Python older than 3.11 (macOS's own `python3` is 3.9), Keyfit says which version it needs
  instead of failing on `import tomllib`.
- Shims whose Keyfit installation is gone (uninstalled before `keyfit setup --remove`, or moved) say
  what to do instead of sh's "No such file or directory". The tool still doesn't start.

### Security

From the pre-launch security review:

- **A `.git` file or symlink no longer lends a folder another project.** A folder (for example an
  unpacked archive) whose `.git` pointed at a registered repository resolved to that project. A
  worktree now counts only when its repository lists it; a moved worktree needs `git worktree repair`.
- **Unclear command lines always run the checks.** `claude --add-dir auth` or `codex -C login` was
  taken for a sign-in command and started a session without checks.
- **`aws --prof other` is refused** like `--profile other`: the AWS CLI accepts abbreviations.
- **A session whose project is no longer registered** runs no `aws`, `gh` or `vercel` instead of
  running them unchecked.
- **An account kind Keyfit does not know** (a typo such as `githb`) fails its check instead of warning.
- **No key in error output.** A malformed `~/.aws/credentials` line (which can be a secret key) was
  printed in the "internal error" message; internal errors now show the exception type and place
  only. A key typed where a reference or name belongs (`--key lin_api_…`, `secret set lin_api_…`) is
  refused without being repeated, and hidden prompts never fall back to echoing.
- **Keys go only to their own service.** Only values shaped like a Linear key are sent to Linear
  (`--key env:VAR` and `.envrc` imports could name any variable), and Keyfit's HTTPS checks no
  longer follow redirects, which would have carried the key or token to the new address.
- **Owner-only files from the first byte:** the registry, the check cache and a new Claude
  account's `.claude.json`; the cache folder and Claude account folders are 700.
- **Control characters are escaped** in check output, so text from a repository
  (`.vercel/project.json`) cannot drive the terminal.
- **The loop guard counts every launch**, so a wrapper that calls a tool by name stops after a few
  rounds in unregistered folders too.
- **`bin/keyfit` runs Python isolated**, so a repository it runs in cannot replace a module.
- **Codex never sees a server it cannot hide.** Codex splits a `-c` key on every dot and does not
  understand quotes, so a server name with a dot or `=` that had to be hidden either stayed visible
  or broke the start. Such a server now stops `codex` with a message to rename it; names with inner
  spaces are passed bare, which Codex understands.
- **A new Claude login trusts no more than your default one.** `keyfit add claude --new` pre-trusted
  every folder of the project; trust is what lets a repository's own hooks and settings run, so it
  now carries over only folders your default login already trusts.
- **Release workflow:** actions pinned to commit hashes, `build` and `twine` to versions.
- `docs/security.md` now says plainly what Keyfit sees, stores, sends and does not protect against.

## [0.1.0] - 2026-10-12

First public version. macOS only; Python 3.11 or newer, no other dependencies.

### Added

- **Shims for `claude`, `codex`, `aws`, `gh` and `vercel`.** `keyfit setup` writes them and a small shell
  file that puts them on PATH in new terminals (right after Superset's agent wrapper when it is
  there, so its hooks keep working). You keep typing the commands as usual.
- **zsh and bash.** `keyfit setup` writes a PATH file for each; `keyfit setup --rc` adds the
  line to `~/.zshrc`, or for bash to the login profile (and `~/.bashrc` when the profile sources
  it). `--shell zsh|bash` overrides `$SHELL`, `--rc PATH` picks another file, `--zshrc PATH`
  still works. On other systems than macOS, `keyfit setup` says so and stops.
- **Install with pip, pipx or Homebrew.** Keyfit is a self-contained package (`pip install
  keyfit`, a Homebrew formula for a tap); the shims and the Claude status line run the
  installed package with the Python that ran `keyfit setup`. `keyfit --version` prints the
  version.
- **Guided first run.** In a terminal, `keyfit init` registers the repository, then offers what
  it finds on this Mac one at a time: an AWS profile from `~/.aws/config` (or `AWS_CONFIG_FILE`),
  a GitHub account `gh` is signed in to, the current Claude, Codex and Vercel logins, and a Linear key
  (hidden input, stored in the Keychain). Enter skips; anything already set is left alone; it
  ends with `keyfit status`. `--yes`, or no terminal, registers without asking.
- **`keyfit setup --remove`** undoes setup: it deletes the shims and both PATH files and takes
  Keyfit's line out of `~/.zshrc`, `~/.bash_profile`, `~/.bash_login`, `~/.profile` and
  `~/.bashrc` (or only `--rc PATH`), leaving every other line as it was. The registry stays;
  `--purge` also deletes `~/.config/keyfit`, with the Claude and Vercel login folders Keyfit made, after
  asking (`--yes` to skip the question).
  Keychain items are never deleted.
- **`keyfit doctor`** checks the installation with a ✔/✘ line each and the fix under every ✘:
  macOS, Python, the shims, their place on PATH (before every real tool, right after Superset's
  wrapper), the real `claude`, `codex`, `aws`, `gh` and `vercel`, the registry, the Keychain and the
  current folder's project. It never reads a secret.
- **Projects recognised by folder.** `keyfit init` registers a repository. Every subfolder and
  every git worktree of it, wherever it lives on disk (Superset's included), belongs to that
  project, and so do worktrees of repositories nested inside a project folder. `keyfit which`
  tells you which project a folder belongs to and why; `keyfit projects` lists them all. In a
  folder that is not registered, every tool runs exactly as before.
- **Verified accounts per project.** Before a tool starts, Keyfit hands it only the project's
  accounts, removes conflicting credentials inherited from your shell, and checks each account.
  When one is wrong or expired, the tool does not start and you see the command that fixes it.
  - AWS: a named profile and the account it must resolve to (`keyfit add aws`, or imported
    from a `use aws` line in `.envrc` on `keyfit init`); a profile found through `AWS_CONFIG_FILE`
    keeps that file. `aws --profile <another project's profile>` is refused inside a project.
  - Linear: one API key per project, read from an environment variable or the macOS Keychain,
    checked against the expected workspace (`keyfit add linear`).
  - GitHub: the project's account from `gh`'s stored logins, checked against the expected login;
    a different git author email or SSH host shows as a warning (`keyfit add github`).
  - Claude Code: the project's login, checked to be a claude.ai subscription in the expected
    organisation, optionally the exact login (`keyfit add claude`, `--new` for another account).
  - Codex: the project's ChatGPT login, optionally pinned to one account (`keyfit add codex`).
  - Vercel: the project's login (the CLI's global config folder) and team, checked with Vercel's API
    against the expected username and team membership (`keyfit add vercel`, `--new` for another
    login). `vercel` gets the folder (`--global-config`) and the team (`--scope`); inside a project,
    `--scope` or `--global-config` of another team or folder and `--token` are refused, and so is a
    repository linked (`.vercel/project.json`, `.vercel/repo.json`) to another team. Inherited
    `VERCEL_ORG_ID`, `VERCEL_PROJECT_ID`, `VERCEL_TEAM_ID` and `VERCEL_TOKEN` are removed.
- **Sign-in always works.** `aws sso login`, `claude auth login`, `codex login`, `gh auth …` and
  `vercel login` (also `logout`, `switch`, `whoami`, `teams`) run even when a check fails, with the
  project's environment (and for `vercel`, the project's login folder), so they fix the right account.
- **MCP servers per project.** In a project, Claude and Codex see only that project's Linear
  server and never a server another project owns. Your servers keep their names.
- **`keyfit add mcp NAME…`** marks MCP servers (names or patterns such as `'globex_cloud_*'`) as
  belonging to the current project; every other project stops seeing them.
- **Status line.** Claude Code sessions started through Keyfit show the project and its
  accounts, unless you configured a status line of your own.
- **`keyfit status`** checks every connection of the current project (`--offline` for local
  checks only).
- **Fast, forgiving checks.** Successful remote checks are cached for 30 minutes. A network
  blip is retried once, then a success from the last 24 hours is accepted with a warning; a
  wrong account never is.
- **Switches.** `KEYFIT_DRY_RUN=1 <tool>` shows what would run and which environment variables
  would change (names only, never values). `KEYFIT_DISABLE=1 <tool>` skips Keyfit once.
  Removing the shell line turns Keyfit off.
- **Keys stored for you.** `keyfit add linear` without `--key` asks for the key with hidden
  input (or reads one line from stdin in scripts), checks it with Linear, then stores it in the
  macOS Keychain. `keyfit secret set NAME` stores any key the same way and prints its
  `keychain:keyfit/NAME` reference; `keyfit secret list` shows the names. Values never appear
  in output or process arguments.
- The registry (`~/.config/keyfit/registry.toml`) stores where secrets live, never their values.
- **Documentation.** A README for first-time users, `docs/security.md` (everything Keyfit
  reads, stores and sends, and its limits) and `docs/troubleshooting.md`.

### Removed

- WorkOS and Bitwarden Secrets Manager support from the private prototype are not part of the
  public version.
