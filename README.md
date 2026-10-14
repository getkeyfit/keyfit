# Keyfit

`claude`, `codex`, `opencode`, `aws`, `gh`, `vercel`, `supabase`, `wrangler` and `convex` use the accounts of the project you are in. Keyfit checks those accounts before the tool starts, and if a check fails, the tool does not start.

After a one-time `keyfit setup`, you keep typing `claude`, `codex`, `opencode`, `aws`, `gh`, `vercel`, `supabase`, `wrangler` and `convex` as usual. Keyfit works out which project the current folder belongs to. It then checks that project's accounts with the services themselves: the AWS account behind the profile, the Linear workspace behind the key, the GitHub login, the Vercel login and team, the Supabase organization, the Cloudflare account behind wrangler's sign-in, the Convex team, the Claude organization, the Codex login and OpenCode's sign-ins. If everything matches, the tool starts with only that project's credentials. If a check fails, the tool does not start and you see the command that fixes it. Folders you have not registered work exactly as before.

Most tools that switch credentials per folder assume the switch worked. Keyfit asks each service who you are signed in as, and stops the tool if the answer is wrong.

## Who it's for

You use Claude Code, Codex or OpenCode for several clients or companies, and each one has its own AWS account, Linear workspace, GitHub login, Vercel team, Supabase organization, Cloudflare account, Convex team, Claude organization or OpenCode sign-ins. You want an agent working in one client's repository never to act with another client's credentials, and you don't want to remember to switch anything.

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
keyfit setup --rc     # shims for claude, codex, opencode, aws, gh, vercel, supabase, wrangler and convex, plus one line in your shell startup file
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
- **OpenCode:** whether to use the sign-ins in OpenCode's own database (`~/.local/share/opencode/opencode.db`).
- **Vercel:** whether to use the login the `vercel` CLI has now, with the team selected in it.
- **Supabase:** whether to use the login the `supabase` CLI has now. Keyfit keeps a copy of its token in the macOS Keychain (macOS may ask once to allow that).
- **Cloudflare:** the wrangler sign-in (auth profile) bound to the repository's folder, and the Cloudflare account it deploys to. Keyfit keeps no Cloudflare token: wrangler keeps the sign-in, Keyfit checks it.
- **Convex:** whether to use the login the `convex` CLI has now (`npx convex login`). Keyfit records the team the project's deployment belongs to.
- **Linear:** a personal API key, typed at a hidden prompt. Keyfit checks it with Linear, then stores it in the macOS Keychain.

Skipped accounts are simply not checked, with one catch: inside the project, `aws`, `gh` and `vercel` start only once it has an AWS, GitHub or Vercel account. Skip GitHub, and `gh` waits in that repository until you run `keyfit add github --login <login>`. `keyfit status` lists what is still unmapped. `supabase`, `wrangler` and `convex` are the exceptions: many projects use them only locally (`supabase start`, `wrangler dev`, an anonymous local Convex deployment), so without a Supabase, Cloudflare or Convex account they run as before.

It ends with `keyfit status`:

