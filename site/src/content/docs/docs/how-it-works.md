---
title: How it works
description: How Keyfit resolves the project, checks its accounts and starts the tool.
sidebar:
  order: 2
---

<!-- Generated from README.md by site/scripts/sync_docs.py. Edit the source, not this file. -->

## How it works

1. **Resolve.** The shim asks git for the folder's common git directory. That way the main checkout and every worktree of a registered repository resolve to the same project, wherever the worktrees live (Superset's included). A submodule or a repository nested in a checkout belongs to the checkout around it, in worktrees too. Outside a registered repository, the nearest registered parent folder counts. If nothing matches, the real tool runs unchanged. If git can't answer inside a repository (after a macOS update broke the Command Line Tools, say), the tool doesn't start; `keyfit doctor` shows why.
2. **Verify.** Keyfit builds the project's environment and checks each account you mapped. Fast local checks run on every launch: does the profile exist, has the sign-in expired, is the key well-formed. Then come the online checks: who does each service say you are? A successful online check is reused for 30 minutes. If any check fails, the tool does not start.
3. **Inject.** The real tool starts with that environment: the project's credentials are set, and conflicting ones inherited from your shell are removed. MCP servers that belong to other projects are hidden for that session. Nothing is written into your repository or into the tools' config files.

Along the way:

- **Expired sign-ins are one keypress.** In a terminal, when the only problem is an expired or missing sign-in, Keyfit asks `Sign in now? [Y/n]`, runs the sign-in, checks again and starts the tool. Agents, scripts and commands inside a session Keyfit started are never asked; they get the message and the fix.
- **Sign-in commands always run**, with the project's environment, so they fix the right account: `aws sso login`, `gh auth login`, `codex login` and the like. A few that would change a login the project doesn't use are stopped, with the right command instead.
- **Other accounts are refused.** Inside a project, `aws --profile <another profile>`, `vercel --scope <another team>` and similar options that point a tool at another account don't run, and neither does an agent pointed at another project's folder (`codex -C ~/code/globex`, `opencode ~/code/globex`). [docs/accounts.md][accounts] lists them per tool.
- **Agent sessions stay on their project.** The `aws`, `gh`, `vercel`, `supabase`, `wrangler`, `convex`, `gcloud` and `firebase` commands an agent runs stay on that session's project, even after a `cd` elsewhere.
- **Claude Code's status line** shows the project and its accounts, unless you configured a status line of your own.
- **Superset:** when Superset's agent wrapper (`~/.superset/bin`) is on PATH, it goes first and the shims right behind it, ahead of everything else. The chain is Superset wrapper → Keyfit → real tool, and Superset's hooks keep working (for OpenCode too: Keyfit leaves the `OPENCODE_CONFIG_DIR` the wrapper sets). Without Superset, the shims go first.

## What is checked

**Agents** get, and check, all of the project's accounts:

| Agent | Command | Keyfit confirms |
|---|---|---|
| <picture><source media="(prefers-color-scheme: dark)" srcset="https://keyfit.dev/logos/claude-white.svg" /><img height="16" align="top" alt="" src="https://keyfit.dev/logos/claude.svg" /></picture> &nbsp;[Claude Code][claude] | `claude` | the Claude organization |
| <picture><source media="(prefers-color-scheme: dark)" srcset="https://keyfit.dev/logos/openai-white.svg" /><img height="16" align="top" alt="" src="https://keyfit.dev/logos/openai.svg" /></picture> &nbsp;[Codex][codex] | `codex` | a ChatGPT login, without an API key |
| <picture><source media="(prefers-color-scheme: dark)" srcset="https://keyfit.dev/logos/opencode-white.svg" /><img height="16" align="top" alt="" src="https://keyfit.dev/logos/opencode.svg" /></picture> &nbsp;[OpenCode][opencode] | `opencode` | OpenCode's sign-ins |
| <picture><source media="(prefers-color-scheme: dark)" srcset="https://keyfit.dev/logos/copilot-white.svg" /><img height="16" align="top" alt="" src="https://keyfit.dev/logos/copilot.svg" /></picture> &nbsp;[Copilot CLI][copilot] | `copilot` | the GitHub account it runs as |
| <picture><source media="(prefers-color-scheme: dark)" srcset="https://keyfit.dev/logos/gemini-white.svg" /><img height="16" align="top" alt="" src="https://keyfit.dev/logos/gemini.svg" /></picture> &nbsp;[Gemini CLI][gemini] | `gemini` | the Google account it is signed in to |

**CLIs** get only their own account:

