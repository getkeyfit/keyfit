---
title: Limits and turning it off
description: What Keyfit does not cover, and how to skip or remove it.
sidebar:
  order: 4
---

<!-- Generated from README.md by site/scripts/sync_docs.py. Edit the source, not this file. -->

## Limits

- **macOS only, for now.** Keys live in the macOS Keychain, and `keyfit setup` stops on other systems.
- **zsh and bash.** In another shell, put `~/.config/keyfit/bin` first on PATH yourself (after `~/.superset/bin` if you use Superset).
- **Thirteen tools.** Only `claude`, `codex`, `opencode`, `copilot`, `gemini`, `aws`, `gh`, `vercel`, `supabase`, `wrangler`, `convex`, `gcloud` and `firebase` go through Keyfit, when they are found through PATH (typed in a terminal, or run by an agent Keyfit started). These don't go through Keyfit:
  - other commands (terraform, npm, git over SSH), including `vc`, the Vercel CLI's short name, `npx supabase`, `npx wrangler`, `npx convex`, `npx firebase-tools`, `npx @google/gemini-cli`, `bq` and `gsutil` (inside a Claude, Codex, OpenCode, Copilot CLI or Gemini CLI session they get the project's token or account, but no checks of their own; outside one, `npx wrangler` and `npx firebase-tools` follow only the CLI's own folder binding, `npx convex` uses the CLI's own login and `bq` and `gsutil` gcloud's active configuration)
  - tools called by their full path
  - apps that don't start from your shell, such as IDE extensions and desktop apps

  To run any other command with the project's accounts: `keyfit run -- <command>`.
- **Online checks are cached for 30 minutes.** If a key is revoked within that window, Keyfit notices when the cached result expires, or at once with `keyfit status`, which also clears it. A new sign-in, key, token, login or link for any account is checked again right away. When a service cannot be reached, a success from the last 24 hours is accepted with a warning; a wrong account never is.
- **Background sessions only where the project's Claude login has a folder of its own.** Claude Code runs its background sessions (`claude --bg`, agent view, `←` and `/background`) under one supervisor per config folder, and Codex its sessions on one shared server. Each keeps the environment of the start that launched it, for every folder. So in a project whose Claude login is in `~/.claude`, or in a folder another project's login uses too, Claude sessions start with background sessions off, and `claude --bg`, `agents`, `attach` and `respawn` are refused there and inside a session. Give the project a login of its own (`keyfit add claude --new --name NAME`, then sign in there) and they run there, with its accounts; a session agent view starts in another folder (a repository one level below, with `@repo`) gets them too, even when that folder belongs to another project. `claude daemon run` is refused in every project. `codex`, `codex resume` and `codex fork` run without the shared server, and `codex agents`, `queue`, `remote-control` and `app-server` are refused, in a project and inside a session. Outside every project they run as before.
- **One account of each kind per project:** one AWS profile, one GitHub login, one Google account, and so on.
- **Each account has limits of its own,** such as what is checked down to the person and which versions were verified. They are listed under each account in [docs/accounts.md][accounts].

## Turning it off

- **Skip Keyfit once:** `KEYFIT_DISABLE=1 claude` runs that one command without checks, in your unchanged environment. Everything it starts inherits the variable, so an agent started this way runs its own `aws`, `gh`, `vercel`, `supabase`, `wrangler`, `convex`, `gcloud` and `firebase` without Keyfit too.
- **See what would run:** `KEYFIT_DRY_RUN=1 claude` runs the checks in a registered project, then prints the command and the names of the environment variables that would change, and starts nothing. Commands that would run without checks are printed too, with the reason, and don't start either: `supabase`, `wrangler`, `convex`, `gcloud` or `firebase` in a project without an account of their kind, and, in any folder, OpenCode's own commands (`opencode serve`, `opencode mcp`, …) and AWS profiles under `[settings] shared_aws_profiles`. Three things still run as usual: sign-in commands, every other command in a folder that isn't registered, and anything run with `KEYFIT_DISABLE=1`.
- **Turn it off:** `keyfit setup --remove` deletes the shims and PATH files and takes Keyfit's line out of your shell startup files. Open a new terminal afterwards. Your registry stays, so `keyfit setup --rc` turns everything back on.
- **Delete its data too:** `keyfit setup --remove --purge` also deletes `~/.config/keyfit`: the registry, the check cache, and the Claude, Vercel, OpenCode and Gemini CLI login folders Keyfit made (with the logins in them). It asks first (`--yes` skips the question). Keys Keyfit stored in the Keychain are kept; it lists their names so you can delete them in Keychain Access.
- **Uninstall:** run `keyfit setup --remove` first, then `pipx uninstall keyfit` or `brew uninstall keyfit`. Done the other way round, the thirteen tools stop with a note that names the line to remove.

[accounts]: https://github.com/getkeyfit/keyfit/blob/main/docs/accounts.md
