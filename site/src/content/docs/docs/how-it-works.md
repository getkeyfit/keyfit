---
title: How it works
description: How Keyfit resolves the project, checks its accounts and starts the tool.
sidebar:
  order: 2
---

<!-- Generated from README.md by site/scripts/sync_docs.py. Edit the source, not this file. -->

## How it works

1. **Resolve.** The shim asks git for the folder's common git directory. That way the main checkout and every worktree of a registered repository resolve to the same project, wherever the worktrees live (Superset's included). Outside a registered repository, the nearest registered parent folder counts. If nothing matches, the real tool runs unchanged.
2. **Verify.** Keyfit builds the project's environment and checks each account you mapped. Fast local checks run on every launch: does the profile exist, has the sign-in expired, is the key well-formed. Then come the online checks: who do AWS, Linear, GitHub, Vercel and Claude say you are? A successful online check is reused for 30 minutes. If any check fails, the tool does not start.
3. **Inject.** The real tool starts with that environment: the project's credentials are set, and conflicting ones inherited from your shell are removed. MCP servers that belong to other projects are hidden for that session. Nothing is written into your repository or into the tools' config files.

Some details:

- **Which accounts each tool gets:** `claude` and `codex` get, and check, all of the project's accounts. `aws` gets only the AWS account, `gh` only the GitHub account and `vercel` only the Vercel login.
- **Expired sign-ins are one keypress.** When you start a tool in a terminal and the only problem is an expired or missing sign-in, Keyfit asks `Sign in now? [Y/n]`, runs the sign-in, checks again and starts the tool. Agents and scripts are never asked; they get the message and the fix.
- **Sign-in commands always run**, with the project's environment, so they fix the right account. These are `aws sso login`, `aws login`, `aws configure`, `claude auth …`, `codex login`, `gh auth …` and `vercel login`, `logout`, `switch`, `whoami` and `teams`.
- **Inside a Claude or Codex session,** the `aws`, `gh` and `vercel` commands it runs stay on that session's project, even after a `cd` elsewhere.
- **Claude Code's status line** shows the project and its accounts, unless you configured a status line of your own.
- **Superset:** when Superset's agent wrapper (`~/.superset/bin`) is on PATH, it goes first and the shims right behind it, ahead of everything else. The chain is Superset wrapper → Keyfit → real tool, and Superset's hooks keep working. Without Superset, the shims go first.

## What is checked

| Connection | Map it with | What the tool gets | Checked before it starts |
|---|---|---|---|
| AWS | `keyfit add aws --profile acme-dev` | `AWS_PROFILE` and `AWS_EXPECTED_ACCOUNT_ID` (and the region, if you set one). Inherited AWS keys, tokens, profile, config-file and region variables are removed | A named profile (never `default`) exists, and the account hints in its config match. The SSO or `aws login` session has not expired. Then `aws sts get-caller-identity` must return the expected 12-digit account |
| Linear | `keyfit add linear` | `LINEAR_API_KEY`, plus the project's own variable when the key is an `env:` reference. Every other Linear key variable is removed | The key is present, shaped like a Linear key and not pasted twice. Then Linear's API must report the expected workspace |
| GitHub | `keyfit add github --login octocat` | `GH_TOKEN`, read from `gh`'s stored login for that account. Other GitHub token variables, `GH_HOST` and `GH_CONFIG_DIR` are removed | `gh` has a token for the login, and `api.github.com/user` must return that login. A different git author email or SSH host alias in the repository shows as a warning |
| Vercel | `keyfit add vercel` | The login's folder as `--global-config` (nothing for the CLI's default folder) and the team as `--scope`. `VERCEL_ORG_ID`, `VERCEL_PROJECT_ID`, `VERCEL_TEAM_ID` (and their older `NOW_` names) and `VERCEL_TOKEN` are removed | The folder holds a login, and the repository isn't linked (`.vercel/project.json`, `.vercel/repo.json`) to another team. Then `api.vercel.com` must return the expected username and, with a team, let that login reach the team |
| Claude Code | `keyfit add claude` | The account's config folder: `CLAUDE_CONFIG_DIR`, left unset for the default `~/.claude`. API keys, OAuth tokens, gateway URLs and cloud-provider switches are removed | `claude auth status` must report a claude.ai subscription login (not an API key), in the expected config folder and organization. With `--pin-email`, it must also be the exact login |
| Codex | `keyfit add codex` | `CODEX_HOME`. `CODEX_API_KEY` is removed, and interactive Codex runs without its shared daemon | `auth.json` must hold a ChatGPT login and no API key. With `--pin-account`, it must also be the exact ChatGPT account. This check is local; there is no online check |
| MCP ownership | `keyfit add mcp 'globex_cloud_*'` | Claude gets `--settings` with the servers to hide. Codex gets `-c mcp_servers.NAME.enabled=false` | A Linear server (`mcp.linear.app`) in your user config stays visible only if it reads `LINEAR_API_KEY` or the project's own key variable, both of which hold the project's checked key; Linear servers in the folder's `.mcp.json` are hidden. Servers another project claimed are hidden unless this project claims them too. All other servers stay visible |

Only the accounts you map are touched. For example, a project without a Linear mapping leaves your Linear variables as they are and checks nothing for Linear.

Inside a registered project, `aws`, `gh` and `vercel` don't start until the project has an AWS, GitHub or Vercel account mapped. Also, `aws --profile <another profile>` is refused there, and so are `vercel --scope <another team>`, `vercel --global-config <another folder>` and `vercel --token …`. The exception is AWS profiles of machine-wide tools that serve every project, such as a CI worker. List them in `~/.config/keyfit/registry.toml` and they run unchanged anywhere:

```toml
[settings]
shared_aws_profiles = ["ci-worker"]
```

**Vercel** keeps one login per folder. `keyfit add vercel` records the login in the CLI's default folder (`~/Library/Application Support/com.vercel.cli`) with the team selected there (`--team SLUG` for another). For a second login:

```bash
keyfit add vercel --new --name globex     # creates ~/.config/keyfit/vercel/globex for this project
vercel login                              # inside the project: Keyfit signs in to that folder
keyfit add vercel --name globex --config-dir ~/.config/keyfit/vercel/globex   # records who it is and the team
```

Vercel's MCP server (`https://mcp.vercel.com`) signs in on its own, so Keyfit doesn't check it. If it is signed in to one project's account, claim it for that project with `keyfit add mcp <its name>`; every other project stops seeing it.