| Service | Command | Keyfit confirms |
|---|---|---|
| <picture><source media="(prefers-color-scheme: dark)" srcset="https://keyfit.dev/logos/aws-white.svg" /><img height="16" align="top" alt="" src="https://keyfit.dev/logos/aws.svg" /></picture> &nbsp;[AWS][aws] | `aws` | the account behind the profile |
| <picture><source media="(prefers-color-scheme: dark)" srcset="https://keyfit.dev/logos/github-white.svg" /><img height="16" align="top" alt="" src="https://keyfit.dev/logos/github.svg" /></picture> &nbsp;[GitHub][github] | `gh` | the login |
| <picture><source media="(prefers-color-scheme: dark)" srcset="https://keyfit.dev/logos/vercel-white.svg" /><img height="16" align="top" alt="" src="https://keyfit.dev/logos/vercel.svg" /></picture> &nbsp;[Vercel][vercel] | `vercel` | the login and the team |
| <picture><source media="(prefers-color-scheme: dark)" srcset="https://keyfit.dev/logos/supabase-white.svg" /><img height="16" align="top" alt="" src="https://keyfit.dev/logos/supabase.svg" /></picture> &nbsp;[Supabase][supabase] | `supabase` | the organization, and that the linked project belongs to it |
| <picture><source media="(prefers-color-scheme: dark)" srcset="https://keyfit.dev/logos/cloudflare-white.svg" /><img height="16" align="top" alt="" src="https://keyfit.dev/logos/cloudflare.svg" /></picture> &nbsp;[Cloudflare][cloudflare] | `wrangler` | the account behind wrangler's sign-in |
| <picture><source media="(prefers-color-scheme: dark)" srcset="https://keyfit.dev/logos/convex-white.svg" /><img height="16" align="top" alt="" src="https://keyfit.dev/logos/convex.svg" /></picture> &nbsp;[Convex][convex] | `convex` | the team, and that the folder's deployment belongs to it |
| <picture><source media="(prefers-color-scheme: dark)" srcset="https://keyfit.dev/logos/gcloud-white.svg" /><img height="16" align="top" alt="" src="https://keyfit.dev/logos/gcloud.svg" /></picture> &nbsp;[Google Cloud][gcloud] | `gcloud` | the Google account and the project |
| <picture><source media="(prefers-color-scheme: dark)" srcset="https://keyfit.dev/logos/firebase-white.svg" /><img height="16" align="top" alt="" src="https://keyfit.dev/logos/firebase.svg" /></picture> &nbsp;[Firebase][firebase] | `firebase` | the Google account and the Firebase project |

**Also for agents:** the [Linear][linear] workspace behind the key, and the Google account behind the project's [Gmail, Drive and Calendar][google-mcp] MCP servers. [MCP servers][mcp] that belong to other projects are hidden.

Each link goes to that account's section in [docs/accounts.md][accounts]: how to map it, what the tool gets, what is checked, and its limits.

[accounts]: https://github.com/FlamurMaliqi/keyfit/blob/main/docs/accounts.md
[claude]: https://github.com/FlamurMaliqi/keyfit/blob/main/docs/accounts.md#claude-code
[codex]: https://github.com/FlamurMaliqi/keyfit/blob/main/docs/accounts.md#codex
[opencode]: https://github.com/FlamurMaliqi/keyfit/blob/main/docs/accounts.md#opencode
[copilot]: https://github.com/FlamurMaliqi/keyfit/blob/main/docs/accounts.md#copilot-cli
[gemini]: https://github.com/FlamurMaliqi/keyfit/blob/main/docs/accounts.md#gemini-cli
[aws]: https://github.com/FlamurMaliqi/keyfit/blob/main/docs/accounts.md#aws
[github]: https://github.com/FlamurMaliqi/keyfit/blob/main/docs/accounts.md#github
[vercel]: https://github.com/FlamurMaliqi/keyfit/blob/main/docs/accounts.md#vercel
[supabase]: https://github.com/FlamurMaliqi/keyfit/blob/main/docs/accounts.md#supabase
[cloudflare]: https://github.com/FlamurMaliqi/keyfit/blob/main/docs/accounts.md#cloudflare
[convex]: https://github.com/FlamurMaliqi/keyfit/blob/main/docs/accounts.md#convex
[gcloud]: https://github.com/FlamurMaliqi/keyfit/blob/main/docs/accounts.md#google-cloud
[firebase]: https://github.com/FlamurMaliqi/keyfit/blob/main/docs/accounts.md#firebase
[linear]: https://github.com/FlamurMaliqi/keyfit/blob/main/docs/accounts.md#linear
[google-mcp]: https://github.com/FlamurMaliqi/keyfit/blob/main/docs/accounts.md#gmail-drive-and-calendar
[mcp]: https://github.com/FlamurMaliqi/keyfit/blob/main/docs/accounts.md#mcp-ownership
