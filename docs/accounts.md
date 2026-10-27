# Accounts

Everything Keyfit does for each kind of account: how to map it, what the tool gets, what is checked before it starts, what is refused inside the project, and its limits. To install Keyfit and register a first project, see the [README](../README.md).

- [How every account works](#how-every-account-works)
- **Agents:** [Claude Code](#claude-code) · [Codex](#codex) · [OpenCode](#opencode) · [Copilot CLI](#copilot-cli) · [Gemini CLI](#gemini-cli)
- **CLIs:** [AWS](#aws) · [GitHub](#github) · [Vercel](#vercel) · [Supabase](#supabase) · [Cloudflare](#cloudflare) · [Convex](#convex) · [Google Cloud](#google-cloud) · [Firebase](#firebase)
- **Shared by several tools:** [Google account](#google-account) · [Linear](#linear) · [Gmail, Drive and Calendar](#gmail-drive-and-calendar) · [MCP ownership](#mcp-ownership)

## How every account works

- **Only the accounts you map are touched.** For example, a project without a Linear mapping leaves your Linear variables as they are and checks nothing for Linear.
- **Which accounts each tool gets:** `claude`, `codex`, `opencode`, `copilot` and `gemini` get, and check, all of the project's accounts. `aws` gets only the AWS account, `gh` only the GitHub account, `vercel` only the Vercel login, `supabase` only the Supabase token, `wrangler` only the Cloudflare account, `convex` only the Convex login, `gcloud` only the Google Cloud account and `firebase` only the Firebase account.
- **Skipped accounts are simply not checked**, with one catch: inside the project, `aws`, `gh` and `vercel` start only once it has an AWS, GitHub or Vercel account. Skip GitHub, and `gh` waits in that repository until you run `keyfit add github --login <login>`. `keyfit status` lists what is still unmapped. `supabase`, `wrangler`, `convex`, `gcloud` and `firebase` are the exceptions: many projects use them only locally (`supabase start`, `wrangler dev`, an anonymous local Convex deployment, `gcloud components`, the Firebase emulators), so without a Supabase, Cloudflare, Convex, Google Cloud or Firebase account they run as before.
- **Other accounts are refused.** Inside a registered project, options that point a tool at another profile, team, organization, account or project are refused. Each section below lists them.
- **Expired sign-ins are one keypress.** When you start a tool in a terminal and the only problem is an expired or missing sign-in, Keyfit asks `Sign in now? [Y/n]`, runs the sign-in, checks again and starts the tool. A project folder that isn't bound to its wrangler profile or to the project's firebase sign-in yet works the same way, with `Bind this folder now? [Y/n]`. Agents, scripts and commands inside a session Keyfit started (`KEYFIT_PROJECT` set) are never asked; they get the message and the fix.
- **Sign-in commands always run**, with the project's environment, so they fix the right account. Each section below lists them, and the few Keyfit stops because they would change a login the project doesn't use.
- **An agent's own commands count only as its first word.** `claude mcp list`, `codex login` or `gemini extensions …` are read after the agent's own options (`claude --model opus mcp list`). A later word is part of the prompt (`gemini summarize the mcp setup`, `claude --add-dir ../lib agents`), and a command line Keyfit can't read for sure (an option it doesn't know before the word) starts as a session, with the checks and the MCP servers hidden.
- **Inside a Claude, Codex, OpenCode, Copilot CLI or Gemini CLI session,** the `aws`, `gh`, `vercel`, `supabase`, `wrangler`, `convex`, `gcloud` and `firebase` commands it runs stay on that session's project, even after a `cd` elsewhere. The session also has the project's `SUPABASE_ACCESS_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`, `CONVEX_OVERRIDE_ACCESS_TOKEN`, `COPILOT_GITHUB_TOKEN`, gcloud configuration (`CLOUDSDK_ACTIVE_CONFIG_NAME`, `CLOUDSDK_CORE_ACCOUNT`, `CLOUDSDK_CORE_PROJECT`) and Gemini CLI folder (`GEMINI_CLI_HOME`, and `GEMINI_API_KEY` for a key account), so `npx supabase`, `npx wrangler`, `npx convex`, `npx @github/copilot`, `npx @google/gemini-cli`, `bq` and `gsutil` (and `npm run deploy`) use the project's accounts too. `npx firebase-tools` uses the sign-in its folder is bound to, which the session's start checked; `FIREBASE_TOKEN` and `GOOGLE_APPLICATION_CREDENTIALS` are removed, so nothing else signs it in.
- **New sign-ins are checked again right away.** Online checks are cached for 30 minutes, but a new AWS SSO or console sign-in, a different Linear key, GitHub token, Vercel login or Supabase token or link, a new wrangler sign-in, a Convex login or deployment, a new gcloud sign-in or a changed gcloud configuration, a new firebase sign-in, a different Claude organization, a new OpenCode sign-in, a new token for the Copilot CLI account, a new Gemini CLI sign-in or key or a changed Google Workspace server login is checked again right away.
- **One account of each kind per project:** one AWS profile, one Linear workspace, one GitHub login, one Vercel login and team, one Supabase token and organization, one wrangler profile and Cloudflare account, one Convex login and team, one Google account, one gcloud configuration and Google Cloud project, one Firebase project, one Claude login, one Codex login, one OpenCode database, one GitHub login for Copilot CLI, one Gemini CLI folder (with its sign-in or key), and one Google account for all of its Gmail, Drive and Calendar servers.
- **`--project NAME`** makes any `keyfit add …` change another project. Account names (`--name`) can be shared between projects that use the same login; given another login, a shared name changes for all of them, and `keyfit add` names the projects it changed. Without `--name`, a second login never takes the first one's name: it gets the next free one, such as `pro-2`. A name is letters, digits, `.`, `_` and `-`.

## Agents

### Claude Code

- **Map it:** `keyfit add claude`, the Claude login in `~/.claude`. For a second login: `--new --name globex`. It makes a config folder of its own (`~/.config/keyfit/claude/globex`) and links your `settings.json`, `keybindings.json`, `CLAUDE.md`, `skills`, `commands`, `agents` and `plugins` from `~/.claude` into it. It is a separate login: sign in there once, as printed (Claude keeps one Keychain item per folder). It is a separate history too: sessions kept in `~/.claude` can't be resumed from it, nor its sessions from `~/.claude`.
- **`keyfit init` offers:** whether to use the login currently in `~/.claude`.
- **The tool gets:** the account's config folder: `CLAUDE_CONFIG_DIR`, left unset for the default `~/.claude`. API keys, OAuth tokens (also `CLAUDE_CODE_OAUTH_REFRESH_TOKEN`, which `claude auth login` would sign in with), gateway URLs and headers (`ANTHROPIC_BASE_URL`, `ANTHROPIC_CUSTOM_HEADERS`) and cloud-provider switches (`CLAUDE_CODE_USE_BEDROCK`, `…_VERTEX`, `…_FOUNDRY`, `…_ANTHROPIC_AWS`, `…_MANTLE`) are removed.
- **Checked before it starts:** `claude auth status` must report a claude.ai subscription login (not an API key), in the expected config folder and organization. Any login of the organization passes; for an account added as a personal one, any claude.ai login does.
- **Sign-in commands:** `claude auth …` always runs.
- **Your own `--settings`** (a file or JSON) is merged with Keyfit's into one `--settings`, since Claude keeps only the last one: the servers to hide are added to your `deniedMcpServers` and `disabledMcpjsonServers`, background sessions stay off where they are off (below), and Keyfit's status line goes in only when you set none. A `--settings` file that sets `env` is refused: merging would put its variables on Claude's command line, where every process can read them. Servers of `--mcp-config` files and JSON are hidden like those of `~/.claude.json`. A `--settings` or `--mcp-config` Keyfit can't read stops the start.
- **Background sessions:** only in a project whose Claude login has a config folder of its own. Claude runs them (`claude --bg`, agent view, `←` and `/background`) under one supervisor per config folder, which keeps the environment of the start that launched it and gives it to every background session, in any folder. A folder of its own is one that isn't `~/.claude` (or the `CLAUDE_CONFIG_DIR` your shell sets) and that no other project's Claude account uses: `keyfit add claude --new --name NAME`, then sign in there.
  - In such a project, from your terminal or its own sessions, `claude agents`, `--bg`, `attach` and `respawn` run, checked like a session start, and sessions start with agent view on. `claude agents` gets Keyfit's `--settings` (after the word), which agent view passes to the sessions it starts. First Keyfit makes sure a supervisor already running for that folder has this project's environment: it reads the pid and start time in the folder's `daemon.lock` and, while that process runs, its `KEYFIT_PROJECT` (with `ps -E`; nothing else of its environment is kept). While one with another project's environment runs there, or one Keyfit didn't start, those commands are refused and sessions start with background sessions off: stop it with `CLAUDE_CONFIG_DIR=… claude daemon stop --any`, as printed.
  - Everywhere else in a project (`~/.claude`, a folder another project shares), and inside another project's session, sessions start with `"disableAgentView": true` in `--settings` and `CLAUDE_CODE_DISABLE_AGENT_VIEW=1`, and `claude --bg`, `agents`, `attach` and `respawn` are refused. Inside a session, in a folder outside every project, they are refused too, unless the session's project has a folder of its own and the session uses it.
  - `claude daemon run` is refused in every project and inside a session: the supervisor starts when a checked command needs it. `claude daemon status`, `logs` and `stop` run as typed.
  - Elsewhere, start background sessions in a terminal outside every project, or bypass once with `KEYFIT_DISABLE=1 claude …`, which runs them with your own environment, without the project's accounts.

**Limits.** Claude is checked by organization, never down to the person, so account switchers such as claude-swap keep working. Agent view can start a session in another folder than the one you opened it in: a repository one level below it (`@repo` in the prompt), a worktree inside it, or a folder that already has a session in its list. That session runs with this project's accounts, without Keyfit's check of the folder, also when the folder belongs to another project. Claude's own Gmail, Google Drive and Google Calendar connectors are outside Keyfit's reach: see [Gmail, Drive and Calendar](#gmail-drive-and-calendar).

### Codex

- **Map it:** `keyfit add codex`, the Codex login in `~/.codex`. Another one: `--codex-home PATH`.
- **`keyfit init` offers:** whether to use the login currently in `~/.codex`.
- **The tool gets:** `CODEX_HOME`. `CODEX_API_KEY` is removed, and interactive Codex (`codex`, `codex resume`, `codex fork`) runs without its shared daemon (`--no-daemon`), which keeps the environment of the start that launched it, for every folder. `codex agents`, `queue`, `remote-control` and `app-server` work on that daemon, so they are refused in a project. Inside a session, in a folder outside every project, the same holds: Codex starts without the daemon and those commands are refused, since the session's environment holds its project's accounts.
- **Checked before it starts:** `auth.json` must hold a ChatGPT login and no API key. This check is local; there is no online check.
- **Refused in the project:** `codex -C DIR` (`--cd`) with a folder of another project, and a `-c` that would turn a hidden MCP server back on (`-c mcp_servers.NAME.enabled=true`, or a new table for `NAME` or `mcp_servers`).
- **Sign-in commands:** `codex login` always runs.

**Limits.** Codex is checked by login type: it only has to be a ChatGPT login without an API key, never down to the person, so account switchers such as codex-multi-auth keep working. When codex-multi-auth binds Codex to its local router (its first run does that if the Codex app is installed), Codex sends its requests there, and the router picks the login from its own pool; Keyfit checks the login in `auth.json`.

### OpenCode

- **Map it:** `keyfit add opencode`, the providers signed in in OpenCode's database. Another set: `--new --name NAME`.
- **`keyfit init` offers:** whether to use the sign-ins in OpenCode's own database (`~/.local/share/opencode/opencode.db`).
- **The tool gets:** `OPENCODE_DB`, the account's database of sign-ins.
  - `opencode` starts without `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`, `GOOGLE_GENERATIVE_AI_API_KEY`, `GOOGLE_API_KEY`, `GITHUB_TOKEN` and `OPENCODE_API_KEY`, and without every other key or token from your shell that OpenCode's own report names as a sign-in (`OPENROUTER_API_KEY`, `GROQ_API_KEY`, `AWS_BEARER_TOKEN_BEDROCK`, `CLOUDFLARE_API_TOKEN`, `GITLAB_TOKEN`, …; the names come from the last check, cached with it).
  - Claude, Codex and Copilot CLI sessions keep them all; Gemini CLI sessions keep them too, apart from the Google keys its own account removes (`GEMINI_API_KEY`, `GOOGLE_API_KEY`). In a project with an AWS account, no session keeps `AWS_BEARER_TOKEN_BEDROCK`: the AWS account removes it.
  - Sessions run on a private server (`--standalone`) instead of OpenCode's shared background server.
- **Checked before it starts:**
  - The database must hold a sign-in for each provider recorded with the account (read-only, never a credential).
  - Then `opencode auth list` in that environment must list them as stored.
  - Each key or token it lists from your shell (a name containing `KEY` or `TOKEN`) gets a warning, "from your shell, not the project's account", and is left out of the session; a setting such as the `CLOUDFLARE_ACCOUNT_ID` Keyfit sets is not a key and stays.
  - No provider is asked.
- **Sign-in commands:** `opencode auth …` always runs, with `--standalone`, so the sign-in lands in the project's OpenCode database.

OpenCode keeps every sign-in (`opencode auth login`, API keys too) in its database, `~/.local/share/opencode/opencode.db`, next to its sessions. `OPENCODE_DB` moves that database, so an account is a database: `keyfit add opencode` records OpenCode's own one and the providers signed in there. For a second set of sign-ins:

```bash
keyfit add opencode --new --name globex   # creates ~/.config/keyfit/opencode/globex for this project
opencode auth login                       # inside the project: Keyfit sends the sign-in to that database
keyfit add opencode --name globex --db ~/.config/keyfit/opencode/globex/opencode.db   # records the providers
```

**Background server.** OpenCode normally runs sessions in a background server that serves every folder and keeps the environment of whichever start launched it. Inside a project, Keyfit adds `--standalone` instead (a private server with the project's environment) to sessions (`opencode`, `run`, `mini`), sign-ins (`auth …`) and `models`, `stats` and `session …`, and refuses `--server`. `opencode DIR` with a folder of another project is refused too, also after options (`opencode -m MODEL DIR`), and so are `opencode run --dir DIR` and `opencode acp --cwd DIR`: start it there. A folder after an option Keyfit doesn't know is refused as well, since it may be that option's value: name the folder first. Commands of the background server itself (`mcp`, `serve`, `service`, `api`, `plugin`, `debug`, `reload`, `pair`) and `upgrade` run unchanged, as outside a project.

Inside a Claude, Codex, OpenCode, Copilot CLI or Gemini CLI session, though, the environment holds the project's accounts, and a background server started from there would keep them for every folder: there `api`, `reload` and an `opencode` in a folder outside every project get `--standalone`, and the other server commands (and `acp --login`) are refused; run them in a terminal of your own. Your configuration (`~/.config/opencode`, or the `OPENCODE_CONFIG_DIR` Superset's wrapper sets) stays shared; MCP servers other projects own are switched off for the session.

**Limits.** OpenCode is checked by provider. OpenCode labels a sign-in with the provider's name, not the person, so Keyfit checks that the project's database holds a sign-in for each provider it recorded and that OpenCode itself sees them there, not whose they are. No provider is asked: OpenCode has no cheap call that confirms a sign-in without using it. A key in your shell for any provider (`OPENROUTER_API_KEY`, say) is left out of OpenCode sessions, with a warning, as soon as OpenCode's own report (`opencode auth list`) names it; a new one is checked at the next start. That report lists the keys and tokens OpenCode takes from the environment, not every setting it uses: Amazon Bedrock also starts from `AWS_PROFILE` or `AWS_ACCESS_KEY_ID`, which the report does not show. In a project with an AWS account those are the project's; in one without, they stay as in your shell. Verified with OpenCode 2.0.25.

### Copilot CLI

- **Map it:** `keyfit add copilot`: Copilot CLI runs as the project's GitHub account. Another one: `--login LOGIN`.
- **`keyfit init` offers:** whether Copilot CLI runs as the project's GitHub account (asked when `copilot` is installed and you picked a GitHub account).
- **The tool gets:** `COPILOT_GITHUB_TOKEN`, read from `gh`'s stored login for the account (the project's GitHub account unless you name another `--login`); Copilot CLI takes it before `GH_TOKEN`, `GITHUB_TOKEN`, its own login and gh's. Inherited Copilot tokens, `COPILOT_GH_HOST`, `GH_HOST` and the `COPILOT_PROVIDER_…` variables (a model provider of your own instead of the GitHub account) are removed. Copilot CLI also takes model providers from `COPILOT_PROVIDERS_CONFIG`, else from `providers.json` in `~/.copilot`, before those variables: `COPILOT_PROVIDERS_CONFIG` is set to a file that never exists (`/var/empty/providers.json`), so sessions in the project use no `providers.json` either.
- **Checked before it starts:** `gh` has a token for the login and it isn't a classic personal access token (`ghp_…`, which Copilot CLI refuses). Then `api.github.com/user` must return that login. Whether the account has a Copilot plan, Copilot CLI itself says when it starts.
- **Refused in the project:** `--enable-mcp-server` with a server another project owns, and `copilot -C DIR` with a folder of another project.
- **Sign-in commands:** `copilot login` always runs, except in a project with a Copilot account: there it would change only Copilot's own login, which the project doesn't use, so Keyfit stops it and names the `gh` sign-in instead.

Copilot CLI signs in with GitHub, and Keyfit already knows the project's GitHub account, so that is its account: `keyfit add copilot` records the project's GitHub login (`--login` for another one gh is signed in to), and each session gets gh's token for it in `COPILOT_GITHUB_TOKEN`, which Copilot takes before its own login (`copilot login`) and gh's. Copilot's own folder (`~/.copilot`, or `COPILOT_HOME`: settings, sessions, `mcp-config.json`) stays shared.

Sessions (`copilot`, `copilot -p …`, `init`, `workflow`) start with `--disable-mcp-server=NAME` for each server another project owns, from `mcp-config.json`, `.mcp.json` and `.github/mcp.json` up to the repository's top and `--additional-mcp-config`. Copilot's own commands (`mcp`, `config`, `plugin`, `update`, …) run as typed. The fix for a missing or wrong login is gh's sign-in, which Keyfit prints and never runs (it switches gh's active account):

```bash
gh auth login --hostname github.com --web   # sign in as the project's account, then switch back:
gh auth switch --user <your usual account>
keyfit add copilot                          # inside the project: Copilot CLI runs as its GitHub account
```

**Limits.** Copilot CLI is checked by GitHub login. The check confirms the login behind gh's token, not the Copilot plan: GitHub has no public endpoint for that, and Copilot CLI says so itself when it starts. MCP servers of installed Copilot plugins aren't seen by Keyfit, so they aren't hidden. GitHub Enterprise hosts are not supported. Verified with Copilot CLI 1.0.97.

### Gemini CLI

- **Map it:** `keyfit add gemini`, Gemini CLI's Google sign-in in `~/.gemini`. Another one: `--new`. A Gemini API key: `--key`.
- **`keyfit init` offers:** whether to use the Google sign-in in `~/.gemini` (when it is the project's [Google account](#google-account)), or else a Gemini CLI folder of the project's own, where you sign in once (asked when `gemini` is installed and the project has a Google account).
- **The tool gets:** `GEMINI_CLI_HOME`, the account's Gemini CLI folder (left unset for your own `~/.gemini`), and for a key account `GEMINI_API_KEY`. `gemini` starts without `GEMINI_API_KEY` (unless it is the account's), `GOOGLE_API_KEY`, `GOOGLE_CLOUD_PROJECT` (the account's `--cloud-project`, if you gave one), `GOOGLE_APPLICATION_CREDENTIALS`, `GOOGLE_GENAI_USE_VERTEXAI`, `CODE_ASSIST_ENDPOINT` (where the sign-in's token goes) and the other variables that would pick another sign-in, endpoint or credential store; Claude and Codex sessions keep them.
- **Checked before it starts:**
  - **`.env` files:** Gemini CLI loads one `.env` file for every variable that isn't set: the nearest `.gemini/.env` or `.env` from the folder up, else `~/.gemini/.env` or `~/.env` (for a Gemini CLI folder of the project's own, the ones in that folder). When that file sets a variable Keyfit removed, the check fails. The one exception is the project's own `.env` (in one of its folders or worktrees) setting what its app uses: a key or credentials (`GEMINI_API_KEY`, `GOOGLE_API_KEY`, `GOOGLE_APPLICATION_CREDENTIALS`, …), the Google Cloud project or location, `GOOGLE_GENAI_USE_VERTEXAI` or `GOOGLE_GENAI_USE_GCA`. That gets a warning, as Gemini CLI uses it instead of the account. An endpoint (`CODE_ASSIST_ENDPOINT`, `GOOGLE_GEMINI_BASE_URL`, …), `GEMINI_CLI_HOME` or the credential store fails even there: a repository must never send the sign-in's token elsewhere or move the sign-in. Only the names are read. Names you list in Gemini CLI's `advanced.excludedEnvVars` setting don't count in a `.env` outside a `.gemini` folder, as Gemini CLI skips them there.
  - **Google sign-in:** the folder's sign-in method is Google's (or not chosen yet), it holds a Google sign-in (`oauth_creds.json`), and the email Google wrote into it is the project's Google account. Then, while the sign-in's token is still valid, Google's tokeninfo must name that account.
  - **API key:** the key is there and the folder uses the API-key method; then the Gemini API must accept the key.
- **Refused in the project:** an `--allowed-mcp-server-names` of your own that names another project's server.
- **Sign-in commands:** `gemini --prompt-interactive /quit` always runs. Gemini CLI has no sign-in command: this starts it in the project's Gemini CLI folder, where its own sign-in dialog opens, and quits once you are signed in.

**Which accounts Gemini CLI still serves.** Since 18 June 2026, Gemini CLI no longer serves Google AI Pro, Google AI Ultra or free personal Google accounts: Google moved them to Antigravity CLI ([google-gemini/gemini-cli#28017](https://github.com/google-gemini/gemini-cli/discussions/28017)), which Keyfit doesn't cover. A Google sign-in with a Gemini Code Assist licence and a Gemini API key still work. Keyfit's check confirms which Google account is signed in, not whether Gemini CLI still serves it.

Gemini CLI keeps everything in `~/.gemini`: settings, sessions, extensions and the Google sign-in. `GEMINI_CLI_HOME` moves that folder, so an account is a folder, and its sign-in is checked against the project's Google account, the work or the personal email. `keyfit add gemini` takes your own `~/.gemini` when it is signed in as that account; for the other one, the project gets a folder of its own:

```bash
keyfit add google --email me@acme.com      # the project's Google account
keyfit add gemini --new                    # creates ~/.config/keyfit/gemini/acme (your GEMINI.md, commands, skills and extensions linked in)
gemini                                     # inside the project: Sign in now? opens Gemini CLI's own sign-in there; sign in as me@acme.com
```

The sign-in Keyfit offers (`Sign in now?`) and prints is `gemini --prompt-interactive /quit`: Gemini CLI starts in the project's folder, shows its sign-in dialog with Sign in with Google selected, and quits once you are signed in (after Gemini CLI's own "press R to restart"); Keyfit checks the account and starts the session. Every other project with the same Google account shares that folder, so one sign-in serves them all. Signed in as the wrong account, the check names the file to remove (`oauth_creds.json` in Keyfit's folder; Keyfit never suggests removing your own `~/.gemini` sign-in, it suggests a folder of the project's own instead).

A Gemini API key works too: `keyfit add gemini --key` checks a pasted key with the Gemini API and keeps it in the Keychain (`--key env:VAR` for one you keep elsewhere), with a folder of its own that uses the API-key method. A Workspace account that needs a Google Cloud project for Gemini Code Assist gets one with `--cloud-project ID`.

**MCP servers.** Sessions (`gemini`, `gemini QUERY`, `gemini -p …`) start with `--allowed-mcp-server-names` naming every server they keep, when the project must not see one: servers of the folder's settings (yours in the Gemini CLI folder, the folder's `.gemini/settings.json`, the system's) and of your extensions. Your own `mcp.allowed` and `mcp.excluded` settings are carried over, as the option replaces them for the session. Gemini CLI's own commands (`mcp`, `extensions`, `skills`, `hooks`, `gemma`) run as typed when they are the first word; `gemini explain the mcp setup` is a session.

**Limits.** Gemini CLI is checked by Google account. The email in the sign-in is Google's own (the id_token Google issued with it). Google confirms the sign-in online only while its token is valid (an hour after Gemini CLI last renewed it): renewing it takes Gemini CLI's own OAuth client, so Keyfit leaves that to Gemini CLI, and until then the local check decides (`not confirmed online` in the details). A Gemini API key is checked by whether the Gemini API accepts it, not whose it is. A sign-in you change inside a running session (`/auth`) is checked at the next start. `GEMINI_FORCE_ENCRYPTED_FILE_STORAGE` is removed in a project with a Gemini CLI account: it would move the sign-in into one Keychain item that every folder shares. The folder's `.gemini/settings.json` counts only in the folder Gemini CLI starts in, as Gemini CLI reads it, and not for `advanced.excludedEnvVars` (an untrusted folder's settings are ignored). Whether you trust the folder in Gemini CLI, and `--ignore-env`, decide which `.env` it loads; the check doesn't know either, so a file any of them would load counts. A server with a comma in its name can't be named in the allowlist, so it stays hidden. Verified with Gemini CLI 0.64.0.

## CLIs

### AWS

- **Map it:** `keyfit add aws --profile acme-dev`.
- **`keyfit init` offers:** a profile from `~/.aws/config`. Keyfit records the account that profile belongs to. It also imports `use aws PROFILE ACCOUNT_ID` lines from the repository's `.envrc`.
- **The tool gets:** `AWS_PROFILE` and `AWS_EXPECTED_ACCOUNT_ID` (and the region, if you set one). Inherited AWS keys, tokens (also `AWS_BEARER_TOKEN_BEDROCK` and any other `AWS_BEARER_TOKEN_…`, which the CLI takes before the profile), profile, config-file and region variables are removed.
- **Checked before it starts:** a named profile (never `default`) exists, and the account hints in its config match. The SSO or `aws login` session has not expired. Then `aws sts get-caller-identity` must return the expected 12-digit account, asked of AWS's own STS: endpoint overrides (`AWS_ENDPOINT_URL`, `AWS_ENDPOINT_URL_STS`, a profile's `endpoint_url`) don't apply to this check. Commands you run keep them.
- **Refused in the project:** `aws --profile <another profile>`, also with the sign-in commands below: `aws configure export-credentials --profile other` would print that profile's keys.
- **Sign-in commands:** `aws sso login`, `aws login` and `aws configure` always run, for the project's profile.

