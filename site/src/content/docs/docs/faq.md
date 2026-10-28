---
title: FAQ
description: Multiple accounts for Claude Code, Codex, AWS and gh, one per client, and how Keyfit compares.
sidebar:
  order: 7
---

<!-- Generated from README.md by site/scripts/sync_docs.py. Edit the source, not this file. -->

### Can I use Claude Code and Codex with multiple accounts, one per client?

Yes: one login per project, and several projects side by side in different terminals. `keyfit add claude --new --name globex` makes a Claude config folder of its own for a second login (you sign in there once), and `keyfit add codex --codex-home PATH` maps a Codex login kept in another folder. In that project, `claude` and `codex` start with its login and its other accounts. Claude is checked by organization, Codex by being a ChatGPT login without an API key; neither is tied to one login. See [Claude Code][claude] and [Codex][codex].

### How do I use a different AWS profile or GitHub account per project?

In the repository, run `keyfit add aws --profile acme-dev` and `keyfit add github --login octocat`, or let `keyfit init` offer them. From then on, `aws` there runs with that profile and must reach the account recorded for it, and `gh` runs with that login's token while gh's active account stays as it is. The `aws` and `gh` commands an agent runs get the same, even after a `cd` elsewhere. This is the account your `aws` commands use; Keyfit doesn't set up Claude Code on Bedrock. See [AWS][aws] and [GitHub][github].

### Does Keyfit switch accounts?

No. The folder you are in picks the project, and Keyfit checks that project's accounts before the tool starts. The credentials go into that one tool's environment; gh's active account and the login in `~/.claude` stay as they are. If a check fails, the tool doesn't start.

### Does it work with account switchers such as claude-swap and codex-multi-auth?

Yes. Keyfit never ties a project to one login. Claude is checked by organization: in a project added with a personal account any Claude login works, and in one of an organization any login of that organization. Codex only has to be a ChatGPT login. So [claude-swap](https://github.com/realiti4/claude-swap) and [codex-multi-auth](https://github.com/ndycode/codex-multi-auth) can move between your logins. If claude-swap switches an organization's project to an account outside it, Keyfit doesn't start Claude. The first `keyfit setup` offers to install both.

### Why not `CLAUDE_CONFIG_DIR` or `CODEX_HOME` aliases, direnv or aws-vault?

Each of them picks an account, but none checks whose it is before the tool starts:

- **An alias** such as `alias claude-acme='CLAUDE_CONFIG_DIR=~/.claude-acme claude'` sets the same variable Keyfit sets. You still have to remember which alias to type, nothing checks whose login is in the folder, and the agent still sees the keys your shell holds for other clients.
- **direnv** sets variables per folder from an `.envrc` and trusts them; it doesn't ask the service who you are. `keyfit init` imports the `use aws PROFILE ACCOUNT_ID` and `export LINEAR_API_KEY=$VARIABLE` lines of a repository's `.envrc`.
- **aws-vault and Granted** keep AWS keys and sign-ins, and you pick the profile. A profile whose `credential_process` runs one of them is checked like any other.

### How is it different from cloudpin?

[cloudpin](https://github.com/paureis/cloudpin) also stops cloud CLIs on the wrong account. It checks each command before it runs, yours and your agents', against a `.cloudpin.yml` that you commit and the team shares. It also runs on Linux and Windows, and covers `az`, `kubectl` and `helm`. Keyfit needs nothing in the repository, also checks the agents' own logins (Claude, Codex, …), removes other projects' keys from the environment and hides their MCP servers, and runs only on macOS.

### Is it for rotating subscriptions around usage limits?

No. Each project has one login of each kind, and Keyfit never moves it to another account. It keeps clients' accounts, or work and personal ones, apart; it doesn't spread usage across them.

### Does it work with git worktrees and Superset?

Yes. Every worktree of a registered repository belongs to its project, wherever it lives: Keyfit asks git for the repository's common git directory. Superset's agent wrapper stays first on PATH with Keyfit's shims right behind it, so Superset's hooks keep working. wrangler and the firebase CLI bind sign-ins to folders, so a new worktree may need a binding of its own; started in a terminal, Keyfit offers it (`Bind this folder now? [Y/n]`).

### Does it work with Claude Desktop, IDE extensions or on Linux?

No. The thirteen commands go through Keyfit when they are typed in a terminal or run by an agent Keyfit started; apps that don't start from your shell, such as Claude Desktop and IDE extensions, don't. Keyfit runs only on macOS for now: keys live in the macOS Keychain.

[claude]: https://github.com/getkeyfit/keyfit/blob/main/docs/accounts.md#claude-code
[codex]: https://github.com/getkeyfit/keyfit/blob/main/docs/accounts.md#codex
[aws]: https://github.com/getkeyfit/keyfit/blob/main/docs/accounts.md#aws
[github]: https://github.com/getkeyfit/keyfit/blob/main/docs/accounts.md#github
