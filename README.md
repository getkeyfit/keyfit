# Keyfit

`claude`, `codex`, `aws`, `gh` and `vercel` use the accounts of the project you are in. Keyfit checks those accounts before the tool starts, and if a check fails, the tool does not start.

After a one-time `keyfit setup`, you keep typing `claude`, `codex`, `aws`, `gh` and `vercel` as usual. Keyfit works out which project the current folder belongs to. It then checks that project's accounts with the services themselves: the AWS account behind the profile, the Linear workspace behind the key, the GitHub login, the Vercel login and team, the Claude organization and the Codex login. If everything matches, the tool starts with only that project's credentials. If a check fails, the tool does not start and you see the command that fixes it. Folders you have not registered work exactly as before.

Most tools that switch credentials per folder assume the switch worked. Keyfit asks each service who you are signed in as, and stops the tool if the answer is wrong.

## Who it's for

You use Claude Code or Codex for several clients or companies, and each one has its own AWS account, Linear workspace, GitHub login, Vercel team or Claude organization. You want an agent working in one client's repository never to act with another client's credentials, and you don't want to remember to switch anything.

If you work with a single set of accounts, you don't need Keyfit.

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

```bash
keyfit setup --rc     # shims for claude, codex, aws, gh and vercel, plus one line in your shell startup file
```

Open a new terminal, then go to a repository of one of your clients (a repository, not your home folder: everything under the folder you register belongs to that project):

```bash
cd ~/code/acme-api
keyfit init           # registers the repository, then offers the accounts it finds on this Mac
claude                # starts with this project's accounts, checked
```

`keyfit init` asks about one account at a time, and Enter skips any of them:

- **AWS:** a profile from `~/.aws/config`. Keyfit records the account that profile belongs to.
- **GitHub:** one of the accounts `gh` is signed in to.
- **Claude and Codex:** whether to use the login currently in `~/.claude` or `~/.codex`.
- **Vercel:** whether to use the login the `vercel` CLI has now, with the team selected in it.
- **Linear:** a personal API key, typed at a hidden prompt. Keyfit checks it with Linear, then stores it in the macOS Keychain.

Skipped accounts are simply not checked, with one catch: inside the project, `aws`, `gh` and `vercel` start only once it has an AWS, GitHub or Vercel account. Skip GitHub, and `gh` waits in that repository until you run `keyfit add github --login <login>`. `keyfit status` lists what is still unmapped.

It ends with `keyfit status`:

```
acme-api
  ✔ aws       acme-dev → …4321   SSO token valid 6h 41m · refreshes automatically · account confirmed
  ✔ linear    acme   workspace confirmed
  ✔ github    octocat   login confirmed
  ✔ vercel    octocat → acme   login and team confirmed
  ✔ claude    Acme   Acme (team) confirmed
  ✔ codex     ChatGPT pro   subscription login, no API key
```

When a check fails, the tool does not start:

```
$ claude
keyfit: claude not started, acme-api needs attention
  ✘ aws       acme-dev → …4321   SSO session expired
    → aws sso login --sso-session acme
  (bypass once: KEYFIT_DISABLE=1 claude …)
```

