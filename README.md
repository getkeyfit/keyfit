<div align="center">

<img src="https://keyfit.dev/logo/keyfit-icon-dark.svg" alt="Keyfit icon" width="96" height="96" />

### Keyfit

Your AI agents and cloud CLIs use the accounts of the project you are in, checked before they start.

[**Install**](#install) &nbsp;&bull;&nbsp; [Website](https://keyfit.dev) &nbsp;&bull;&nbsp; [Documentation](https://keyfit.dev/docs/quickstart/) &nbsp;&bull;&nbsp; [Security](https://github.com/FlamurMaliqi/keyfit/blob/main/docs/security.md) &nbsp;&bull;&nbsp; [Changelog](https://github.com/FlamurMaliqi/keyfit/blob/main/CHANGELOG.md)

<p align="center">
  <a href="https://keyfit.dev/#demo-title"><img width="800" alt="In harbor-api, claude starts with harbor's accounts and /mcp lists only harbor's Linear server. In lumen-web, the AWS sign-in has expired: Keyfit does not start Claude, offers the sign-in, then starts it, and /mcp lists only lumen's Linear server." src="https://keyfit.dev/demo/keyfit-demo.gif" /></a>
</p>

<sub>Real Keyfit and Claude Code with two made-up clients, whose AWS and Linear accounts are stand-ins (<a href="https://github.com/FlamurMaliqi/keyfit/tree/main/scripts/demo">scripts/demo</a> records it).</sub>

</div>

Keyfit is for multiple accounts on one Mac: one per client, or work and personal. Each project can have its own Claude Code and Codex login, AWS profile, GitHub account and so on. After a one-time `keyfit setup`, you keep typing `claude`, `codex`, `aws`, `gh` and the rest as usual. Keyfit works out which project the current folder belongs to and checks that project's accounts with the services themselves. If everything matches, the tool starts with only that project's credentials. If a check fails, the tool does not start, and you see the command that fixes it:

```
$ cd ~/code/acme-api && claude
keyfit: claude not started, acme-api needs attention
  ✘ aws        acme-dev → …4321   SSO session expired
    → aws sso login --sso-session acme
  (bypass once: KEYFIT_DISABLE=1 claude …)
```

Folders you have not registered work exactly as before.

Most tools that switch credentials per folder assume the switch worked. Keyfit asks each service who you are signed in as, and stops the tool if the answer is wrong.

## Who it's for

You use Claude Code, Codex, OpenCode, Copilot CLI or Gemini CLI for several clients or companies, and each one has its own AWS account, Linear workspace, GitHub login, Vercel team, Supabase organization, Cloudflare account, Convex team, Google account (work or personal), Google Cloud project and Firebase project, Claude organization or OpenCode sign-ins. You want an agent working in one client's repository never to act with another client's credentials, and you don't want to remember to switch anything.

If you work with a single set of accounts, you don't need Keyfit.

## What is checked

**Agents** get, and check, all of the project's accounts:

| Agent | Command | Keyfit confirms |
|---|---|---|
| <picture><source media="(prefers-color-scheme: dark)" srcset="https://keyfit.dev/logos/claude-white.svg" /><img height="16" align="top" alt="" src="https://keyfit.dev/logos/claude.svg" /></picture> &nbsp;[Claude Code][claude] | `claude` | the Claude organization |
| <picture><source media="(prefers-color-scheme: dark)" srcset="https://keyfit.dev/logos/openai-white.svg" /><img height="16" align="top" alt="" src="https://keyfit.dev/logos/openai.svg" /></picture> &nbsp;[Codex][codex] | `codex` | a ChatGPT login, without an API key |
| <picture><source media="(prefers-color-scheme: dark)" srcset="https://keyfit.dev/logos/opencode-white.svg" /><img height="16" align="top" alt="" src="https://keyfit.dev/logos/opencode.svg" /></picture> &nbsp;[OpenCode][opencode] | `opencode` | OpenCode's sign-ins |
| <picture><source media="(prefers-color-scheme: dark)" srcset="https://keyfit.dev/logos/copilot-white.svg" /><img height="16" align="top" alt="" src="https://keyfit.dev/logos/copilot.svg" /></picture> &nbsp;[Copilot CLI][copilot] | `copilot` | the GitHub account it runs as |
| <picture><source media="(prefers-color-scheme: dark)" srcset="https://keyfit.dev/logos/gemini-white.svg" /><img height="16" align="top" alt="" src="https://keyfit.dev/logos/gemini.svg" /></picture> &nbsp;[Gemini CLI][gemini] | `gemini` | the Google account it is signed in to |

Since 18 June 2026, Gemini CLI no longer serves Google AI Pro, Ultra or free personal accounts: Google moved them to Antigravity CLI, which Keyfit doesn't cover. Gemini CLI still works with a Gemini Code Assist licence or an API key.

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

## Install

You need macOS, Python 3.11 or newer, and zsh or bash. The `python3` that comes with macOS is 3.9, which is too old; `brew install pipx` installs pipx together with a current Python (then run `pipx ensurepath` and open a new terminal).

```bash
pipx install keyfit                       # coming with the first release
brew install flamurmaliqi/keyfit/keyfit   # coming with the first release
```

Nothing is published yet. Until then, install from source:

```bash
git clone https://github.com/FlamurMaliqi/keyfit.git
pipx install ./keyfit        # or run it without installing: ./keyfit/bin/keyfit … (uses the python3 on PATH, 3.11 or newer)
```

The shims run Keyfit with the Python that ran `keyfit setup`, and from a checkout they also run that checkout. If you move Keyfit to another Python, virtualenv or folder, run `keyfit setup` again.

## Quickstart (5 minutes)

**1. Install the shims.** This puts a shim in front of each of the thirteen tools and adds one line to your shell startup file:

```bash
keyfit setup --rc
```

**2. Register a project.** Open a new terminal, then go to a repository of one of your clients (a repository, not your home folder: everything under the folder you register belongs to that project):

```bash
cd ~/code/acme-api
keyfit init           # registers the repository, then offers the accounts it finds on this Mac
```

The project is named after the folder (`acme-api`). Folder names such as `api` or `web` repeat across clients, so when a project of that name exists already in another folder, `init` stops and asks for a name of its own: `keyfit init --name globex-api`. To add the folder to that project instead, with the same accounts, name it: `keyfit init --name api`.

`keyfit init` asks about one account at a time, and Enter skips any of them. Skipped accounts are simply not checked; `keyfit status` lists what is still unmapped. Already using direnv? `keyfit init` also imports `use aws PROFILE ACCOUNT_ID` and `export LINEAR_API_KEY=$VARIABLE` lines from the repository's `.envrc`. It reads the file as text and never runs it. Without a terminal, or with `--yes`, `init` asks nothing: it registers the repository and imports from `.envrc` only.

**3. Use your tools as usual.**

```bash
claude                # starts with this project's accounts, checked
```

`keyfit init` ends with `keyfit status`:

```
acme-api
  ✔ aws        acme-dev → …4321   SSO token valid 6h 41m · refreshes automatically · account confirmed
  ✔ linear     acme   workspace confirmed
  ✔ github     octocat   login confirmed
  ✔ vercel     octocat → acme   login and team confirmed
  ✔ supabase   Acme   token, organization and linked project abcdefghijklmnopqrst confirmed
  ✔ cloudflare acme → Acme   bound at ~/code/acme-api · profile and account confirmed
  ✔ convex     Acme   login, team and deployment happy-animal-123 (acme-web) confirmed
  ✔ google     me@acme.com
  ✔ gcloud     acme → acme-prod   me@acme.com · account and project confirmed
  ✔ firebase   acme-prod   me@acme.com, bound at ~/code/acme-api · account and project confirmed
  ✔ claude     Acme   Acme (team) confirmed
  ✔ codex      ChatGPT pro   subscription login, no API key
  ✔ opencode   default   anthropic, openai signed in · confirmed by OpenCode
  ✔ copilot    octocat   login confirmed
  ✔ gemini     me@acme.com   Google sign-in in ~/.config/keyfit/gemini/acme · confirmed by Google
  ✔ google-mcp gmail_acme, gcal_acme   me@acme.com · confirmed by Google
```

If something seems off, run `keyfit doctor`. It checks the installation and prints the fix under every ✘. See also the [troubleshooting guide](https://github.com/FlamurMaliqi/keyfit/blob/main/docs/troubleshooting.md).

### Setup options

- **Without `--rc`,** `keyfit setup` changes nothing outside `~/.config/keyfit` and prints the line for you to add yourself.
- **The shell** comes from `$SHELL`; choose it with `--shell zsh|bash`.
- **zsh:** the line goes into `~/.zshrc`, or `$ZDOTDIR/.zshrc` when `ZDOTDIR` is set.
- **bash:** the line goes into the login profile macOS terminals read. That is the first of `~/.bash_profile`, `~/.bash_login` and `~/.profile` that exists. It also goes into `~/.bashrc` when that profile sources it.
- **Another file:** `--rc PATH`.
- **Keep the line last** in the file, so nothing added to PATH later in that file comes before the shims.

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

## Everyday commands

```bash
# The project
keyfit init                        # register this repository; offers the accounts found on this Mac (--yes: ask nothing)
keyfit status                      # check every account of this project now: always online, refreshes the cache (--offline: local only)
keyfit doctor                      # check the installation: shims, PATH order, real tools, git, registry, Keychain
keyfit which -v                    # which project this folder belongs to, why, and its accounts
keyfit projects                    # all registered projects

# Agents
keyfit add claude                  # the Claude login in ~/.claude; for a second login: --new --name globex
keyfit add codex                   # the Codex login in ~/.codex; another one: --codex-home PATH
keyfit add opencode                # the providers signed in in OpenCode's database; another set: --new --name NAME
keyfit add copilot                 # Copilot CLI runs as the project's GitHub account; another one: --login LOGIN
keyfit add gemini                  # Gemini CLI's Google sign-in in ~/.gemini; another one: --new; a Gemini API key: --key

# CLIs
keyfit add aws --profile acme-dev  # add or change one account of this project
keyfit add github --login octocat  # one of the accounts `gh auth status` lists
keyfit add vercel                  # the login the vercel CLI has now, and its team; another login: --new --name NAME
keyfit add supabase                # paste a Supabase token (hidden): checked, stored in the Keychain; or --cli
keyfit add cloudflare              # the wrangler profile bound to this folder and its Cloudflare account
keyfit add convex --cli            # the login the convex CLI has now, and the team of the folder's deployment
keyfit add google --email me@acme.com   # the project's Google account (work or personal), for every Google tool
keyfit add gcloud                  # the gcloud configuration signed in as that account, and its project
keyfit add firebase                # the Firebase project the folder uses; the firebase CLI's sign-in there must be that account

# Linear and MCP servers
keyfit add linear                  # paste a key (hidden): checked with Linear, stored in the Keychain
keyfit add linear --key env:LINEAR_ACME_API_KEY   # or a key you keep elsewhere (env:VAR, keychain:service/account)
keyfit add google-mcp --server gmail_acme   # a Gmail, Drive or Calendar MCP server signed in as that account
keyfit add mcp 'globex_cloud_*'    # these MCP servers belong to this project; every other project stops seeing them

# Anything else
keyfit run -- terraform plan       # run any other command with the project's accounts (checked first)
keyfit secret set NAME             # store a key from a hidden prompt; prints keychain:keyfit/NAME to use
keyfit secret list                 # names of the keys Keyfit stored (never the values)
```

`--project NAME` makes any `keyfit add …` change another project. Account names (`--name`) can be shared between projects that use the same login; given another login, a shared name changes for all of them, and `keyfit add` names the projects it changed. Without `--name`, a second login never takes the first one's name: it gets the next free one, such as `pro-2`. The registry lives in `~/.config/keyfit/registry.toml`. It stores where secrets live, never their values; see [security](https://github.com/FlamurMaliqi/keyfit/blob/main/docs/security.md).

## Speed

Measured on a MacBook Pro with an Apple M4 (16 GB, macOS 26.6, Python 3.13.3), with other apps running, a shim adds:

| Added | When |
|---|---|
| **~0.1 s** | In a folder you haven't registered, and for `aws`, `vercel`, `supabase`, `wrangler`, `convex`, `gcloud` and `firebase` in a registered project when its checks are cached |
| **~0.2 s** | For `gh`, `claude`, `codex`, `opencode`, `copilot` and `gemini` in a project with a GitHub or Copilot account |
| **0.5–2 s** | When the checks have to go online: at most once every 30 minutes per project, and right after a new sign-in |

<details>
<summary>Where the time goes</summary>

- **The 0.2 s:** half of it is `gh auth token`, which Keyfit runs to get the account's token (once: a Copilot account that is the project's GitHub login reuses that token once its check confirmed it). Inside a session that Keyfit started, `gh` and `copilot` keep the token their check already confirmed, and cost about 0.1 s.
- **Online checks:** GitHub, Linear, Vercel, Supabase, Cloudflare, Convex and Claude are checked in parallel. A few start a CLI and take longer:
  - Cloudflare's starts wrangler (`wrangler whoami`), which takes 1–2 s.
  - OpenCode's starts OpenCode (`opencode auth list`), about 2 s.
  - Google Cloud's starts gcloud (`gcloud auth print-access-token`, 0.6 s before it renews the token) and then asks Google twice.
  - Gemini CLI's asks Google once, and only while the sign-in's token is still valid.
  - Firebase's asks Google twice; when the access token the firebase CLI keeps has expired (it lasts an hour), it first starts the CLI to renew it (`firebase projects:list`, about 2 s).
  - AWS takes about 1 s with a console sign-in (`aws login`), because its check starts the AWS CLI; longer when your credential tool has to renew a login.

</details>

`--version` is checked like any other command, so `time gh --version` inside a project shows the full cost. To measure Keyfit alone on your Mac, with stub tools and no accounts or network: `python3 scripts/bench_shim.py`.

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

## Private by design

- **No account, no server, no telemetry.** A small open-source command-line tool with no dependencies.
- **Keys stay secret.** Never printed or logged. Keys you add are stored in the macOS Keychain.
- **Each key goes only where it belongs.** The Linear key goes to Linear, the GitHub token to GitHub. Nothing else leaves your Mac.
- **Your repositories stay untouched.** Nothing is written into your code or into another tool's login.

[Everything Keyfit reads, stores and sends](https://github.com/FlamurMaliqi/keyfit/blob/main/docs/security.md), and what it does not protect against.

## FAQ

### Can I use Claude Code and Codex with multiple accounts, one per client?

Yes: one login per project, and several projects side by side in different terminals. `keyfit add claude --new --name globex` makes a Claude config folder of its own for a second login (you sign in there once), and `keyfit add codex --codex-home PATH` maps a Codex login kept in another folder. In that project, `claude` and `codex` start with its login and its other accounts. Claude is checked by organization (`--pin-email` for the exact login), Codex by being a ChatGPT login without an API key (`--pin-account` for the exact one). See [Claude Code][claude] and [Codex][codex].

### How do I use a different AWS profile or GitHub account per project?

In the repository, run `keyfit add aws --profile acme-dev` and `keyfit add github --login octocat`, or let `keyfit init` offer them. From then on, `aws` there runs with that profile and must reach the account recorded for it, and `gh` runs with that login's token while gh's active account stays as it is. The `aws` and `gh` commands an agent runs get the same, even after a `cd` elsewhere. This is the account your `aws` commands use; Keyfit doesn't set up Claude Code on Bedrock. See [AWS][aws] and [GitHub][github].

### Does Keyfit switch accounts?

No. The folder you are in picks the project, and Keyfit checks that project's accounts before the tool starts. The credentials go into that one tool's environment; gh's active account and the login in `~/.claude` stay as they are. If a check fails, the tool doesn't start.

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

## Learn more

- **[Accounts][accounts]**: every account, tool by tool: what it gets, what is checked, what is refused, and its limits
- **[Security](https://github.com/FlamurMaliqi/keyfit/blob/main/docs/security.md)**: what Keyfit reads, stores and sends
- **[Troubleshooting](https://github.com/FlamurMaliqi/keyfit/blob/main/docs/troubleshooting.md)**: when a tool doesn't go through Keyfit, or a check fails
- **[Changelog](https://github.com/FlamurMaliqi/keyfit/blob/main/CHANGELOG.md)**: what changed in each version

## Contributing

Bugs and ideas go in [issues](https://github.com/FlamurMaliqi/keyfit/issues). [CONTRIBUTING.md](https://github.com/FlamurMaliqi/keyfit/blob/main/CONTRIBUTING.md) covers running the tests and the rules every change follows. Please report vulnerabilities privately, as [SECURITY.md](https://github.com/FlamurMaliqi/keyfit/blob/main/SECURITY.md) describes.

![macOS](https://img.shields.io/badge/platform-macOS-lightgrey?style=flat) ![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue?style=flat) ![zsh | bash](https://img.shields.io/badge/shell-zsh%20%7C%20bash-green?style=flat) [![MIT](https://img.shields.io/badge/license-MIT-brightgreen?style=flat)](https://github.com/FlamurMaliqi/keyfit/blob/main/LICENSE)

## License

Keyfit is free and open source under the MIT license.

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