AWS profiles of machine-wide tools that serve every project, such as a CI worker, are the exception. List them in `~/.config/keyfit/registry.toml` and they run unchanged anywhere:

```toml
[settings]
shared_aws_profiles = ["ci-worker"]
```

### GitHub

- **Map it:** `keyfit add github --login octocat`, one of the accounts `gh auth status` lists.
- **`keyfit init` offers:** one of the accounts `gh` is signed in to.
- **The tool gets:** `GH_TOKEN`, read from `gh`'s stored login for that account. Other GitHub token variables, `GH_HOST` and `GH_CONFIG_DIR` are removed.
- **Checked before it starts:** `gh` has a token for the login, and `api.github.com/user` must return that login. A different git author email or SSH host alias in the repository shows as a warning.
- **Refused in the project:** `--user <another account>` with `gh auth token` or `gh auth status`, `gh auth token --secure-storage` without `--user`, and `gh auth status --show-token` without `--active`. They would print another account's token.
- **Sign-in commands:** `gh auth login`, `logout`, `refresh`, `switch` and `setup-git` always run, without `GH_TOKEN`, so gh manages its own logins. `gh auth token`, `status` and `git-credential` are checked like any `gh` command and answer with the project's token. In a project without a GitHub account, `gh auth status` still runs unchecked, so you can pick the login to map.

