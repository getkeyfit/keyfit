# Changelog

All notable changes to Keyfit are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-10-12

First public version. macOS only; Python 3.11 or newer, no other dependencies.

### Added

- **Shims for `claude`, `codex`, `aws` and `gh`.** `keyfit setup` writes them and a small shell
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
  a GitHub account `gh` is signed in to, the current Claude and Codex logins, and a Linear key
  (hidden input, stored in the Keychain). Enter skips; anything already set is left alone; it
  ends with `keyfit status`. `--yes`, or no terminal, registers without asking.
- **`keyfit setup --remove`** undoes setup: it deletes the shims and both PATH files and takes
  Keyfit's line out of `~/.zshrc`, `~/.bash_profile`, `~/.bash_login`, `~/.profile` and
  `~/.bashrc` (or only `--rc PATH`), leaving every other line as it was. The registry stays;
  `--purge` also deletes `~/.config/keyfit` after asking (`--yes` to skip the question).
  Keychain items are never deleted.
- **`keyfit doctor`** checks the installation with a ✔/✘ line each and the fix under every ✘:
  macOS, Python, the shims, their place on PATH (before every real tool, right after Superset's
  wrapper), the real `claude`, `codex`, `aws` and `gh`, the registry, the Keychain and the
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
- **Sign-in always works.** `aws sso login`, `claude auth login`, `codex login` and `gh auth …`
  run even when a check fails, with the project's environment, so they fix the right account.
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
