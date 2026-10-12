---
title: Security
description: Everything Keyfit reads, stores and sends.
sidebar:
  order: 5
---

<!-- Generated from docs/security.md by site/scripts/sync_docs.py. Edit the source, not this file. -->

Keyfit decides which credentials `claude`, `codex`, `aws`, `gh` and `vercel` get, so this page lists everything it reads, stores and sends. To report a vulnerability, see [SECURITY.md](https://github.com/FlamurMaliqi/keyfit/blob/main/SECURITY.md); please don't open a public issue.

## In short

- **No secret values in Keyfit's files.** The registry and the check cache hold references and labels only. The one place Keyfit puts a secret is the macOS Keychain, and only for keys you type into `keyfit add linear` or `keyfit secret set`.
- **Where a secret value goes:** into the environment of the tool Keyfit starts, and to the service it belongs to when Keyfit asks that service whose key it is. The Linear key goes only to Linear, the GitHub token only to GitHub and the Vercel token only to Vercel.
- **No telemetry, no log files, no writes into your repositories.** Keyfit never changes another tool's login.
- **What it protects against:** using the wrong account by accident. It is not a sandbox, and it does not protect against malicious code running as you.

## What Keyfit reads

### Files

| File | When | Why |
|---|---|---|
| `~/.config/keyfit/registry.toml` | every shim launch and most `keyfit` commands | your projects and their accounts |
| `~/.config/keyfit/cache/checks.json` | launches and `keyfit run` | recent successful checks (see [the verification cache](#the-verification-cache)) |
| Environment variables named by `env:` references, such as `$LINEAR_ACME_API_KEY` | launches in that project | the key to hand to the tool |
| `~/.aws/config` (or `$AWS_CONFIG_FILE`, or the project's `config_file`) | AWS checks, `keyfit add aws`, guided `keyfit init` | the profile's type, its account hints, the list of profile names |
| `~/.aws/credentials` (or `$AWS_SHARED_CREDENTIALS_FILE`) | AWS checks | only whether the profile has a static access key there, so Keyfit can warn about it |
| `~/.aws/sso/cache/*.json`, `~/.aws/login/cache/*.json` (or `$AWS_LOGIN_CACHE_DIRECTORY`) | AWS checks | expiry times, whether a refresh token exists, and the account of a console sign-in. The tokens in these files are never used or sent by Keyfit |
| `~/.claude.json` (or `.claude.json` in the account's config folder) | `claude` launches, `keyfit add mcp` | `mcpServers`, to hide other projects' servers, and the organization and account IDs under `oauthAccount`, to notice a changed login |
| `settings.json` in the Claude config folder | `claude` launches | only whether you set a `statusLine` of your own |
| `.mcp.json` in the current folder | `claude` launches in a project | which project-scope servers to disable for this session |
| `~/.codex/auth.json` (or `$CODEX_HOME/auth.json`) | Codex checks, `keyfit add codex`, guided `keyfit init` | the login mode, whether an API key is stored, and the plan and account ID from the ID token's claims. Tokens are not used or sent |
| `~/.codex/config.toml` (or `$CODEX_HOME/config.toml`) | `codex` launches, `keyfit add mcp` | `mcp_servers`, to hide other projects' servers |
| `auth.json` and `config.json` in the account's Vercel folder (the CLI's default `~/Library/Application Support/com.vercel.cli`, or the account's `config_dir`) | Vercel checks, `keyfit add vercel`, guided `keyfit init` | from `auth.json`: the token, to ask Vercel whose it is, its expiry and whether a refresh token exists. From `config.json`: `currentTeam`, the team selected in that login. Neither file is written by Keyfit |
| `.vercel/project.json`, `.vercel/repo.json`, `.now/project.json` in the current folder and its parents up to the repository root (and in the folder `vercel --cwd` names) | Vercel checks, `vercel` launches | only `orgId`: the team the repository is linked to |
| `.envrc` of the repository | `keyfit init` only | `use aws PROFILE ACCOUNT_ID` and `export LINEAR_API_KEY=$VARIABLE` lines. It is read as text and never run |
| Your shell startup files (`~/.zshrc` or `$ZDOTDIR/.zshrc`, `~/.bash_profile`, `~/.bash_login`, `~/.profile`, `~/.bashrc`) | `keyfit setup`, `keyfit setup --remove`, `keyfit doctor` | where Keyfit's line goes, whether it is there, and removing it |
| `PATH`, and the `.keyfit-shims` marker file in shim folders | every launch | to find the real tool behind the shim, skipping Keyfit's shims and Superset's wrappers (`~/.superset/bin`, `~/.superset-*/bin`) |
| `/etc/ssl/cert.pem` | HTTPS checks | the CA bundle macOS maintains, used only when Python has no CA certificates of its own |

### Programs

Keyfit runs the real programs, never a shim or wrapper:

| Command | When | Why |
|---|---|---|
| `git -C <folder> rev-parse --path-format=absolute --git-common-dir --show-toplevel` | every launch and most commands | which repository, and therefore which project, a folder belongs to |
| `git config user.email`, `git remote get-url origin` | GitHub checks, `keyfit add github` | to warn when commits would carry another identity |
| `aws sts get-caller-identity --profile P --query Account --output text` | AWS checks, `keyfit add aws` | which account the profile is signed in to (see [network calls](#network-calls)) |
| `claude auth status --json` | Claude checks, `keyfit add claude`, guided `keyfit init` | whether the login is a claude.ai subscription, plus its config folder, organization, email and plan |
| `gh auth token --hostname github.com --user LOGIN` | launches that include GitHub, `keyfit add github` | the token `gh` stored for that account. This only reads it: Keyfit never runs `gh auth login`, `gh auth switch` or anything else that changes gh's logins |
| `vercel whoami` (with `--global-config DIR` for a folder other than the CLI's default) | Vercel checks, `keyfit add vercel`, only when the stored access token has expired | the CLI renews its own token, as every `vercel` command does before it starts. Keyfit ignores the output and reads the renewed token from `auth.json`. It runs with `VERCEL_TELEMETRY_DISABLED=1` |
| `gh auth status --hostname github.com` | guided `keyfit init` | the accounts gh is signed in to (it prints logins; tokens stay masked) |
| `/usr/bin/security find-generic-password -s SERVICE -a ACCOUNT` | `keychain:` references, `keyfit secret list` | with `-w` it reads the value, but only when the value is needed for a launch or a check. `keyfit secret list` runs it without `-w`, which only checks that the item exists |
| `/usr/bin/security -i` with `add-generic-password -U` | `keyfit add linear` (pasted key), `keyfit secret set` | to store a key. The command goes to the stdin of `security -i`, never into its arguments |
| `/usr/bin/security list-keychains` | `keyfit doctor` | whether the Keychain answers; it reads no item |

## What Keyfit stores

Everything Keyfit writes lives under `~/.config/keyfit` (or `$KEYFIT_CONFIG_DIR`). The two exceptions are your Keychain and, only when you ask for it, one line in a shell startup file.

| Path | Contents |
|---|---|
| `registry.toml` (mode 600) | Projects: names, folders, connections. Accounts: identity labels only. These are AWS profile names with their 12-digit account IDs (and the AWS config file, when not the default), Linear workspace slugs, and where each key lives (`env:NAME` or `keychain:service/account`). It also holds GitHub logins (with the expected git email and SSH host alias, if any), Vercel login folders with the username and team (slug and ID), the Claude config folder with organization ID and name (plus the email when pinned), and the Codex home with its plan (plus the account ID when pinned). Finally: MCP server name patterns, `[settings]`, and the names of Keychain items Keyfit stored. It never holds a key, token or password |
| `cache/checks.json` (mode 600) | successful online checks; see below |
| `bin/claude`, `bin/codex`, `bin/aws`, `bin/gh`, `bin/vercel`, `bin/.keyfit-shims` | the shims: two-line `sh` scripts that run Keyfit with the Python that ran `keyfit setup` |
| `shell.zsh`, `shell.sh` | the PATH snippets your startup file loads |
| `claude/NAME/` | Only after `keyfit add claude --new --name NAME`: a config folder for another Claude login. It holds symlinks to your shared Claude settings (`settings.json`, `keybindings.json`, `CLAUDE.md`, `skills`, `commands`, `agents`) and a `.claude.json` that marks the project's folders as trusted. Claude Code keeps that login's credentials itself, as it does for any config folder |
| `vercel/NAME/` (mode 700) | Only after `keyfit add vercel --new --name NAME`: an empty folder for another Vercel login. `vercel login` inside the project then writes that login's `auth.json` and `config.json` into it; Keyfit only reads them |

**Keychain.** Keyfit creates generic passwords with the service `keyfit`:

- `linear-WORKSPACE`, made by `keyfit add linear` when you paste a key
- `NAME`, made by `keyfit secret set NAME`

`keyfit secret list` shows their names. Keychain Access shows them too (search for "keyfit"). Keyfit only stores printable ASCII values, and it reads each value back once to confirm it was stored. `keyfit setup --remove --purge` keeps these items and prints their names. Delete them in Keychain Access or with `security delete-generic-password -s keyfit -a NAME`.

**Shell startup file.** `keyfit setup --rc` appends one line that loads `shell.zsh` or `shell.sh`. Without `--rc`, Keyfit only prints the line. `keyfit setup --remove` takes it out again, with the blank line before it.

## The verification cache

`~/.config/keyfit/cache/checks.json` (mode 600) remembers successful online checks, so daily launches don't wait for the network.

- **What is in it.** Each entry has a key, the time of the check, its status and a short detail such as `account confirmed` or `Acme (team) confirmed`. The key is a SHA-256 hash of four things: the project name, the connection kind, the connection's registry entry, and a fingerprint of the current login. No key, token or password is stored, not even hashed on its own.
- **The fingerprint** changes when the identity behind a connection changes, and then the cached entry no longer matches:
  - AWS: the modification time of the SSO or console sign-in cache file, so a new sign-in counts
  - Linear, GitHub and Vercel: the first 16 hex characters of a SHA-256 of the key or token
  - Claude: the organization ID (and the account ID when pinned) in `.claude.json`
- **30 minutes (fresh).** A launch reuses a successful check for 30 minutes instead of asking the service again. Local checks (files, expiry times, key shape) still run on every launch.
- **24 hours (stale), for network failures only.** Sometimes a service doesn't answer: a connection error, a DNS failure or a timeout. Keyfit then retries once. If that also fails, it accepts a success from the last 24 hours and shows it as a warning, for example `workspace confirmed 3h 5m ago · could not re-check now (…)`. A rejected key, an expired sign-in or a wrong account always fails, however recent the last success was.
- **Failures are never cached.** Entries older than 24 hours are dropped the next time the file is written.
- **Forcing a fresh check.** `keyfit status` always checks online and refreshes the cache. Deleting the file is always safe.

## What Keyfit never does

- **Print, log or store secret values.** Output names the variable or the Keychain item (`$LINEAR_ACME_API_KEY`, `Keychain item keyfit/linear-acme`), never the value. AWS account IDs are shown as their last four digits. `KEYFIT_DRY_RUN=1` lists the names of changed environment variables only. Keyfit keeps no log file.
- **Put a secret on a command line,** where every process could read it with `ps`. Keys are stored through the stdin of `security -i`, and values travel through pipes and environments. `vercel` gets only a folder (`--global-config`) and a team slug (`--scope`) as arguments; the CLI reads its token from that folder, and `vercel --token …` is refused in a project. MCP configuration refers to variables (`${LINEAR_API_KEY}`), never to keys.
- **Send a secret anywhere but its own service.** The Linear key goes only to `api.linear.app`, the GitHub token only to `api.github.com` and the Vercel token only to `api.vercel.com`. AWS and Claude credentials are never used by Keyfit; the `aws` and `claude` programs authenticate themselves.
- **Telemetry.** No analytics, no update checks, no crash reports. The [network calls](#network-calls) below are all of them.
- **Write into your repositories.** Keyfit reads a project's `.envrc` (on `keyfit init`), `.mcp.json` and git configuration. It never creates or changes files in a project folder, its worktrees or its `.git`. Per-session settings reach the tool through its environment or command line: Claude's `--settings`, and Codex's `-c` and `--no-daemon`.
- **Change other tools' logins or configuration.**
  - It reads gh's token with `gh auth token --user`; it never switches or adds gh logins.
  - It never runs `aws sso login`, `claude auth login`, `codex login` or `vercel login` by itself. It prints them as the fix.
  - It doesn't edit `~/.aws/config`, `~/.claude.json`, Claude's settings, `~/.codex/config.toml` or the Vercel CLI's `auth.json` and `config.json`. When the stored Vercel access token has expired, it lets the CLI renew it (`vercel whoami`), which any `vercel` command would do first.

  When you type a sign-in command yourself, Keyfit lets it through with the project's environment, so it signs in the right account. The only places Keyfit creates configuration for another tool are a new, separate Claude config folder (`keyfit add claude --new`) and an empty Vercel login folder (`keyfit add vercel --new`), and only when you ask for them.
- **Start a tool after a failed check.** A failed check, and also an error inside Keyfit (`keyfit: internal error …`), stops the tool. These are the documented exceptions:
  - `KEYFIT_DISABLE=1`
  - sign-in commands
  - folders that aren't registered
  - AWS profiles listed under `[settings] shared_aws_profiles`
  - a network failure within 24 hours of a success, shown as a warning

## Network calls

Keyfit makes no network calls in folders that aren't registered. In a project, these are the only ones:

| Call | Made by | What is sent | When |
|---|---|---|---|
| AWS STS `GetCallerIdentity` | the real `aws` CLI: `aws sts get-caller-identity --profile P` (connect timeout 5 s, read timeout 10 s) | whatever the AWS CLI sends for that profile. Keyfit reads only the account number from the answer | AWS check without a fresh cached success; `keyfit status`; `keyfit add aws` when the profile's config doesn't name the account |
| `POST https://api.linear.app/graphql` | Keyfit (Python's `urllib`, 15 s timeout) | the query `{ organization { urlKey name } }`, with the project's Linear key in the `Authorization` header | Linear check without a fresh cached success; `keyfit status`; `keyfit add linear`; `keyfit init` when it imports a key from `.envrc` |
| `GET https://api.github.com/user` | Keyfit (Python's `urllib`, 15 s timeout) | the account's token as a Bearer token, `User-Agent: keyfit` | GitHub check without a fresh cached success; `keyfit status`; `keyfit add github` |
| `GET https://api.vercel.com/v2/user` | Keyfit (Python's `urllib`, 15 s timeout) | the login's token from `auth.json` as a Bearer token, `User-Agent: keyfit` | Vercel check without a fresh cached success; `keyfit status`; `keyfit add vercel`; guided `keyfit init` |
| `GET https://api.vercel.com/v2/teams/TEAM` | Keyfit (Python's `urllib`, 15 s timeout) | the same token. When the API doesn't find a team by its slug, Keyfit looks it up in `GET /v2/teams?limit=100`, the login's teams | the same, for an account with a team |
| `vercel whoami` | the real Vercel CLI (30 s timeout) | whatever the CLI sends to renew its token; Keyfit sends nothing itself | only when the stored Vercel access token has expired |
| `claude auth status --json` | the real Claude Code CLI (30 s timeout) | whatever Claude Code sends to report its login; Keyfit sends nothing itself | Claude check without a fresh cached success; `keyfit status`; `keyfit add claude` |

Guided `keyfit init` makes the same calls as the `keyfit add …` commands it runs for you. For Vercel it makes them once before it asks, too, so that it can show whose login it offers. Both `keyfit init` and `keyfit add aws` end with `keyfit status`.

The AWS CLI may do more on its own:

- For an SSO profile it may refresh the SSO token.
- For a `credential_process` profile it runs that process. A process started with `--auto-login` (Granted, for example) may open a browser; Keyfit warns about it.

**No network:**

- Codex checks, which read `auth.json`
- `gh auth token` and `gh auth status`, which read gh's local store
- `keyfit status --offline`, `which`, `projects`, `doctor`, `setup`, `secret`, `add mcp` and `add vercel --new`

## Where secrets end up

At launch, the project's Linear key (as `LINEAR_API_KEY`) and GitHub token (as `GH_TOKEN`) are put into the environment of the tool Keyfit starts. The Vercel token is not: the CLI reads it from its own folder. `claude` and `codex` get all of the project's accounts; `aws`, `gh` and `vercel` get only their own. Everything the tool starts inherits that environment: its MCP servers, its hooks, and the commands an agent runs. That is how the agent's `gh` and Linear server work. It is also why Keyfit removes other projects' keys from that environment.

Keyfit only cleans up the accounts the project maps. A project without a Linear mapping, for example, keeps whatever Linear variables your shell exports.

## How to audit it

- `KEYFIT_DRY_RUN=1 claude` in a project runs the checks, then prints three things: the real binary, the arguments it would add, and the names of the environment variables that would change. The arguments include the `--settings` JSON that lists the hidden MCP servers. It starts nothing.
- `keyfit which -v` shows which project a folder belongs to, why, and its accounts. `keyfit status` shows every check and its result.
- `cat ~/.config/keyfit/registry.toml` shows plain TOML with nothing secret in it. The same goes for `cat ~/.config/keyfit/cache/checks.json`.
- `cat ~/.config/keyfit/bin/claude` shows the shim, which is two lines.
- `keyfit secret list` and Keychain Access (search for "keyfit") show the items Keyfit stored.
- The code is small and uses Python's standard library only. A launch goes through:
  - `keyfit/shim.py`: the launch itself
  - `keyfit/resolve.py`: which project
  - `keyfit/environment.py` and `keyfit/adapters/`: injecting and checking
  - `keyfit/mcp.py`: hiding MCP servers
  - `keyfit/secrets.py`: Keychain and `env:` references
  - `keyfit/cache.py`: the verification cache

## Threat model

**Keyfit protects against using the wrong account by accident:**

- an agent in one client's repository running with another client's AWS profile, Linear key, GitHub login, Vercel login or team, or Claude organization
- a `vercel link` or deploy landing in another team: through the login's selected team, `--scope`, `VERCEL_ORG_ID`/`VERCEL_TEAM_ID`, or a repository already linked to another team
- a credential inherited from your shell overriding the project's, such as `AWS_ACCESS_KEY_ID`, `GH_TOKEN`, `ANTHROPIC_API_KEY` or `CODEX_API_KEY`
- an expired or revoked sign-in that would otherwise fail halfway through a task
- an API key quietly switching Claude or Codex from your subscription to API billing
- MCP servers of other projects showing up in this one

**It does not protect against:**

- **Malicious code running as your user.** Such code can read the same files and environment variables, run the real binaries by their full path, set `KEYFIT_DISABLE=1`, edit the registry or remove the shims. Keyfit is not a sandbox and not a secrets vault.
- **What the tool does with the right account.** An agent with the project's AWS profile can do whatever that profile allows. Give each project's profile only the permissions it needs.
- **Anything that doesn't go through the shims:** other programs (including `vc`, the Vercel CLI's short name), tools called by their full path, apps that don't start from your shell, and runs with `KEYFIT_DISABLE=1`.
- **The MCP servers, hooks and plugins you configure.** Keyfit hides some MCP servers; it doesn't vet any of them. Anything the tool starts can read the environment it was given.
- **Accounts you didn't map.** These are neither checked nor cleaned up.
- **Changes inside the 30-minute window.** A key revoked after a successful check is noticed when the cached result expires, or at once with `keyfit status`.
- **Identities Keyfit can't confirm online.** Codex is checked from its local `auth.json` only. Claude is checked from what `claude auth status` reports.
- **A compromised `aws`, `claude`, `codex`, `gh`, `vercel` or `git`,** or a malicious folder placed on PATH. Keyfit runs the first real binary it finds on PATH after its own shims and Superset's wrappers.

If you find a way around a check that should hold, please [report it privately](https://github.com/FlamurMaliqi/keyfit/blob/main/SECURITY.md).