```
acme-api
  ✔ aws       acme-dev → …4321   SSO token valid 6h 41m · refreshes automatically · account confirmed
  ✔ linear    acme   workspace confirmed
  ✔ github    octocat   login confirmed
  ✔ vercel    octocat → acme   login and team confirmed
  ✔ supabase  Acme   token, organization and linked project abcdefghijklmnopqrst confirmed
  ✔ cloudflare acme → Acme   bound at ~/code/acme-api · profile and account confirmed
  ✔ convex    Acme   login, team and deployment happy-animal-123 (acme-web) confirmed
  ✔ claude    Acme   Acme (team) confirmed
  ✔ codex     ChatGPT pro   subscription login, no API key
  ✔ opencode  default   anthropic, openai signed in · confirmed by OpenCode
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
2. **Verify.** Keyfit builds the project's environment and checks each account you mapped. Fast local checks run on every launch: does the profile exist, has the sign-in expired, is the key well-formed. Then come the online checks: who do AWS, Linear, GitHub, Vercel, Supabase, Cloudflare, Convex and Claude say you are, and which sign-ins does OpenCode see? A successful online check is reused for 30 minutes. If any check fails, the tool does not start.
3. **Inject.** The real tool starts with that environment: the project's credentials are set, and conflicting ones inherited from your shell are removed. MCP servers that belong to other projects are hidden for that session. Nothing is written into your repository or into the tools' config files.

Some details:

- **Which accounts each tool gets:** `claude`, `codex` and `opencode` get, and check, all of the project's accounts. `aws` gets only the AWS account, `gh` only the GitHub account, `vercel` only the Vercel login, `supabase` only the Supabase token, `wrangler` only the Cloudflare account and `convex` only the Convex login.
- **Expired sign-ins are one keypress.** When you start a tool in a terminal and the only problem is an expired or missing sign-in, Keyfit asks `Sign in now? [Y/n]`, runs the sign-in, checks again and starts the tool. Agents and scripts are never asked; they get the message and the fix.
- **Sign-in commands always run**, with the project's environment, so they fix the right account. These are `aws sso login`, `aws login`, `aws configure`, `claude auth …`, `codex login`, `opencode auth …` (with `--standalone`, so the sign-in lands in the project's OpenCode database), `gh auth …` and `vercel login`, `logout`, `switch`, `whoami` and `teams`. `supabase login` and `logout` run too, except in a project with a Supabase account: there they would change only the CLI's own login, which that project doesn't use, so Keyfit stops them and names `keyfit add supabase` instead. `wrangler auth create`, `activate`, `deactivate`, `list` and `whoami` run as well, unless they would move a registered folder to another project's profile; `wrangler login` and `logout` only change wrangler's default sign-in, so in a project with its own profile Keyfit stops them and names `wrangler auth create NAME`. `convex login` and `logout` run where the project's Convex account is the CLI's own login, and are stopped where Keyfit keeps the project's token.
- **Inside a Claude, Codex or OpenCode session,** the `aws`, `gh`, `vercel`, `supabase`, `wrangler` and `convex` commands it runs stay on that session's project, even after a `cd` elsewhere. The session also has the project's `SUPABASE_ACCESS_TOKEN`, `CLOUDFLARE_ACCOUNT_ID` and `CONVEX_OVERRIDE_ACCESS_TOKEN`, so `npx supabase`, `npx wrangler` and `npx convex` (and `npm run deploy`) use the project's accounts too.
- **Claude Code's status line** shows the project and its accounts, unless you configured a status line of your own.
- **Superset:** when Superset's agent wrapper (`~/.superset/bin`) is on PATH, it goes first and the shims right behind it, ahead of everything else. The chain is Superset wrapper → Keyfit → real tool, and Superset's hooks keep working (for OpenCode too: Keyfit leaves the `OPENCODE_CONFIG_DIR` the wrapper sets). Without Superset, the shims go first.

## Speed

Measured on a MacBook Pro with an Apple M4 (16 GB, macOS 26.6, Python 3.13.3), with other apps running, a shim adds:

- **About 0.1 s** in a folder you haven't registered, and for `aws`, `vercel`, `supabase`, `wrangler` and `convex` in a registered project when its checks are cached.
- **About 0.2 s** for `gh`, `claude`, `codex` and `opencode` in a project with a GitHub account. Half of that is `gh auth token`, which Keyfit runs to get the account's token. Inside a Claude, Codex or OpenCode session that Keyfit started, `gh` keeps the token its check already confirmed, and costs about 0.1 s.
- **0.5–2 s** when the checks have to go online, at most once every 30 minutes per project (and right after a new sign-in). GitHub, Linear, Vercel, Supabase, Cloudflare, Convex and Claude are checked in parallel. Cloudflare's check starts wrangler (`wrangler whoami`), which takes 1–2 s, and OpenCode's starts OpenCode (`opencode auth list`), about 2 s. AWS takes about 1 s with a console sign-in (`aws login`), because its check starts the AWS CLI; longer when your credential tool has to renew a login.

`--version` is checked like any other command, so `time gh --version` inside a project shows the full cost. To measure Keyfit alone on your Mac, with stub tools and no accounts or network: `python3 scripts/bench_shim.py`.

## What is checked

| Connection | Map it with | What the tool gets | Checked before it starts |
|---|---|---|---|
| AWS | `keyfit add aws --profile acme-dev` | `AWS_PROFILE` and `AWS_EXPECTED_ACCOUNT_ID` (and the region, if you set one). Inherited AWS keys, tokens, profile, config-file and region variables are removed | A named profile (never `default`) exists, and the account hints in its config match. The SSO or `aws login` session has not expired. Then `aws sts get-caller-identity` must return the expected 12-digit account |
| Linear | `keyfit add linear` | `LINEAR_API_KEY`, plus the project's own variable when the key is an `env:` reference. Every other Linear key variable is removed | The key is present, shaped like a Linear key and not pasted twice. Then Linear's API must report the expected workspace |
| GitHub | `keyfit add github --login octocat` | `GH_TOKEN`, read from `gh`'s stored login for that account. Other GitHub token variables, `GH_HOST` and `GH_CONFIG_DIR` are removed | `gh` has a token for the login, and `api.github.com/user` must return that login. A different git author email or SSH host alias in the repository shows as a warning |
| Vercel | `keyfit add vercel` | The login's folder as `--global-config` (nothing for the CLI's default folder) and the team as `--scope`. `VERCEL_ORG_ID`, `VERCEL_PROJECT_ID`, `VERCEL_TEAM_ID` (and their older `NOW_` names) and `VERCEL_TOKEN` are removed | The folder holds a login, and the repository isn't linked (`.vercel/project.json`, `.vercel/repo.json`) to another team. Then `api.vercel.com` must return the expected username and, with a team, let that login reach the team |
| Supabase | `keyfit add supabase` | `SUPABASE_ACCESS_TOKEN`, the token Keyfit keeps for the account, and `SUPABASE_PROFILE=supabase`, so the token only goes to `api.supabase.com` | The token is present and shaped like a Supabase access token. Then `api.supabase.com` must accept it and list the expected organization. When the folder is linked (`supabase/.temp/project-ref`, or `SUPABASE_PROJECT_ID`), the linked project must belong to that organization |
| Cloudflare | `keyfit add cloudflare` | `CLOUDFLARE_ACCOUNT_ID`, the project's account, so wrangler (also `npx wrangler`) works on it or fails. `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_API_KEY`, `CLOUDFLARE_EMAIL` and their older `CF_` names are removed: they beat every wrangler profile. wrangler's folder binding picks the sign-in | The wrangler profile exists, and the folder resolves to it: bound there or in a folder above, not shadowed by a subfolder bound to another profile. The `wrangler.json`, `wrangler.jsonc` or `wrangler.toml` wrangler would read names no other `account_id` (also under `[env.*]`), and the wrangler `npx` would run is 4.106 or newer. Then `wrangler whoami` in the folder must list the account |
| Convex | `keyfit add convex --cli` | `CONVEX_OVERRIDE_ACCESS_TOKEN`, the account's token (the convex CLI takes it before its own login and before deploy keys). `CONVEX_PROVISION_HOST` is removed, so the token only goes to `api.convex.dev`, and so are inherited `CONVEX_DEPLOYMENT`, `CONVEX_DEPLOY_KEY` and `CONVEX_DEPLOYMENT_TOKEN` that the folder's own `.env.local` or `.env` disagree with | A token is present and isn't a deploy key. Then `api.convex.dev` must accept it and list the expected team. The deployment the folder points at (`CONVEX_DEPLOYMENT` or a deploy key, from the environment, `.env.local` or `.env`) must belong to that team. Local and self-hosted deployments need no account |
| Claude Code | `keyfit add claude` | The account's config folder: `CLAUDE_CONFIG_DIR`, left unset for the default `~/.claude`. API keys, OAuth tokens, gateway URLs and cloud-provider switches are removed | `claude auth status` must report a claude.ai subscription login (not an API key), in the expected config folder and organization. With `--pin-email`, it must also be the exact login |
| Codex | `keyfit add codex` | `CODEX_HOME`. `CODEX_API_KEY` is removed, and interactive Codex runs without its shared daemon | `auth.json` must hold a ChatGPT login and no API key. With `--pin-account`, it must also be the exact ChatGPT account. This check is local; there is no online check |
| OpenCode | `keyfit add opencode` | `OPENCODE_DB`, the account's database of sign-ins. `opencode` starts without `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`, `GOOGLE_GENERATIVE_AI_API_KEY`, `GOOGLE_API_KEY`, `GITHUB_TOKEN` and `OPENCODE_API_KEY` (OpenCode takes them as sign-ins of their providers; Claude and Codex sessions keep them). Sessions run on a private server (`--standalone`) instead of OpenCode's shared background server | The database must hold a sign-in for each provider recorded with the account (read-only, never a credential). Then `opencode auth list` in that environment must list them as stored, and no variable from your shell next to them. No provider is asked |
| MCP ownership | `keyfit add mcp 'globex_cloud_*'` | Claude gets `--settings` with the servers to hide. Codex gets `-c mcp_servers.NAME.enabled=false`. OpenCode gets them switched off in `OPENCODE_CONFIG_CONTENT` | A Linear server (`mcp.linear.app`) in your user config stays visible only if it reads `LINEAR_API_KEY` or the project's own key variable, both of which hold the project's checked key; Linear servers in the folder's `.mcp.json` (or `opencode.json`) are hidden. Servers another project claimed are hidden unless this project claims them too. All other servers stay visible |

Only the accounts you map are touched. For example, a project without a Linear mapping leaves your Linear variables as they are and checks nothing for Linear.

Inside a registered project, `aws`, `gh` and `vercel` don't start until the project has an AWS, GitHub or Vercel account mapped. Also, `aws --profile <another profile>` is refused there, and so are `vercel --scope <another team>`, `vercel --global-config <another folder>` and `vercel --token …`. `supabase` refuses `--profile` (other than `supabase`), `--org-id` of another organization, and a project of another organization named with `--project-ref`, `gen types --project-id`, `link <ref>`, `projects delete <ref>` or the link in a `--workdir` folder. `wrangler` refuses `--profile` (other than the project's), and a `--cwd` or `--config` whose folder is bound to another profile or whose config names another `account_id`. `convex` refuses `--admin-key`, `--team` of another team, and another team's deployment named with `--deployment`, `--deployment-name` or an `--env-file`. The exception is AWS profiles of machine-wide tools that serve every project, such as a CI worker. List them in `~/.config/keyfit/registry.toml` and they run unchanged anywhere:

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

**Supabase** keeps one login per Mac, so Keyfit keeps a token per account instead. `keyfit add supabase` asks for a personal access token (create one at [supabase.com/dashboard/account/tokens](https://supabase.com/dashboard/account/tokens)), checks it with Supabase and stores it in the Keychain. `--cli` copies the token of the login the `supabase` CLI has now. The organization comes from `--org`, else the project the folder is linked to, else the token's only organization:

```bash
keyfit add supabase                        # paste a token (hidden); its organization is recorded
keyfit add supabase --cli                  # or copy the supabase CLI's current login
keyfit add supabase --name globex --org globex-org   # a token that sees several organizations
```

A Supabase MCP server that authenticates with `${SUPABASE_ACCESS_TOKEN}` works in every project, always with that project's token:

```bash
claude mcp add --scope user --transport http supabase https://mcp.supabase.com/mcp --header 'Authorization: Bearer ${SUPABASE_ACCESS_TOKEN}'
codex mcp add supabase --url https://mcp.supabase.com/mcp --bearer-token-env-var SUPABASE_ACCESS_TOKEN
```

One that signed in on its own (in the browser) belongs to one account: claim it for its project with `keyfit add mcp <its name>`.

**Cloudflare** sign-ins stay with wrangler. Since 4.106, wrangler keeps named sign-ins ("auth profiles") and binds them to folders, so Keyfit keeps no token: it records the profile and the account, checks that the folder resolves to that profile, and pins the account. One profile per client:

```bash
wrangler auth create acme                          # signs in (browser) as the client's account
wrangler auth activate acme ~/code/acme-api        # this folder and everything under it use that sign-in
keyfit add cloudflare                              # records the folder's profile and its account
```

`keyfit add cloudflare` offers the profiles wrangler has (and the folders each is bound to), then the accounts the chosen one reaches. The account comes from `--account`, else the `account_id` in the folder's wrangler config, else the profile's only account. If the folder isn't bound yet, it names the `wrangler auth activate` command (and offers to run it in a terminal). Worktrees outside the repository's folder need a binding of their own; with Superset, bind the folder that holds them once: `wrangler auth activate acme ~/.superset/worktrees/acme-api`.

**Convex** keeps one login per Mac (`~/.convex/config.json`) and has no account switch. Keyfit hands each session its project's token in `CONVEX_OVERRIDE_ACCESS_TOKEN`, which the convex CLI takes before its own login, so `npx convex` inside a Claude, Codex or OpenCode session acts as the project's account. An account is either the CLI's own login (`--cli`, read whenever a session starts) or a token Keyfit keeps in the Keychain (`--copy` copies the CLI's current login; without an option you paste one). The team comes from `--team`, else the team of the deployment in the folder's `.env.local`, else the login's only team:

```bash
npx convex login                          # sign the convex CLI in, if it isn't yet
keyfit add convex --cli                   # this project uses the CLI's login; its team is recorded
keyfit add convex --copy --name acme      # or keep a copy of that login, then sign the CLI in as your other account
keyfit add convex --cli --team globex     # a login that sees several teams
```

When the CLI's own login expires or is signed out, starting `claude`, `codex`, `opencode` or `convex` in a terminal offers `Sign in now?` and runs `npx convex login --force`. Convex's MCP server (`npx convex mcp start`) runs the same CLI, so inside a session it uses the project's account too.

**OpenCode** keeps every sign-in (`opencode auth login`, API keys too) in its database, `~/.local/share/opencode/opencode.db`, next to its sessions. `OPENCODE_DB` moves that database, so an account is a database: `keyfit add opencode` records OpenCode's own one and the providers signed in there. For a second set of sign-ins:

```bash
keyfit add opencode --new --name globex   # creates ~/.config/keyfit/opencode/globex for this project
opencode auth login                       # inside the project: Keyfit sends the sign-in to that database
keyfit add opencode --name globex --db ~/.config/keyfit/opencode/globex/opencode.db   # records the providers
```

OpenCode normally runs sessions in a background server that serves every folder and keeps the environment of whichever start launched it. Inside a project, Keyfit adds `--standalone` instead (a private server with the project's environment) to sessions (`opencode`, `run`, `mini`), sign-ins (`auth …`) and `models`, `stats` and `session …`, and refuses `--server`. `opencode DIR` with a folder of another project is refused too: start it there. Commands of the background server itself (`mcp`, `serve`, `service`, `api`, `plugin`, `debug`, `reload`, `pair`) and `upgrade` run unchanged, as outside a project. Inside a Claude, Codex or OpenCode session, though, the environment holds the project's accounts, and a background server started from there would keep them for every folder: there `api`, `reload` and an `opencode` in a folder outside every project get `--standalone`, and the other server commands (and `acp --login`) are refused; run them in a terminal of your own. Your configuration (`~/.config/opencode`, or the `OPENCODE_CONFIG_DIR` Superset's wrapper sets) stays shared; MCP servers other projects own are switched off for the session.

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
keyfit add opencode                # the providers signed in in OpenCode's database; another set: --new --name NAME
keyfit add vercel                  # the login the vercel CLI has now, and its team; another login: --new --name NAME
keyfit add supabase                # paste a Supabase token (hidden): checked, stored in the Keychain; or --cli
keyfit add cloudflare              # the wrangler profile bound to this folder and its Cloudflare account
keyfit add convex --cli            # the login the convex CLI has now, and the team of the folder's deployment
keyfit add mcp 'globex_cloud_*'    # these MCP servers belong to this project; every other project stops seeing them
keyfit run -- terraform plan       # run any other command with the project's accounts (checked first)
keyfit secret set NAME             # store a key from a hidden prompt; prints keychain:keyfit/NAME to use
keyfit secret list                 # names of the keys Keyfit stored (never the values)
```

