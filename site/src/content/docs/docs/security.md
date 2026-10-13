---
title: Security
description: Everything Keyfit reads, stores and sends.
sidebar:
  order: 5
---

<!-- Generated from docs/security.md by site/scripts/sync_docs.py. Edit the source, not this file. -->

Keyfit decides which credentials `claude`, `codex`, `aws`, `gh` and `vercel` get. This page says what it can see, what it keeps and where, what it sends and to whom, and what it does not protect against. To report a problem, see [the end of this page](#reporting-a-problem); please don't open a public issue.

## In short

- **What it is for:** stopping a tool from using the wrong project's account by accident. It is not a sandbox and not a secrets vault, and it does not protect you from code that already runs as you.
- **No secret values in Keyfit's files.** Keys you paste into Keyfit go into the macOS Keychain.
- **A key or token leaves your Mac only for its own service**, over HTTPS, when Keyfit asks that service whose it is.
- **No telemetry, no update checks, no log files, no writes into your repositories.**
- **Keyfit's output never shows a key or token.**

## What Keyfit can see

Keyfit runs as you, so it could read anything you can. This is what it actually reads:

| What | Why |
|---|---|
| `~/.config/keyfit/registry.toml` and `cache/checks.json` | your projects, and recent successful checks |
| The environment of the shell that starts the tool | to clean it up; an `env:NAME` reference reads the key from `$NAME` |
| Keychain items named by `keychain:service/account` references | the key, read with `/usr/bin/security find-generic-password -w` only when a launch or check needs it |
| `~/.aws/config`, `~/.aws/credentials`, `~/.aws/sso/cache`, `~/.aws/login/cache` | the profile's type and account hints, whether it has a static key, sign-in expiry times. Keyfit never uses or sends the AWS tokens in these files |
| gh's token for the mapped account (`gh auth token --user LOGIN`) | to hand it to the tool as `GH_TOKEN` and ask GitHub whose it is |
| The Vercel login folder's `auth.json` and `config.json` | the token, to ask Vercel whose it is, and the selected team |
| `~/.claude.json` (or the account's), Claude's `settings.json`, `~/.codex/auth.json`, `~/.codex/config.toml` | MCP server lists, organization and account IDs, the login type, whether you set your own status line. Codex tokens are never used or sent |
| In the project folder: `.mcp.json`, `.vercel/project.json` and `repo.json`, the git config | server names to hide, the Vercel team the folder is linked to, the author email and origin host |
| `.envrc` of the repository, on `keyfit init` only | `use aws` and `export LINEAR_API_KEY=$VAR` lines. It is read as text and never run |
| `PATH` | to find the real tool behind each shim |

Programs Keyfit runs, all read-only: `git rev-parse`, `git config user.email` and `git remote get-url origin`; `aws sts get-caller-identity`; `claude auth status --json`; `gh auth token` and `gh auth status`; `vercel whoami` (only to let the CLI renew an expired token); `/usr/bin/security`. Apart from `security`, each is the first one found on PATH (skipping Keyfit's shims and Superset's wrappers), and runs in the current folder, as the tool itself will.

## What Keyfit stores, and where

Everything lives under `~/.config/keyfit` (or `$KEYFIT_CONFIG_DIR`), apart from Keychain items and one optional line in a shell startup file.

| Path | Mode | Contents |
|---|---|---|
| `registry.toml` | 600 | Projects, their folders and account labels: AWS profile names and 12-digit account IDs, Linear workspace slugs, where each key lives (`env:NAME` or `keychain:service/account`), GitHub logins, Vercel users and teams, Claude organizations, Codex plans (plus the email or account ID when you pin one), MCP server name patterns, and the names of Keychain items Keyfit stored. Never a key, token or password |
| `cache/` and `cache/checks.json` | 700, 600 | Successful online checks: time, status and a short label such as `account confirmed`, under a SHA-256 of the project, the connection and a login fingerprint. For Linear, GitHub and Vercel the fingerprint is the first 16 hex characters of a SHA-256 of the key or token |
| `bin/` (the shims), `shell.zsh`, `shell.sh` | 755, 644 | Two-line `sh` scripts and the PATH snippets. Nothing secret |
| `claude/NAME/` | 700 | Only after `keyfit add claude --new`: a config folder for another Claude login, where Claude keeps that login's history and transcripts. Its `.claude.json` (600) marks the project's folders as trusted only where your default Claude login already trusts them, so a new login never trusts more than the old one |
| `vercel/NAME/` | 700 | Only after `keyfit add vercel --new`: the Vercel CLI keeps that login's `auth.json` here |
| macOS Keychain, service `keyfit` | Keychain | Keys you pasted into `keyfit add linear` (`linear-WORKSPACE`) or `keyfit secret set NAME`. They are written through the stdin of `security -i`, never through its arguments |
| Your shell startup file | | One line that loads `shell.zsh` or `shell.sh`, only with `keyfit setup --rc` |

`~/.config/keyfit` itself gets your usual permissions (usually 755). The registry, the cache and Claude's `.claude.json` are owner-only from the first byte. `keyfit setup --remove --purge` deletes the folder but never Keychain items; it lists their names so you can delete them in Keychain Access.

## What Keyfit sends, and to whom

Nothing in folders that aren't registered. In a project, Keyfit only asks each service who you are:

| To | What | When |
|---|---|---|
| `api.linear.app` (`POST /graphql`) | the project's Linear key and the query `{ organization { urlKey name } }` | a Linear check without a fresh cached success, `keyfit status`, `keyfit add linear`, `keyfit init` importing a key from `.envrc`. Only a value shaped like a Linear key is ever sent |
| `api.github.com/user` | gh's token for the account | a GitHub check without a fresh cached success, `keyfit status`, `keyfit add github` |
| `api.vercel.com` (`/v2/user`, `/v2/teams/…`) | the login's token from `auth.json` | a Vercel check without a fresh cached success, `keyfit status`, `keyfit add vercel`, guided `keyfit init` |
| AWS STS, through the real `aws` | whatever `aws sts get-caller-identity --profile P` sends | an AWS check without a fresh cached success, `keyfit status`, `keyfit add aws` when the profile doesn't name its account |
| Anthropic, through the real `claude` | whatever `claude auth status` sends | a Claude check without a fresh cached success, `keyfit status`, `keyfit add claude` |
| Vercel, through the real `vercel` | whatever `vercel whoami` sends to renew its token | only when the stored Vercel token has expired |

Keyfit's own requests use HTTPS with certificate and hostname checks (Python's default; when Python has no CA certificates of its own, Keyfit uses the bundle macOS keeps at `/etc/ssl/cert.pem`). They never follow a redirect, because a redirect would carry the key to another address. Each has a 15 s timeout. Like other tools, they honor `HTTPS_PROXY` and `SSL_CERT_FILE` from your environment. Codex is checked offline, from `auth.json`.

**The cache.** A successful online check is reused for 30 minutes. If a service can't be reached (a connection error, a DNS failure or a timeout; for Vercel also a rate limit or server error), Keyfit retries once. If that also fails, it accepts a success from the last 24 hours for the same login and shows it as a warning. A rejected key, an expired sign-in or a wrong account always fails, and failures are never cached. AWS profiles that aren't SSO or console sign-ins (`credential_process`, static keys, roles) have no login fingerprint: a credential swapped behind the same profile name is noticed when the 30 minutes run out, or at once with `keyfit status`.

## What Keyfit never does

- **Print a key or token.** Output names the variable or the Keychain item, and shows AWS account IDs as their last four digits. `KEYFIT_DRY_RUN=1` lists the names of changed variables only. An internal error shows the exception's type and place, not its message, since messages can quote the file that was read. A key typed where a name or reference belongs (`--key lin_api_…`) is refused without being repeated.
- **Put a secret on a command line**, where `ps` would show it to every process.
- **Send a secret anywhere but its own service** (the table above).
- **Collect telemetry or keep logs.** No analytics, update checks or crash reports.
- **Write into your repositories, or change another tool's logins or configuration.** It never runs `gh auth login` or `gh auth switch`. It runs `aws login`, `aws sso login`, `claude auth login`, `codex login` or `vercel login` only when you answer yes to `Sign in now?`, which it asks only when you start a tool yourself in a terminal and every failed check is fixed by such a sign-in; the sign-in then talks to you directly, and the checks run again before the tool starts. Otherwise it prints them as the fix. The one other exception is `vercel whoami`, which renews an expired Vercel token, as any `vercel` command would.
- **Start a tool after a failed check, a mistake in the registry (`registry.toml has a mistake`), an error inside Keyfit, or from a shim whose Keyfit installation is gone.** The exceptions are `KEYFIT_DISABLE=1`, sign-in commands (they run with the project's environment but without checks), folders that aren't registered, the AWS profiles under `[settings] shared_aws_profiles`, and a network failure within 24 hours of a success, shown as a warning.

## Where secrets end up

`claude` and `codex` start with the project's Linear key (as `LINEAR_API_KEY`, and in the project's own `env:` variable) and gh's token for the project's GitHub account (as `GH_TOKEN`). That token is gh's own login token, with all of its scopes. Inside a session Keyfit started, a `gh` call keeps the session's `GH_TOKEN` instead of asking gh again only when the project's check confirmed exactly that token in the last 30 minutes and gh's sign-ins (the time of `hosts.yml`, never its content) haven't changed since. `aws`, `gh` and `vercel` get only their own account. Everything the tool starts inherits its environment: MCP servers, hooks, the commands an agent runs, and the install scripts of the packages it installs. Keyfit removes other projects' variables only for the kinds of account the project maps. In folders that aren't registered, the tool gets your environment unchanged, apart from `KEYFIT_SHIM_DEPTH`, a counter that stops shims from calling each other in a loop.

## What Keyfit does not protect against

- **Code that already runs as you.** It can read the same files and environment, call the real binaries by full path, set `KEYFIT_DISABLE=1`, edit the registry or remove the shims. It can also read the Keychain items Keyfit stored, without a prompt, through `/usr/bin/security`; that is how Keyfit reads them without asking at every launch. The Keychain protects them at rest and from other users, not from your own processes.
- **An agent that goes around the five commands:** one that reads `~/.aws`, gh's or Vercel's stored logins or the Keychain directly, or runs a tool by its full path. Keyfit only decides what `claude`, `codex`, `aws`, `gh` and `vercel` get when they are started through PATH. Other programs (`vc`, terraform, npm, IDE extensions, desktop apps) don't go through Keyfit.
- **What a tool does with the right account.** An agent with the project's AWS profile can do whatever that profile allows. Give each project's profile only the permissions it needs.
- **Configuration a repository brings with it, once you trust it.** After you trust a folder in Claude, its `.claude/settings.json` applies to the agent's commands, and its `env` can set `KEYFIT_DISABLE`, `KEYFIT_PROJECT` or `AWS_PROFILE`. An `.envrc` you `direnv allow` can do the same. Keyfit can't tell those from your own settings. A project `.mcp.json` server you approve gets the session's environment, or any variable it names, such as `${LINEAR_API_KEY}` or `${GH_TOKEN}`. `keyfit init` imports the repository's `.envrc` lines, so register only repositories you trust and read the `imported …` lines it prints.
- **`KEYFIT_DISABLE=1` beyond the one command.** It is inherited: an agent started with it runs its own `aws`, `gh` and `vercel` without Keyfit too.
- **What is on PATH.** Keyfit runs the first `aws`, `claude`, `codex`, `gh`, `vercel` and `git` on PATH after its shims and Superset's wrappers, as your shell would. A compromised binary or a malicious folder early on PATH wins. That includes relative entries such as `.` or `./node_modules/.bin`, which point into whatever folder you are in.
- **MCP servers Keyfit doesn't see.** It hides servers by name from `~/.claude.json` (user scope), the current folder's `.mcp.json` and `~/.codex/config.toml`. Servers from plugins, claude.ai connectors or Claude's local scope are not hidden, and Keyfit doesn't vet any server. Codex can't hide a server whose name has a dot or `=`, so if such a server must be hidden, `codex` doesn't start until you rename it.
- **Accounts you didn't map, and identities it can't confirm online.** Unmapped accounts are neither checked nor cleaned up. Codex is checked from its local `auth.json` only, and Claude from what `claude auth status` reports.

## How to audit it

- `KEYFIT_DRY_RUN=1 claude` in a project runs the checks and prints the real binary, the arguments Keyfit adds (including the `--settings` JSON with the hidden MCP servers) and the names of the variables it changes. It starts nothing.
- `keyfit which -v` shows which project a folder belongs to, and why. `keyfit status` runs every check online.
- `cat ~/.config/keyfit/registry.toml`, `cat ~/.config/keyfit/cache/checks.json` and `cat ~/.config/keyfit/bin/claude` show plain files with nothing secret in them.
- `keyfit secret list`, or Keychain Access (search for "keyfit"), shows the items Keyfit stored.
- The code uses Python's standard library only. A launch goes through `keyfit/shim.py` (the launch), `resolve.py` (which project), `environment.py` and `adapters/` (injecting and checking), `mcp.py` (hiding MCP servers), `secrets.py` (Keychain and `env:` references), `http.py` (TLS) and `cache.py`.

## Reporting a problem

Please don't report a vulnerability in a public issue, pull request or discussion. Report it privately instead:

- on GitHub, with private vulnerability reporting: the repository's **Security** tab, then **Report a vulnerability** (<https://github.com/FlamurMaliqi/keyfit/security/advisories/new>), or
- by email to <contact@flamur-maliqi.de>.

[SECURITY.md](https://github.com/FlamurMaliqi/keyfit/blob/main/SECURITY.md) says what to include and what counts. Never send real keys or tokens, not even expired ones.