If something seems off, run `keyfit doctor`. It checks the installation and prints the fix under every ✘. See also the [troubleshooting guide](https://github.com/FlamurMaliqi/keyfit/blob/main/docs/troubleshooting.md).

More on setup:

- **Without `--rc`,** `keyfit setup` changes nothing outside `~/.config/keyfit` and prints the line for you to add yourself.
- **The shell** comes from `$SHELL`; choose it with `--shell zsh|bash`.
- **zsh:** the line goes into `~/.zshrc`, or `$ZDOTDIR/.zshrc` when `ZDOTDIR` is set.
- **bash:** the line goes into the login profile macOS terminals read. That is the first of `~/.bash_profile`, `~/.bash_login` and `~/.profile` that exists. It also goes into `~/.bashrc` when that profile sources it.
- **Another file:** `--rc PATH`.
- **Keep the line last** in the file, so nothing added to PATH later in that file comes before the shims.

Already using direnv? `keyfit init` also imports `use aws PROFILE ACCOUNT_ID` and `export LINEAR_API_KEY=$VARIABLE` lines from the repository's `.envrc`. It reads the file as text and never runs it. Without a terminal, or with `--yes`, `init` asks nothing: it registers the repository and imports from `.envrc` only.

## How it works

1. **Resolve.** The shim asks git for the folder's common git directory. That way the main checkout and every worktree of a registered repository resolve to the same project, wherever the worktrees live (Superset's included). Outside a registered repository, the nearest registered parent folder counts. If nothing matches, the real tool runs unchanged.
2. **Verify.** Keyfit builds the project's environment and checks each account you mapped. Fast local checks run on every launch: does the profile exist, has the sign-in expired, is the key well-formed. Then come the online checks: who do AWS, Linear, GitHub, Vercel and Claude say you are? A successful online check is reused for 30 minutes. If any check fails, the tool does not start.
3. **Inject.** The real tool starts with that environment: the project's credentials are set, and conflicting ones inherited from your shell are removed. MCP servers that belong to other projects are hidden for that session. Nothing is written into your repository or into the tools' config files.

Some details:

- **Which accounts each tool gets:** `claude` and `codex` get, and check, all of the project's accounts. `aws` gets only the AWS account, `gh` only the GitHub account and `vercel` only the Vercel login.
- **Sign-in commands always run**, with the project's environment, so they fix the right account. These are `aws sso login`, `aws login`, `aws configure`, `claude auth …`, `codex login`, `gh auth …` and `vercel login`, `logout`, `switch`, `whoami` and `teams`.
- **Inside a Claude or Codex session,** the `aws`, `gh` and `vercel` commands it runs stay on that session's project, even after a `cd` elsewhere.
- **Claude Code's status line** shows the project and its accounts, unless you configured a status line of your own.
- **Superset:** when Superset's agent wrapper (`~/.superset/bin`) is on PATH, it goes first and the shims right behind it, ahead of everything else. The chain is Superset wrapper → Keyfit → real tool, and Superset's hooks keep working. Without Superset, the shims go first.

## Speed

Measured on a MacBook Pro with an Apple M4 (16 GB, macOS 26.6, Python 3.13.3), with other apps running, a shim adds:

- **About 0.1 s** in a folder you haven't registered, and for `aws` and `vercel` in a registered project when its checks are cached.
- **About 0.2 s** for `gh`, `claude` and `codex` in a project with a GitHub account. Half of that is `gh auth token`, which Keyfit runs to get the account's token. Inside a Claude or Codex session that Keyfit started, `gh` keeps the token its check already confirmed, and costs about 0.1 s.
- **0.5–2 s** when the checks have to go online, at most once every 30 minutes per project (and right after a new sign-in). GitHub, Linear, Vercel and Claude are checked in parallel. AWS takes about 1 s with a console sign-in (`aws login`), because its check starts the AWS CLI; longer when your credential tool has to renew a login.

`--version` is checked like any other command, so `time gh --version` inside a project shows the full cost. To measure Keyfit alone on your Mac, with stub tools and no accounts or network: `python3 scripts/bench_shim.py`.

## What is checked

