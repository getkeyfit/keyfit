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
- **Ten tools.** Only `claude`, `codex`, `opencode`, `copilot`, `aws`, `gh`, `vercel`, `supabase`, `wrangler` and `convex` go through Keyfit, when they are found through PATH (typed in a terminal, or run by an agent Keyfit started). These don't go through Keyfit:
  - other commands (terraform, npm, git over SSH), including `vc`, the Vercel CLI's short name, `npx supabase`, `npx wrangler` and `npx convex` (inside a Claude, Codex, OpenCode or Copilot CLI session they get the project's token or account, but no checks of their own; outside one, `npx wrangler` follows only wrangler's own folder binding and `npx convex` uses the CLI's own login)
  - tools called by their full path
  - apps that don't start from your shell, such as IDE extensions and desktop apps

  To run any other command with the project's accounts: `keyfit run -- <command>`.
- **Online checks are cached for 30 minutes.** If a key is revoked within that window, Keyfit notices when the cached result expires, or at once with `keyfit status`. A new AWS SSO or console sign-in, a different Linear key, GitHub token, Vercel login or Supabase token or link, a new wrangler sign-in, a Convex login or deployment, a different Claude organization, a new OpenCode sign-in or a new token for the Copilot CLI account is checked again right away. When a service cannot be reached, a success from the last 24 hours is accepted with a warning; a wrong account never is.
- **One account of each kind per project:** one AWS profile, one Linear workspace, one GitHub login, one Vercel login and team, one Supabase token and organization, one wrangler profile and Cloudflare account, one Convex login and team, one Claude login, one Codex login, one OpenCode database, one GitHub login for Copilot CLI.
- **Vercel is checked by login and team.** `keyfit add vercel` records the team selected in the login, if there is one. A project mapped without a team gets whichever team is selected at the time (`vercel switch` changes it), and a repository linked to another team isn't noticed.
- **Supabase is checked by organization.** A token belongs to a person, who may be in several organizations; Keyfit records the project's and checks the token can see it and the folder's linked project belongs to it. A folder linked to a preview branch shows a warning: Supabase's API doesn't say which organization a branch belongs to. `--db-url` and other direct database connections don't use the account, so Keyfit doesn't check them.
- **Cloudflare is checked by account.** wrangler 4.106 or newer is needed (older ones ignore profiles). A `CLOUDFLARE_API_TOKEN` in the folder's `.env` still beats the profile, as wrangler loads it itself; the account stays pinned, and the online check runs with what wrangler uses there. wrangler matches bound folders by their path as written, so a folder reached through a symlink or moved elsewhere needs its binding again.
- **Convex is checked by team.** A login belongs to a person, who may be in several teams; Keyfit records the project's team and checks that the login sees it and that the folder's deployment belongs to it. The Convex project inside that team is shown, not enforced. Where a project deploy key (`project:…`) is in the folder, the convex CLI accepts no other login, so Keyfit leaves that key in charge and doesn't check it (a warning says so); local and self-hosted deployments aren't checked either. `CONVEX_OVERRIDE_ACCESS_TOKEN` is the CLI's own variable but not documented by Convex; it was verified on convex 1.46.
- **GitHub means github.com.** GitHub Enterprise hosts are not supported, for `gh` or for Copilot CLI.
- **Copilot CLI is checked by GitHub login.** The check confirms the login behind gh's token, not the Copilot plan: GitHub has no public endpoint for that, and Copilot CLI says so itself when it starts. MCP servers of installed Copilot plugins aren't seen by Keyfit, so they aren't hidden. Verified with Copilot CLI 1.0.97.
- **OpenCode is checked by provider.** OpenCode labels a sign-in with the provider's name, not the person, so Keyfit checks that the project's database holds a sign-in for each provider it recorded and that OpenCode itself sees them there, not whose they are. No provider is asked: OpenCode has no cheap call that confirms a sign-in without using it. A key in your shell for another provider (`OPENROUTER_API_KEY`, say) stays, and stops the start only when the database also holds a sign-in for that provider. Verified with OpenCode 2.0.25.
- **Claude is checked by organization, Codex by login type.** Codex only has to be a ChatGPT login without an API key. Neither is checked down to the person unless you pin it with `--pin-email` or `--pin-account`. That way, login pools that rotate between accounts keep working.
- **Linear MCP servers are matched by the key variable they read.** Keyfit puts each project's checked key into `LINEAR_API_KEY`, so one Linear server that reads `${LINEAR_API_KEY}` works in every project, always with that project's workspace. Servers that read another variable are hidden unless it is the project's own `env:` variable. To add such a server once:

  ```bash
  claude mcp add --scope user --transport http linear https://mcp.linear.app/mcp --header 'Authorization: Bearer ${LINEAR_API_KEY}'
  codex mcp add linear --url https://mcp.linear.app/mcp --bearer-token-env-var LINEAR_API_KEY
  ```

## Turning it off

- **Skip Keyfit once:** `KEYFIT_DISABLE=1 claude` runs that one command without checks, in your unchanged environment. Everything it starts inherits the variable, so an agent started this way runs its own `aws`, `gh`, `vercel`, `supabase`, `wrangler` and `convex` without Keyfit too.
- **See what would run:** `KEYFIT_DRY_RUN=1 claude` runs the checks in a registered project, then prints the command and the names of the environment variables that would change, and starts nothing. In a folder that isn't registered, and for sign-in commands, the tool runs as usual.
- **Turn it off:** `keyfit setup --remove` deletes the shims and PATH files and takes Keyfit's line out of your shell startup files. Open a new terminal afterwards. Your registry stays, so `keyfit setup --rc` turns everything back on.
- **Delete its data too:** `keyfit setup --remove --purge` also deletes `~/.config/keyfit`: the registry, the check cache, and the Claude, Vercel and OpenCode login folders Keyfit made (with the logins in them). It asks first (`--yes` skips the question). Keys Keyfit stored in the Keychain are kept; it lists their names so you can delete them in Keychain Access.
- **Uninstall:** run `keyfit setup --remove` first, then `pipx uninstall keyfit` or `brew uninstall keyfit`. Done the other way round, the ten tools stop with a note that names the line to remove.