`--project NAME` makes any `keyfit add …` change another project. Account names (`--name`) can be shared between projects. The registry lives in `~/.config/keyfit/registry.toml`. It stores where secrets live, never their values; see [security](https://github.com/FlamurMaliqi/keyfit/blob/main/docs/security.md).

## Limits

- **macOS only, for now.** Keys live in the macOS Keychain, and `keyfit setup` stops on other systems.
- **zsh and bash.** In another shell, put `~/.config/keyfit/bin` first on PATH yourself (after `~/.superset/bin` if you use Superset).
- **Nine tools.** Only `claude`, `codex`, `opencode`, `aws`, `gh`, `vercel`, `supabase`, `wrangler` and `convex` go through Keyfit, when they are found through PATH (typed in a terminal, or run by an agent Keyfit started). These don't go through Keyfit:
  - other commands (terraform, npm, git over SSH), including `vc`, the Vercel CLI's short name, `npx supabase`, `npx wrangler` and `npx convex` (inside a Claude, Codex or OpenCode session they get the project's token or account, but no checks of their own; outside one, `npx wrangler` follows only wrangler's own folder binding and `npx convex` uses the CLI's own login)
  - tools called by their full path
  - apps that don't start from your shell, such as IDE extensions and desktop apps

  To run any other command with the project's accounts: `keyfit run -- <command>`.