| Connection | Map it with | What the tool gets | Checked before it starts |
|---|---|---|---|
| AWS | `keyfit add aws --profile acme-dev` | `AWS_PROFILE` and `AWS_EXPECTED_ACCOUNT_ID` (and the region, if you set one). Inherited AWS keys, tokens, profile, config-file and region variables are removed | A named profile (never `default`) exists, and the account hints in its config match. The SSO or `aws login` session has not expired. Then `aws sts get-caller-identity` must return the expected 12-digit account |
| Linear | `keyfit add linear` | `LINEAR_API_KEY`, plus the project's own variable when the key is an `env:` reference. Every other Linear key variable is removed | The key is present, shaped like a Linear key and not pasted twice. Then Linear's API must report the expected workspace |
| GitHub | `keyfit add github --login octocat` | `GH_TOKEN`, read from `gh`'s stored login for that account. Other GitHub token variables, `GH_HOST` and `GH_CONFIG_DIR` are removed | `gh` has a token for the login, and `api.github.com/user` must return that login. A different git author email or SSH host alias in the repository shows as a warning |
| Vercel | `keyfit add vercel` | The login's folder as `--global-config` (nothing for the CLI's default folder) and the team as `--scope`. `VERCEL_ORG_ID`, `VERCEL_PROJECT_ID`, `VERCEL_TEAM_ID` (and their older `NOW_` names) and `VERCEL_TOKEN` are removed | The folder holds a login, and the repository isn't linked (`.vercel/project.json`, `.vercel/repo.json`) to another team. Then `api.vercel.com` must return the expected username and, with a team, let that login reach the team |
| Claude Code | `keyfit add claude` | The account's config folder: `CLAUDE_CONFIG_DIR`, left unset for the default `~/.claude`. API keys, OAuth tokens, gateway URLs and cloud-provider switches are removed | `claude auth status` must report a claude.ai subscription login (not an API key), in the expected config folder and organization. With `--pin-email`, it must also be the exact login |
| Codex | `keyfit add codex` | `CODEX_HOME`. `CODEX_API_KEY` is removed, and interactive Codex runs without its shared daemon | `auth.json` must hold a ChatGPT login and no API key. With `--pin-account`, it must also be the exact ChatGPT account. This check is local; there is no online check |
| MCP ownership | `keyfit add mcp 'globex_cloud_*'` | Claude gets `--settings` with the servers to hide. Codex gets `-c mcp_servers.NAME.enabled=false` | A Linear server (`mcp.linear.app`) in your user config stays visible only if it reads the project's own key variable; Linear servers in the folder's `.mcp.json` are hidden. Servers another project claimed are hidden unless this project claims them too. All other servers stay visible |

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

## Everyday commands

```bash
keyfit init                        # register this repository; offers the accounts found on this Mac (--yes: ask nothing)
keyfit status                      # check every account of this project now: always online, refreshes the cache (--offline: local only)
keyfit doctor                      # check the installation: shims, PATH order, real tools, registry, Keychain
keyfit which -v                    # which project this folder belongs to, why, and its accounts
keyfit projects                    # all registered projects
keyfit add aws --profile acme-dev  # add or change one account of this project
keyfit add linear                  # paste a key (hidden): checked with Linear, stored in the Keychain
keyfit add linear --key env:LINEAR_ACME_API_KEY   # or a key you keep elsewhere (env:VAR, keychain:service/account)
keyfit add github --login octocat  # one of the accounts `gh auth status` lists
keyfit add claude                  # the Claude login in ~/.claude; for a second login: --new --name globex
keyfit add codex                   # the Codex login in ~/.codex; another one: --codex-home PATH
keyfit add vercel                  # the login the vercel CLI has now, and its team; another login: --new --name NAME
keyfit add mcp 'globex_cloud_*'    # these MCP servers belong to this project; every other project stops seeing them
keyfit run -- terraform plan       # run any other command with the project's accounts (checked first)
keyfit secret set NAME             # store a key from a hidden prompt; prints keychain:keyfit/NAME to use
keyfit secret list                 # names of the keys Keyfit stored (never the values)
```