**Limits.** GitHub means github.com. GitHub Enterprise hosts are not supported, for `gh` or for Copilot CLI.

### Vercel

- **Map it:** `keyfit add vercel`, the login the vercel CLI has now, and its team. Another login: `--new --name NAME`.
- **`keyfit init` offers:** whether to use the login the `vercel` CLI has now, with the team selected in it.
- **The tool gets:** the login's folder as `--global-config` (nothing for the CLI's default folder) and the team as `--scope`. `VERCEL_ORG_ID`, `VERCEL_PROJECT_ID`, `VERCEL_TEAM_ID` (and their older `NOW_` names) and `VERCEL_TOKEN` are removed.
- **Checked before it starts:** the folder holds a login, and the repository isn't linked (`.vercel/project.json`, or the entry for the folder in `.vercel/repo.json`) to another team. Then `api.vercel.com` must return the expected username and, with a team, let that login reach the team.
- **Refused in the project:** `vercel --scope <another team>`, `vercel --global-config <another folder>`, `vercel --token …`, and a folder linked to another team named with `--cwd`, `vercel deploy <path>`, `vercel <path>` or `vercel dev <dir>` (the CLI takes the team from that folder's link, whatever `--scope` says).
- **Sign-in commands:** `vercel login`, `logout`, `switch`, `whoami`, `teams switch` and `teams ls` always run. The other `teams` commands (`invite`, `members`, …) are checked and get the project's team, like any `vercel` command.

Vercel keeps one login per folder. `keyfit add vercel` records the login in the CLI's default folder (`~/Library/Application Support/com.vercel.cli`) with the team selected there (`--team SLUG` for another). For a second login:

```bash
keyfit add vercel --new --name globex     # creates ~/.config/keyfit/vercel/globex for this project
vercel login                              # inside the project: Keyfit signs in to that folder
keyfit add vercel --name globex --config-dir ~/.config/keyfit/vercel/globex   # records who it is and the team
```

Vercel's MCP server (`https://mcp.vercel.com`) signs in on its own, so Keyfit doesn't check it. If it is signed in to one project's account, claim it for that project with `keyfit add mcp <its name>`; every other project stops seeing it.

**Limits.** Vercel is checked by login and team. `keyfit add vercel` records the team selected in the login, if there is one. A project mapped without a team gets whichever team is selected at the time (`vercel switch` changes it), and a repository linked to another team isn't noticed.

### Supabase

- **Map it:** `keyfit add supabase`: paste a Supabase token (hidden), which is checked and stored in the Keychain. Or `--cli`.
- **`keyfit init` offers:** whether to use the login the `supabase` CLI has now. Keyfit keeps a copy of its token in the macOS Keychain (macOS may ask once to allow that).
- **The tool gets:** `SUPABASE_ACCESS_TOKEN`, the token Keyfit keeps for the account, and `SUPABASE_PROFILE=supabase`, so the token only goes to `api.supabase.com`.
- **Checked before it starts:** the token is present and shaped like a Supabase access token. Then `api.supabase.com` must accept it and list the expected organization. When the folder is linked (`supabase/.temp/project-ref` with the parent project in `.temp/linked-project.json`, the TypeScript CLI's `supabase/.link.json`, or `SUPABASE_PROJECT_ID`), every linked project must belong to that organization.
- **Refused in the project:** `--profile` (other than `supabase`), `--org-id` of another organization, and a project of another organization named with `--project-ref`, `gen types --project-id`, any word shaped like a project ref (`link <ref>`, `projects delete <ref>`) or the link in a `--workdir` folder.
- **Sign-in commands:** `supabase login` and `logout` run, except in a project with a Supabase account: there they would change only the CLI's own login, which that project doesn't use, so Keyfit stops them and names `keyfit add supabase` instead.

Supabase keeps one login per Mac, so Keyfit keeps a token per account instead. `keyfit add supabase` asks for a personal access token (create one at [supabase.com/dashboard/account/tokens](https://supabase.com/dashboard/account/tokens)), checks it with Supabase and stores it in the Keychain. `--cli` copies the token of the login the `supabase` CLI has now. The organization comes from `--org`, else the project the folder is linked to, else the token's only organization:

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

**Limits.** Supabase is checked by organization. A token belongs to a person, who may be in several organizations; Keyfit records the project's and checks the token can see it and the folder's linked project belongs to it. A folder linked to a preview branch shows a warning: Supabase's API doesn't say which organization a branch belongs to. `--db-url` and other direct database connections don't use the account, so Keyfit doesn't check them.

### Cloudflare

- **Map it:** `keyfit add cloudflare`, the wrangler profile bound to this folder and its Cloudflare account.
- **`keyfit init` offers:** the wrangler sign-in (auth profile) bound to the repository's folder, and the Cloudflare account it deploys to. Keyfit keeps no Cloudflare token: wrangler keeps the sign-in, Keyfit checks it.
- **The tool gets:** `CLOUDFLARE_ACCOUNT_ID`, the project's account, so wrangler (also `npx wrangler`) works on it or fails. wrangler's folder binding picks the sign-in.
  - `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_API_KEY`, `CLOUDFLARE_EMAIL` and their older `CF_` names are removed: they beat every wrangler profile.
  - `CLOUDFLARE_API_BASE_URL` is set to Cloudflare's own API, and inherited `WRANGLER_AUTH_DOMAIN`, `WRANGLER_AUTH_URL`, `WRANGLER_TOKEN_URL`, `WRANGLER_REVOKE_URL` and `WRANGLER_CLIENT_ID` are removed, so the profile's sign-in goes only to Cloudflare.
- **Checked before it starts:**
  - No `.env`, `.env.local` or `.env.<env>` in the folder sets one of those five, the API host or `XDG_CONFIG_HOME` (wrangler loads them into its environment; `XDG_CONFIG_HOME` would move its profiles).
  - The wrangler profile exists, and the folder resolves to it: bound there or in a folder above, not shadowed by a subfolder bound to another profile.
  - The `wrangler.json`, `wrangler.jsonc` or `wrangler.toml` wrangler would read names no other `account_id` (also under `[env.*]`), and neither does the config a build generated that `.wrangler/deploy/config.json` sends `wrangler deploy` to (the Vite plugin's `dist/<name>/wrangler.json`). A config Keyfit cannot read (TOML 1.1 syntax, say) fails the check instead of being skipped.
  - The wrangler `npx` would run and the one `wrangler` runs (on PATH) are 4.106 or newer. A version Keyfit cannot tell is a warning; the tool still starts.
  - Then `wrangler whoami` in the folder must list the account. It runs with your shell's environment and the project's Cloudflare account only, never the project's other tokens.
- **Refused in the project:** `--profile` (other than the project's), and a `--cwd` or `--config` whose folder is bound to another profile or whose config names another `account_id`, in every spelling wrangler's parser takes (`-c FILE`, `-c=FILE`, `-c/path/wrangler.toml`, `-ec FILE`, `--c FILE`, `--config -0x.toml`; each config of `wrangler dev`). The same for an entry file whose folder has such a config (`wrangler deploy ../other/src/index.ts`, also `versions upload`, `dev`, `preview` and `--script`): wrangler reads the config from the entry file's folder. `--cwd` is followed through symlinks, as wrangler does, and `--env NAME` loads `.env.NAME` wherever that path leads.
- **Sign-in commands:** `wrangler auth create`, `activate`, `deactivate`, `list` and `whoami` run, unless they would move a registered folder to another project's profile (or a `.env` there sets wrangler's API host or sign-in). That covers a folder that holds a project's folders too (`..`, `~/code`, `~/.superset/worktrees/acme-api`), and `auth activate`, `deactivate` and `delete` typed outside every project. `wrangler login` and `logout` only change wrangler's default sign-in, so in a project with its own profile Keyfit stops them and names `wrangler auth create NAME`.

Cloudflare sign-ins stay with wrangler. Since 4.106, wrangler keeps named sign-ins ("auth profiles") and binds them to folders, so Keyfit keeps no token: it records the profile and the account, checks that the folder resolves to that profile, and pins the account. One profile per client:

```bash
wrangler auth create acme                          # signs in (browser) as the client's account
wrangler auth activate acme ~/code/acme-api        # this folder and everything under it use that sign-in
keyfit add cloudflare                              # records the folder's profile and its account
```

`keyfit add cloudflare` offers the profiles wrangler has (and the folders each is bound to), then the accounts the chosen one reaches. The account comes from `--account`, else the `account_id` in the folder's wrangler config, else the profile's only account. If the folder isn't bound yet, it names the `wrangler auth activate` command (and offers to run it in a terminal) for the repository's folder, or for the project's own folder when the repository holds more than the project (`~/mono/apps/web` in `~/mono`). Worktrees outside the repository's folder need a binding of their own; with Superset, bind the folder that holds them once: `wrangler auth activate acme ~/.superset/worktrees/acme-api`.

**Binding new folders.** Starting a tool yourself in a terminal in a folder of the project that isn't bound yet (a new worktree, say) asks `Bind this folder now? [Y/n]`. Press Enter: Keyfit runs `wrangler auth activate` with the project's wrangler, checks again and starts the tool. In a Superset worktree it offers the folder that holds the repository's worktrees instead (`Bind ~/.superset/worktrees/acme-api now, for every Superset worktree of acme-api?`), so the next workspace needs nothing; when a worktree of another project is in that folder too, it offers only this worktree. Keyfit binds only the project's own profile, and only to a folder of that project: never another project's folder or one outside every project, never your home folder or a folder above it. Agents and scripts are never asked; they get the printed command.

**Limits.** Cloudflare is checked by account. wrangler 4.106 or newer is needed (older ones ignore profiles). Keyfit reads wrangler configs as TOML 1.0 and JSON with comments; a `wrangler.toml` in TOML 1.1 syntax (which wrangler reads) is refused until it is written in 1.0. A `CLOUDFLARE_API_TOKEN` in the folder's `.env` still beats the profile, as wrangler loads it itself; the account stays pinned, and the online check runs with what wrangler uses there. wrangler matches bound folders by their path as written, so a folder reached through a symlink or moved elsewhere needs its binding again. wrangler runs on Cloudflare's public API and sign-in only: a project that deliberately uses another host (Cloudflare's staging, a `compliance_region` such as FedRAMP, `CLOUDFLARE_API_BASE_URL`) isn't supported yet; `KEYFIT_DISABLE=1 wrangler …` runs such a command without Keyfit.

### Convex

- **Map it:** `keyfit add convex --cli`, the login the convex CLI has now, and the team of the folder's deployment.
- **`keyfit init` offers:** whether to use the login the `convex` CLI has now (`npx convex login`). Keyfit records the team the project's deployment belongs to.
- **The tool gets:** `CONVEX_OVERRIDE_ACCESS_TOKEN`, the account's token (the convex CLI takes it before its own login and before deploy keys). `CONVEX_PROVISION_HOST` is removed, so the token only goes to `api.convex.dev`, and so are inherited `CONVEX_DEPLOYMENT`, `CONVEX_DEPLOY_KEY`, `CONVEX_DEPLOYMENT_TOKEN`, `CONVEX_SELF_HOSTED_URL` and `CONVEX_SELF_HOSTED_ADMIN_KEY` that the folder's own `.env.local` or `.env` disagree with.
- **Checked before it starts:** a token is present and isn't a deploy key. Then `api.convex.dev` must accept it and list the expected team. The deployment the folder points at (`CONVEX_DEPLOYMENT` or a deploy key, from the environment, `.env.local` or `.env`) must belong to that team. Keyfit reads those files the way the convex CLI does (with dotenv's rules), and one it can't read fails the check, as does a `CONVEX_DEPLOYMENT` that holds a deploy key or no deployment name. Local and self-hosted deployments need no account.
- **Refused in the project:** `--admin-key`, `--team` of another team, and another team's deployment named with `--deployment`, `--deployment-name` or an `--env-file`. Also a `--deployment-name` that isn't a deployment name, a deploy key given to `--team`, `--deployment` or `--deployment-name`, and an `--env-file` Keyfit can't read.
- **Sign-in commands:** `convex login` and `logout` run where the project's Convex account is the CLI's own login, and are stopped where Keyfit keeps the project's token.

Convex keeps one login per Mac (`~/.convex/config.json`) and has no account switch. Keyfit hands each session its project's token in `CONVEX_OVERRIDE_ACCESS_TOKEN`, which the convex CLI takes before its own login, so `npx convex` inside a Claude, Codex, OpenCode, Copilot CLI or Gemini CLI session acts as the project's account. An account is either the CLI's own login (`--cli`, read whenever a session starts) or a token Keyfit keeps in the Keychain (`--copy` copies the CLI's current login; without an option you paste one). The team comes from `--team`, else the team of the deployment in the folder's `.env.local`, else the login's only team:

```bash
npx convex login                          # sign the convex CLI in, if it isn't yet
keyfit add convex --cli                   # this project uses the CLI's login; its team is recorded
keyfit add convex --copy --name acme      # or keep a copy of that login, then sign the CLI in as your other account
keyfit add convex --cli --team globex     # a login that sees several teams
```

When the CLI's own login expires or is signed out, starting `claude`, `codex`, `opencode`, `copilot` or `convex` in a terminal offers `Sign in now?` and runs `npx convex login --force`. Convex's MCP server (`npx convex mcp start`) runs the same CLI, so inside a session it uses the project's account too.

**Limits.** Convex is checked by team. A login belongs to a person, who may be in several teams; Keyfit records the project's team and checks that the login sees it and that the folder's deployment belongs to it. The Convex project inside that team is shown, not enforced. Where a project deploy key (`project:…`) is in the folder, the convex CLI accepts no other login, so Keyfit leaves that key in charge and doesn't check it (a warning says so); local and self-hosted deployments aren't checked either. `CONVEX_OVERRIDE_ACCESS_TOKEN` is the CLI's own variable but not documented by Convex; it was verified on convex 1.46.

### Google Cloud

- **Map it:** `keyfit add gcloud`, the gcloud configuration signed in as the project's [Google account](#google-account), and its project.
- **`keyfit init` offers:** a gcloud configuration signed in as that account, and the Google Cloud project it works on. Keyfit keeps no Google token: gcloud keeps the sign-in, Keyfit checks it.
- **The tool gets:** `CLOUDSDK_ACTIVE_CONFIG_NAME` (the project's gcloud configuration, for this process only; the one active everywhere else is untouched), `CLOUDSDK_CORE_ACCOUNT` (the project's Google account) and `CLOUDSDK_CORE_PROJECT`. Inherited `CLOUDSDK_…` variables that pick another configuration, account, project, credentials or endpoint are removed (those for gcloud's Python, a proxy or prompts stay), and so is `GOOGLE_APPLICATION_CREDENTIALS`.
- **Checked before it starts:** the configuration exists in gcloud's folder, its account is the project's Google account and its project the expected one, it takes no credentials from a file (`auth/access_token_file`), it impersonates no service account (`auth/impersonate_service_account`), it sends nothing elsewhere than to Google (no `auth/token_host`, `auth/disable_ssl_validation` or `api_endpoint_overrides/…`), and gcloud holds a sign-in for the account. Then the token `gcloud auth print-access-token` prints for it must belong to that account (Google's tokeninfo) and see the project (Cloud Resource Manager).
- **Refused in the project:** `--configuration`, `--account` and `--project` with other values than the project's, `--access-token-file` (a token instead of the account), `--impersonate-service-account` (another identity) and `--flags-file` (Keyfit can't see what it holds).
- **Sign-in commands:** `gcloud auth login`, `auth list`, `config set`, `config unset` and `config configurations create`, `list` and `describe` run in the project's configuration, so a sign-in or a setting lands there. `gcloud auth login` as anyone but the project's Google account, or without naming it, is stopped (so is `gcloud auth activate-service-account`), and so is a `config set` that would move a project's configuration to another account or project, or set an `auth/…` or `api_endpoint_overrides/…` setting in it. Release tracks (`gcloud beta …`) and arguments after `--` count the same.

Google Cloud sign-ins stay with gcloud, which keeps named configurations: each has an account and a project, and `CLOUDSDK_ACTIVE_CONFIG_NAME` picks one for a single process. A project first says which Google account it uses, its work or its personal email, once; Keyfit then checks gcloud's configuration against it; the firebase CLI and Gemini CLI are checked against the same email. One configuration per client:

```bash
gcloud config configurations create acme --no-activate   # leaves the configuration you use elsewhere active
keyfit add google --email me@acme.com                    # the project's Google account
gcloud auth login me@acme.com                            # inside the project: the sign-in lands in configuration acme
gcloud config set project acme-prod --configuration acme
keyfit add gcloud --configuration acme                   # records the configuration and its project, checks both
```

`keyfit add gcloud` without `--configuration` takes the configuration signed in as the project's Google account (it asks when there are several), and its project (`--project-id ID` for another). It never takes another account's configuration, such as the default one you use elsewhere: without one of the project's account, it names `gcloud config configurations create … --no-activate`. When the project has no Google account yet, it takes the only configuration (else it asks), and the configuration's account becomes the project's. Inside the project, `gcloud` runs as the project's account on its project whichever configuration is active; `gcloud config configurations activate` still switches it for the rest of your Mac.

**Limits.** Google Cloud is checked by account and project. The project's Google account must be the configuration's account and own the token gcloud prints for it, and the account must see the project (any role on it will do: Viewer includes `resourcemanager.projects.get`). Other Google Cloud projects are refused with `--project`, even ones the account may use: map the project the folder works on. A configuration that impersonates a service account (`auth/impersonate_service_account`) fails its check: inside a project, gcloud works as the project's account only. Application default credentials (`gcloud auth application-default login`, what the client libraries and Terraform use) are one login per Mac, not per configuration: Keyfit removes an inherited `GOOGLE_APPLICATION_CREDENTIALS` in a project with a Google Cloud account but doesn't check that login. Verified with gcloud 580.0.0.

### Firebase

- **Map it:** `keyfit add firebase`, the Firebase project the folder uses. The firebase CLI's sign-in there must be the project's [Google account](#google-account).
- **`keyfit init` offers:** the Firebase project the repository's `.firebaserc` names. The firebase CLI keeps the sign-in and binds the folder to it (`firebase login:use`); Keyfit checks both.
- **The tool gets:** nothing is set: the folder's binding (`firebase login:use`) picks the firebase CLI's sign-in, for `npx firebase-tools` too. `FIREBASE_TOKEN` and `GOOGLE_APPLICATION_CREDENTIALS` are removed: they would act instead of the sign-in. So are `FIREBASE_…_URL` (`FIREBASE_TOKEN_URL` and `FIREBASE_GOOGLE_URL` are where the CLI sends the refresh token) and `FIREBASE_CLIENT_ID` and `FIREBASE_CLIENT_SECRET`. Outside every Firebase folder (no `firebase.json` there or above), `firebase` gets `--account` with the project's Google account when the CLI's default login is another one.
- **Checked before it starts:** the firebase CLI is signed in as the project's Google account. In the CLI's project folder (the nearest one with a `firebase.json`) that is the account it uses (bound there with `firebase login:use`, or its default login), and the Firebase project it picks (`firebase use`, else the deprecated `"firebase"` key in `firebase.json`, else `.firebaserc`) is the expected one. Outside every Firebase folder (a monorepo's top), the project a `.firebaserc` there picks must be the expected one too: commands such as `firestore:delete` need no `firebase.json`. Then Google's tokeninfo must name the account for the access token the CLI keeps for it (renewed by the CLI first when it has expired), and the Firebase Management API must show the project to it.
- **Refused in the project:** `--account` and `--project` with other values than the project's (an alias counts as the project `.firebaserc` names; `-P other`, `-Pother` and `-jP other` too), `--token`, and a `--config` whose folder is bound to another account or works on another project.
- **Sign-in commands:** `firebase login`, `login:add`, `login:use`, `login:list`, `logout` and `use` run, with three exceptions Keyfit stops (also when the argument comes after `--`, as in `firebase use -- other`):
  - a `login:use` that would bind a project's folder to another account (outside every Firebase folder, where it would change the CLI's default login, it is stopped too);
  - a `use` of another Firebase project;
  - a `login --reauth` that would replace the CLI's default login with the project's account (Keyfit names `firebase login:add` instead).

Firebase sign-ins stay with the firebase CLI, which keeps several (`firebase login:add`) and binds one to each project folder, the folder with the `firebase.json` (`firebase login:use`). Keyfit checks that binding against the project's Google account, and the project the CLI picks there against the project's Firebase project:

```bash
keyfit add google --email me@acme.com   # the project's Google account, if it has none yet
firebase login:add me@acme.com          # sign the firebase CLI in as that account too (browser)
firebase login:use me@acme.com          # in the project's Firebase folder: binds it to that sign-in
keyfit add firebase                     # records the Firebase project the folder uses, checks both
```

`keyfit add firebase` takes the project the CLI uses in the folder (`firebase use`, else `.firebaserc`; `--project-id ID` for another). When the project has no Google account yet, the folder's sign-in becomes it. A binding belongs to one folder, matched by its exact path, so every worktree needs its own: starting a tool yourself in a terminal in a worktree that isn't bound yet asks `Bind this folder now? [Y/n]` and runs `firebase login:use` there. An expired or revoked sign-in asks `Sign in now? [Y/n]` and runs `firebase login:add` (choose the account in the browser: it renews that sign-in), or `firebase login --reauth` when it is the CLI's default login.

**Limits.** Firebase is checked by account and project. The firebase CLI binds a sign-in to the exact path of a folder with a `firebase.json`, so a worktree, or a folder reached through a symlink, needs its own `firebase login:use`. Outside every Firebase folder (a monorepo's top, say) the CLI uses its default login: Keyfit's `firebase` adds `--account`, and agents started there get a warning, as `npx firebase-tools` there uses the default login. Other Firebase projects are refused with `--project` and `firebase use`, also another alias of the same app (`staging`): map the project the folder works on. The online check reads the access token the CLI keeps for the account and sends it only to Google; the refresh token is never used or sent (the CLI renews the access token itself). The account must see the project in the Firebase Management API. Application default credentials aren't checked; `GOOGLE_APPLICATION_CREDENTIALS` is removed. Verified with firebase-tools 15.26.0.

## Shared by several tools

### Google account

- **Map it:** `keyfit add google --email me@acme.com`, the project's Google account (work or personal), for every Google tool.
- **`keyfit init` offers:** the project's one Google account, the work or the personal email, picked from the accounts `gcloud` and the firebase CLI are signed in to.
- **The tool gets:** nothing of its own: it is the email every Google tool of the project is checked against: [Google Cloud](#google-cloud), [Firebase](#firebase), [Gemini CLI](#gemini-cli) and the [Gmail, Drive and Calendar](#gmail-drive-and-calendar) MCP servers.
- **Checked before it starts:** it is an email address. Each Google tool's own check compares its sign-in with it.

### Linear

- **Map it:** `keyfit add linear`: paste a key (hidden), which is checked with Linear and stored in the Keychain. Or a key you keep elsewhere: `keyfit add linear --key env:LINEAR_ACME_API_KEY` (`env:VAR`, `keychain:service/account`).
- **`keyfit init` offers:** a personal API key, typed at a hidden prompt. Keyfit checks it with Linear, then stores it in the macOS Keychain. It also imports `export LINEAR_API_KEY=$VARIABLE` lines from the repository's `.envrc`.
- **The tool gets:** `LINEAR_API_KEY`, plus the project's own variable when the key is an `env:` reference. Every other Linear key variable is removed: any name with `LINEAR` and `KEY` or `TOKEN` in it, in any case (`LINEAR_TOKEN`, `GLOBEX_LINEAR_KEY`, `linear_api_key`), but not LinearB's `LINEARB_…`.
- **Checked before it starts:** the key is present, shaped like a Linear key and not pasted twice. Then Linear's API must report the expected workspace.

**Linear MCP servers are matched by the keys they read.** A Linear server is one with `mcp.linear.app` in its address or command (in any case, with any port; `npx mcp-remote https://mcp.linear.app/mcp` counts), or with a `LINEAR_…` variable or a Linear key in its `env`. Keyfit puts each project's checked key into `LINEAR_API_KEY`, so one Linear server that reads `${LINEAR_API_KEY}` works in every project, always with that project's workspace. A Linear server stays visible only if every variable it names is `LINEAR_API_KEY` or the project's own `env:` variable, and a key written into its definition is the project's own; a local server that names no key reads `LINEAR_API_KEY` and stays too. A server signed in inside the agent (OAuth, no key) could be on any workspace, so it is hidden unless you give it to a project with `keyfit add mcp NAME`. In a project without a Linear account, every Linear server is hidden. To add a server that works in every project once:

```bash
claude mcp add --scope user --transport http linear https://mcp.linear.app/mcp --header 'Authorization: Bearer ${LINEAR_API_KEY}'
codex mcp add linear --url https://mcp.linear.app/mcp --bearer-token-env-var LINEAR_API_KEY
```

### Gmail, Drive and Calendar

- **Map it:** `keyfit add google-mcp --server gmail_acme`, a Gmail, Drive or Calendar MCP server signed in as the project's [Google account](#google-account).
- **`keyfit init` offers:** which of the Gmail, Drive and Calendar MCP servers in your agents' configs belong to the project (asked when the project has a Google account). Servers whose login is signed in as another account are named, not offered. The ones you take are hidden in every other project.
- **The agents get:** the servers belong to the project: every other project's sessions don't see them, and this project's sessions don't see any other Gmail, Drive or Calendar server. `GMAIL_CREDENTIALS_PATH`, `WORKSPACE_MCP_CREDENTIALS_DIR`, `GOOGLE_MCP_CREDENTIALS_DIR`, `GOOGLE_CALENDAR_MCP_TOKEN_PATH`, `GOOGLE_DRIVE_MCP_TOKEN_PATH` and `USER_GOOGLE_EMAIL` inherited from your shell are removed, so each server reads the login its own definition names. So are the variables that would sign a claimed server in some other way than with its login, a service account or a token of its own: for Google Drive MCP `GOOGLE_APPLICATION_CREDENTIALS`, `GOOGLE_DRIVE_MCP_SUBJECT`, `GOOGLE_DRIVE_MCP_ACCESS_TOKEN`, `GOOGLE_DRIVE_MCP_REFRESH_TOKEN`, `GOOGLE_DRIVE_MCP_CLIENT_ID`, `GOOGLE_DRIVE_MCP_CLIENT_SECRET` and `GOOGLE_TOKEN_PATH`; for Google Calendar MCP `GOOGLE_SERVICE_ACCOUNT_KEY`, `GOOGLE_SERVICE_ACCOUNT_SUBJECT` and `GOOGLE_APPLICATION_CREDENTIALS`; for Google Workspace MCP `GOOGLE_SERVICE_ACCOUNT_KEY_FILE` and `GOOGLE_SERVICE_ACCOUNT_KEY_JSON`. A project with only Gmail AutoAuth keeps `GOOGLE_APPLICATION_CREDENTIALS`.
- **Checked before an agent starts:**
  - Every definition of the server in the five agents' configs (yours, Claude's local scope and the folder's) runs the recorded server, reads the recorded login (a default login under the `HOME` and `XDG_CONFIG_HOME` the definition sets, as the server looks for it) and sets none of the variables above that would sign it in some other way.
  - Google Drive MCP not signed in yet is a block, not a warning, when the folder holds `google-tokens.json` or `.gcp-saved-tokens.json`: the server would move that old token file into its login and act as its account.
  - Every account in that login is the project's Google account, by the email the login names (its file name, `cached_email` or `email`).
  - Then Google must name that account (tokeninfo, Gmail's profile or the primary calendar), asked with the saved token while it is valid, else with one renewed with the server's own OAuth client (see Limits below).

Gmail, Drive and Calendar reach an agent through MCP servers. The ones that run on your Mac keep their own login, a file or folder of OAuth tokens, so a server is signed in to one Google account. Name one server per account in your agents' MCP configs (`gmail_acme`, `gmail_personal`), each with a login of its own, and give each to the projects of its account:

```bash
claude mcp add --scope user --env GMAIL_CREDENTIALS_PATH=$HOME/.gmail-mcp/acme.json gmail_acme -- npx @gongrzhe/server-gmail-autoauth-mcp
GMAIL_CREDENTIALS_PATH=$HOME/.gmail-mcp/acme.json npx @gongrzhe/server-gmail-autoauth-mcp auth   # sign in as me@acme.com
keyfit add google-mcp --server gmail_acme    # inside the project: checks the login, then claims the server
```

Keyfit knows four servers by name, recognised by the package they run:

| Server | Package |
|---|---|
| [Google Workspace MCP](https://github.com/taylorwilsdon/google_workspace_mcp) | `workspace-mcp` (a folder with one `<email>.json` per account) |
| [Gmail AutoAuth](https://www.npmjs.com/package/@gongrzhe/server-gmail-autoauth-mcp) | `@gongrzhe/server-gmail-autoauth-mcp` |
| [Google Calendar MCP](https://github.com/nspady/google-calendar-mcp) | `@cocal/google-calendar-mcp` |
| [Google Drive MCP](https://github.com/piotr-agier/google-drive-mcp) | `@piotr-agier/google-drive-mcp` |

It reads where each keeps its login from the server's own definition (`env`), else the server's default. Any other server works with its login file named: `keyfit add google-mcp --server NAME --login PATH`. `keyfit add google-mcp` refuses a server whose login holds another account, and a login file shared by several accounts (the Calendar, Drive and Workspace servers keep several in one place, and a call can use any of them): give each account's server a login of its own.

**Claude's own Gmail, Google Drive and Google Calendar connectors are outside Keyfit's reach.** The ones you connect in claude.ai's settings belong to the Claude account, not to the Mac: Keyfit can't see which Google account they are signed in to, and can't choose between two Gmails connected to one Claude account. What it can do is pick the Claude account per project (`keyfit add claude`): a work Claude account with only the work Gmail connected gives a work folder the work Gmail. Claude Code has no per-project connector accounts either ([anthropics/claude-code#27302](https://github.com/anthropics/claude-code/issues/27302)). The same holds for Google's own remote Workspace servers (`gmailmcp.googleapis.com`, `drivemcp.googleapis.com`, `calendarmcp.googleapis.com`, …): each agent keeps their sign-in itself. In a project with Google Workspace servers they are hidden; `keyfit add mcp <its name>` gives one to a project, unchecked.

**Limits.** Google Workspace MCP servers are checked by the login they read. Keyfit reads the email a login names: the Workspace server's file names, the Calendar server's `cached_email`, the Drive server's `email`, an `id_token`. Google must then name the same account, asked with the token saved in the login while it is valid. Once that has expired, Keyfit renews it the way the server does: the login's refresh token and the server's own OAuth client go to Google's token endpoint only, the fresh token answers that one question and is dropped, and nothing is written to the login. This is the one place Keyfit uses a refresh token ([security](security.md)).

- **Gmail AutoAuth's login names no account,** and the server never saves a renewed token, so this is how Keyfit knows whose it is: Google confirms it at every check, hours after `… auth`, and another account is refused.
- **Where Keyfit looks for the OAuth client** is where the server reads it: in the Workspace server's login, Gmail AutoAuth's `GMAIL_OAUTH_PATH` or `~/.gmail-mcp/gcp-oauth.keys.json`, the Drive server's `GOOGLE_DRIVE_OAUTH_CREDENTIALS` or `gcp-oauth.keys.json` in its config folder, the Calendar server's `GOOGLE_OAUTH_CREDENTIALS` (a client file inside the Calendar package isn't looked for). The variable counts only in your own configs and your shell, never in a definition the folder brings (`.mcp.json`, `opencode.json`, `.github/mcp.json`, `.gemini/settings.json`, Codex's project config), so a repository can't choose the client Keyfit renews with; and only a regular file of at most 64 KB is read.
- **Without a client or a refresh token,** the local check decides (`not confirmed online`), and a login that names no account is a warning, `account not confirmed`, at every agent start; the project still sees only its own Gmail server, reading the project's login file.
- **Servers Keyfit doesn't recognise by their package** (a global install under another name, a server run from a checkout) are hidden from other projects only once claimed, and are checked with `--type` or `--login`.

### MCP ownership

- **Map it:** `keyfit add mcp 'globex_cloud_*'`: these MCP servers belong to this project, and every other project stops seeing them.
- **The agents get** the servers to hide, for that session only:
  - Claude gets `--settings` with the servers to hide, merged into your own `--settings` if you give one; servers of `--mcp-config` count too.
  - Codex gets `-c mcp_servers.NAME.enabled=false`; a `-c` of yours that turns one back on is refused.
  - OpenCode gets them switched off in `OPENCODE_CONFIG_CONTENT`.
  - Copilot CLI gets `--disable-mcp-server=NAME`.
  - Gemini CLI gets `--allowed-mcp-server-names` with every server it keeps.
- **What stays visible:**
  - A [Linear](#linear) server in your own config stays visible only if every key it reads or carries is the project's checked key. One signed in inside the agent stays only in a project that claims it, and in a project without a Linear account no Linear server stays. Linear servers in the folder's `.mcp.json` (or `opencode.json`, `.github/mcp.json` for Copilot CLI, `.gemini/settings.json` for Gemini CLI) are hidden unless the project claims them.
  - Servers another project claimed (with `keyfit add mcp` or `keyfit add google-mcp`) are hidden unless this project claims them too.
  - In a project with Google Workspace servers, every other Gmail, Drive or Calendar server is hidden too.
  - All other servers stay visible.
- **Where Keyfit looks:** each agent's own config and the folder's (see [security](security.md#what-keyfit-can-see)). For Claude that includes its local scope (`claude mcp add` without `--scope`), for the folder, the repository's top and a worktree's main checkout. For Codex it includes the profile file `codex --profile NAME` reads (`NAME.config.toml` next to `config.toml`) and the `.codex/config.toml` of the folders Codex trusts. If an agent loads a config file Keyfit can't parse, that agent doesn't start: Keyfit couldn't hide the servers in it.

Nothing is written into your repository or into the tools' config files: the servers are hidden through each agent's command line or environment.