- **Online checks are cached for 30 minutes.** If a key is revoked within that window, Keyfit notices when the cached result expires, or at once with `keyfit status`. A new AWS SSO or console sign-in, a different Linear key, GitHub token, Vercel login or Supabase token or link, a new wrangler sign-in, a Convex login or deployment, a different Claude organization or a new OpenCode sign-in is checked again right away. When a service cannot be reached, a success from the last 24 hours is accepted with a warning; a wrong account never is.
- **One account of each kind per project:** one AWS profile, one Linear workspace, one GitHub login, one Vercel login and team, one Supabase token and organization, one wrangler profile and Cloudflare account, one Convex login and team, one Claude login, one Codex login, one OpenCode database.
- **Vercel is checked by login and team.** `keyfit add vercel` records the team selected in the login, if there is one. A project mapped without a team gets whichever team is selected at the time (`vercel switch` changes it), and a repository linked to another team isn't noticed.
- **Supabase is checked by organization.** A token belongs to a person, who may be in several organizations; Keyfit records the project's and checks the token can see it and the folder's linked project belongs to it. A folder linked to a preview branch shows a warning: Supabase's API doesn't say which organization a branch belongs to. `--db-url` and other direct database connections don't use the account, so Keyfit doesn't check them.
- **Cloudflare is checked by account.** wrangler 4.106 or newer is needed (older ones ignore profiles). A `CLOUDFLARE_API_TOKEN` in the folder's `.env` still beats the profile, as wrangler loads it itself; the account stays pinned, and the online check runs with what wrangler uses there. wrangler matches bound folders by their path as written, so a folder reached through a symlink or moved elsewhere needs its binding again.
- **Convex is checked by team.** A login belongs to a person, who may be in several teams; Keyfit records the project's team and checks that the login sees it and that the folder's deployment belongs to it. The Convex project inside that team is shown, not enforced. Where a project deploy key (`project:…`) is in the folder, the convex CLI accepts no other login, so Keyfit leaves that key in charge and doesn't check it (a warning says so); local and self-hosted deployments aren't checked either. `CONVEX_OVERRIDE_ACCESS_TOKEN` is the CLI's own variable but not documented by Convex; it was verified on convex 1.46.
- **GitHub means github.com.** GitHub Enterprise hosts are not supported.
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
- **Uninstall:** run `keyfit setup --remove` first, then `pipx uninstall keyfit` or `brew uninstall keyfit`. Done the other way round, the nine tools stop with a note that names the line to remove.

## More

- [Security](https://github.com/FlamurMaliqi/keyfit/blob/main/docs/security.md): what Keyfit reads, stores and sends, and what it does not protect against
- [Troubleshooting](https://github.com/FlamurMaliqi/keyfit/blob/main/docs/troubleshooting.md)
- [Contributing](https://github.com/FlamurMaliqi/keyfit/blob/main/CONTRIBUTING.md): running the tests and the rules every change follows
- [Reporting a vulnerability](https://github.com/FlamurMaliqi/keyfit/blob/main/SECURITY.md)
- [Changelog](https://github.com/FlamurMaliqi/keyfit/blob/main/CHANGELOG.md)

MIT licensed.