`--project NAME` makes any `keyfit add …` change another project. Account names (`--name`) can be shared between projects. The registry lives in `~/.config/keyfit/registry.toml`. It stores where secrets live, never their values; see [security](https://github.com/FlamurMaliqi/keyfit/blob/main/docs/security.md).

## Limits

- **macOS only, for now.** Keys live in the macOS Keychain, and `keyfit setup` stops on other systems.
- **zsh and bash.** In another shell, put `~/.config/keyfit/bin` first on PATH yourself (after `~/.superset/bin` if you use Superset).
- **Five tools.** Only `claude`, `codex`, `aws`, `gh` and `vercel` go through Keyfit, when they are found through PATH (typed in a terminal, or run by an agent Keyfit started). These don't go through Keyfit:
  - other commands (terraform, npm, git over SSH), including `vc`, the Vercel CLI's short name
  - tools called by their full path
  - apps that don't start from your shell, such as IDE extensions and desktop apps

  To run any other command with the project's accounts: `keyfit run -- <command>`.
- **Online checks are cached for 30 minutes.** If a key is revoked within that window, Keyfit notices when the cached result expires, or at once with `keyfit status`. A new AWS SSO or console sign-in, a different Linear key, GitHub token or Vercel login, or a different Claude organization is checked again right away. When a service cannot be reached, a success from the last 24 hours is accepted with a warning; a wrong account never is.
- **One account of each kind per project:** one AWS profile, one Linear workspace, one GitHub login, one Vercel login and team, one Claude login, one Codex login.
- **Vercel is checked by login and team.** `keyfit add vercel` records the team selected in the login, if there is one. A project mapped without a team gets whichever team is selected at the time (`vercel switch` changes it), and a repository linked to another team isn't noticed.
- **GitHub means github.com.** GitHub Enterprise hosts are not supported.
- **Claude is checked by organization, Codex by login type.** Codex only has to be a ChatGPT login without an API key. Neither is checked down to the person unless you pin it with `--pin-email` or `--pin-account`. That way, login pools that rotate between accounts keep working.
- **Linear MCP servers are matched by the key variable they read.** Keyfit puts each project's checked key into `LINEAR_API_KEY`, so one Linear server that reads `${LINEAR_API_KEY}` works in every project, always with that project's workspace. Servers that read another variable are hidden unless it is the project's own `env:` variable. To add such a server once:

  ```bash
  claude mcp add --scope user --transport http linear https://mcp.linear.app/mcp --header 'Authorization: Bearer ${LINEAR_API_KEY}'
  codex mcp add linear --url https://mcp.linear.app/mcp --bearer-token-env-var LINEAR_API_KEY
  ```

## Turning it off

- **Skip Keyfit once:** `KEYFIT_DISABLE=1 claude` runs that one command without checks, in your unchanged environment. Everything it starts inherits the variable, so an agent started this way runs its own `aws`, `gh` and `vercel` without Keyfit too.
- **See what would run:** `KEYFIT_DRY_RUN=1 claude` runs the checks in a registered project, then prints the command and the names of the environment variables that would change, and starts nothing. In a folder that isn't registered, and for sign-in commands, the tool runs as usual.
- **Turn it off:** `keyfit setup --remove` deletes the shims and PATH files and takes Keyfit's line out of your shell startup files. Open a new terminal afterwards. Your registry stays, so `keyfit setup --rc` turns everything back on.
- **Delete its data too:** `keyfit setup --remove --purge` also deletes `~/.config/keyfit`: the registry, the check cache, and the Claude and Vercel login folders Keyfit made (with the logins in them). It asks first (`--yes` skips the question). Keys Keyfit stored in the Keychain are kept; it lists their names so you can delete them in Keychain Access.
- **Uninstall:** run `keyfit setup --remove` first, then `pipx uninstall keyfit` or `brew uninstall keyfit`. Done the other way round, the five tools stop with a note that names the line to remove.

## More

- [Security](https://github.com/FlamurMaliqi/keyfit/blob/main/docs/security.md): what Keyfit reads, stores and sends, and what it does not protect against
- [Troubleshooting](https://github.com/FlamurMaliqi/keyfit/blob/main/docs/troubleshooting.md)
- [Contributing](https://github.com/FlamurMaliqi/keyfit/blob/main/CONTRIBUTING.md): running the tests and the rules every change follows
- [Reporting a vulnerability](https://github.com/FlamurMaliqi/keyfit/blob/main/SECURITY.md)
- [Changelog](https://github.com/FlamurMaliqi/keyfit/blob/main/CHANGELOG.md)

MIT licensed.
