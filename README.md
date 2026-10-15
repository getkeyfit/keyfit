# Keyfit

`claude`, `codex`, `opencode`, `copilot`, `gemini`, `aws`, `gh`, `vercel`, `supabase`, `wrangler`, `convex`, `gcloud` and `firebase` use the accounts of the project you are in. Keyfit checks those accounts before the tool starts, and if a check fails, the tool does not start.

After a one-time `keyfit setup`, you keep typing `claude`, `codex`, `opencode`, `copilot`, `gemini`, `aws`, `gh`, `vercel`, `supabase`, `wrangler`, `convex`, `gcloud` and `firebase` as usual. Keyfit works out which project the current folder belongs to. It then checks that project's accounts with the services themselves: the AWS account behind the profile, the Linear workspace behind the key, the GitHub login, the Vercel login and team, the Supabase organization, the Cloudflare account behind wrangler's sign-in, the Convex team, the Google account and Google Cloud project behind gcloud's configuration, the Firebase project behind the firebase CLI's sign-in, the Claude organization, the Codex login, OpenCode's sign-ins, the GitHub account Copilot CLI runs as and the Google account Gemini CLI is signed in to. If everything matches, the tool starts with only that project's credentials. If a check fails, the tool does not start and you see the command that fixes it. Folders you have not registered work exactly as before.

Most tools that switch credentials per folder assume the switch worked. Keyfit asks each service who you are signed in as, and stops the tool if the answer is wrong.

## Who it's for

You use Claude Code, Codex, OpenCode, Copilot CLI or Gemini CLI for several clients or companies, and each one has its own AWS account, Linear workspace, GitHub login, Vercel team, Supabase organization, Cloudflare account, Convex team, Google account (work or personal), Google Cloud project and Firebase project, Claude organization or OpenCode sign-ins. You want an agent working in one client's repository never to act with another client's credentials, and you don't want to remember to switch anything.

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
keyfit setup --rc     # shims for claude, codex, opencode, copilot, gemini, aws, gh, vercel, supabase, wrangler, convex, gcloud and firebase, plus one line in your shell startup file
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
- **Copilot CLI:** whether Copilot CLI runs as the project's GitHub account (asked when `copilot` is installed and you picked a GitHub account).
- **Vercel:** whether to use the login the `vercel` CLI has now, with the team selected in it.
- **Supabase:** whether to use the login the `supabase` CLI has now. Keyfit keeps a copy of its token in the macOS Keychain (macOS may ask once to allow that).
- **Cloudflare:** the wrangler sign-in (auth profile) bound to the repository's folder, and the Cloudflare account it deploys to. Keyfit keeps no Cloudflare token: wrangler keeps the sign-in, Keyfit checks it.
- **Convex:** whether to use the login the `convex` CLI has now (`npx convex login`). Keyfit records the team the project's deployment belongs to.
- **Google:** the project's one Google account, the work or the personal email, picked from the accounts `gcloud` and the firebase CLI are signed in to. Every Google tool of the project is checked against it: `gcloud`, the firebase CLI and Gemini CLI.
- **Google Cloud:** a gcloud configuration signed in as that account, and the Google Cloud project it works on. Keyfit keeps no Google token: gcloud keeps the sign-in, Keyfit checks it.
- **Firebase:** the Firebase project the repository's `.firebaserc` names. The firebase CLI keeps the sign-in and binds the folder to it (`firebase login:use`); Keyfit checks both.
- **Gemini CLI:** whether to use the Google sign-in in `~/.gemini` (when it is that account), or else a Gemini CLI folder of the project's own, where you sign in once (asked when `gemini` is installed and the project has a Google account).
- **Linear:** a personal API key, typed at a hidden prompt. Keyfit checks it with Linear, then stores it in the macOS Keychain.

Skipped accounts are simply not checked, with one catch: inside the project, `aws`, `gh` and `vercel` start only once it has an AWS, GitHub or Vercel account. Skip GitHub, and `gh` waits in that repository until you run `keyfit add github --login <login>`. `keyfit status` lists what is still unmapped. `supabase`, `wrangler`, `convex`, `gcloud` and `firebase` are the exceptions: many projects use them only locally (`supabase start`, `wrangler dev`, an anonymous local Convex deployment, `gcloud components`, the Firebase emulators), so without a Supabase, Cloudflare, Convex, Google Cloud or Firebase account they run as before.

It ends with `keyfit status`:

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
```

When a check fails, the tool does not start:

```
$ claude
keyfit: claude not started, acme-api needs attention
  ✘ aws        acme-dev → …4321   SSO session expired
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
2. **Verify.** Keyfit builds the project's environment and checks each account you mapped. Fast local checks run on every launch: does the profile exist, has the sign-in expired, is the key well-formed. Then come the online checks: who do AWS, Linear, GitHub (for `gh` and for Copilot CLI), Vercel, Supabase, Cloudflare, Convex, Google (for gcloud, the firebase CLI and Gemini CLI) and Claude say you are, and which sign-ins does OpenCode see? A successful online check is reused for 30 minutes. If any check fails, the tool does not start.
3. **Inject.** The real tool starts with that environment: the project's credentials are set, and conflicting ones inherited from your shell are removed. MCP servers that belong to other projects are hidden for that session. Nothing is written into your repository or into the tools' config files.

Some details:

- **Which accounts each tool gets:** `claude`, `codex`, `opencode`, `copilot` and `gemini` get, and check, all of the project's accounts. `aws` gets only the AWS account, `gh` only the GitHub account, `vercel` only the Vercel login, `supabase` only the Supabase token, `wrangler` only the Cloudflare account, `convex` only the Convex login, `gcloud` only the Google Cloud account and `firebase` only the Firebase account.
- **Expired sign-ins are one keypress.** When you start a tool in a terminal and the only problem is an expired or missing sign-in, Keyfit asks `Sign in now? [Y/n]`, runs the sign-in, checks again and starts the tool. A project folder that isn't bound to its wrangler profile or to the project's firebase sign-in yet works the same way, with `Bind this folder now? [Y/n]`. Agents and scripts are never asked; they get the message and the fix.
- **Sign-in commands always run**, with the project's environment, so they fix the right account. These are `aws sso login`, `aws login`, `aws configure`, `claude auth …`, `codex login`, `opencode auth …` (with `--standalone`, so the sign-in lands in the project's OpenCode database), `copilot login` (except in a project with a Copilot account: there it would change only Copilot's own login, which the project doesn't use, so Keyfit stops it and names the `gh` sign-in instead), `gemini --prompt-interactive /quit` (Gemini CLI has no sign-in command: this starts it in the project's Gemini CLI folder, where its own sign-in dialog opens, and quits once you are signed in), `gh auth …` and `vercel login`, `logout`, `switch`, `whoami` and `teams`. `supabase login` and `logout` run too, except in a project with a Supabase account: there they would change only the CLI's own login, which that project doesn't use, so Keyfit stops them and names `keyfit add supabase` instead. `wrangler auth create`, `activate`, `deactivate`, `list` and `whoami` run as well, unless they would move a registered folder to another project's profile (or a `.env` there sets wrangler's API host or sign-in); `wrangler login` and `logout` only change wrangler's default sign-in, so in a project with its own profile Keyfit stops them and names `wrangler auth create NAME`. `convex login` and `logout` run where the project's Convex account is the CLI's own login, and are stopped where Keyfit keeps the project's token. `gcloud auth login`, `auth list`, `config set` and `config configurations create`, `list` and `describe` run in the project's configuration, so a sign-in or a setting lands there; `gcloud auth login` as anyone but the project's Google account is stopped, and so is a `config set` that would move a project's configuration to another account or project. `firebase login`, `login:add`, `login:use`, `login:list`, `logout` and `use` run too, except a `login:use` that would bind a project's folder to another account (outside every Firebase folder, where it would change the CLI's default login, it is stopped too), a `use` of another Firebase project, and a `login --reauth` that would replace the CLI's default login with the project's account (Keyfit names `firebase login:add` instead).
- **Inside a Claude, Codex, OpenCode, Copilot CLI or Gemini CLI session,** the `aws`, `gh`, `vercel`, `supabase`, `wrangler`, `convex`, `gcloud` and `firebase` commands it runs stay on that session's project, even after a `cd` elsewhere. The session also has the project's `SUPABASE_ACCESS_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`, `CONVEX_OVERRIDE_ACCESS_TOKEN`, `COPILOT_GITHUB_TOKEN`, gcloud configuration (`CLOUDSDK_ACTIVE_CONFIG_NAME`, `CLOUDSDK_CORE_ACCOUNT`, `CLOUDSDK_CORE_PROJECT`) and Gemini CLI folder (`GEMINI_CLI_HOME`, and `GEMINI_API_KEY` for a key account), so `npx supabase`, `npx wrangler`, `npx convex`, `npx @github/copilot`, `npx @google/gemini-cli`, `bq` and `gsutil` (and `npm run deploy`) use the project's accounts too. `npx firebase-tools` uses the sign-in its folder is bound to, which the session's start checked; `FIREBASE_TOKEN` and `GOOGLE_APPLICATION_CREDENTIALS` are removed, so nothing else signs it in.
- **Claude Code's status line** shows the project and its accounts, unless you configured a status line of your own.
- **Superset:** when Superset's agent wrapper (`~/.superset/bin`) is on PATH, it goes first and the shims right behind it, ahead of everything else. The chain is Superset wrapper → Keyfit → real tool, and Superset's hooks keep working (for OpenCode too: Keyfit leaves the `OPENCODE_CONFIG_DIR` the wrapper sets). Without Superset, the shims go first.

## Speed

Measured on a MacBook Pro with an Apple M4 (16 GB, macOS 26.6, Python 3.13.3), with other apps running, a shim adds:

- **About 0.1 s** in a folder you haven't registered, and for `aws`, `vercel`, `supabase`, `wrangler`, `convex`, `gcloud` and `firebase` in a registered project when its checks are cached.
- **About 0.2 s** for `gh`, `claude`, `codex`, `opencode`, `copilot` and `gemini` in a project with a GitHub or Copilot account. Half of that is `gh auth token`, which Keyfit runs to get the account's token (once: a Copilot account that is the project's GitHub login reuses that token once its check confirmed it). Inside a session that Keyfit started, `gh` and `copilot` keep the token their check already confirmed, and cost about 0.1 s.
- **0.5–2 s** when the checks have to go online, at most once every 30 minutes per project (and right after a new sign-in). GitHub, Linear, Vercel, Supabase, Cloudflare, Convex and Claude are checked in parallel. Cloudflare's check starts wrangler (`wrangler whoami`), which takes 1–2 s, OpenCode's starts OpenCode (`opencode auth list`), about 2 s, and Google Cloud's starts gcloud (`gcloud auth print-access-token`, 0.6 s before it renews the token) and then asks Google twice. Gemini CLI's asks Google once, and only while the sign-in's token is still valid. Firebase's asks Google twice; when the access token the firebase CLI keeps has expired (it lasts an hour), it first starts the CLI to renew it (`firebase projects:list`, about 2 s). AWS takes about 1 s with a console sign-in (`aws login`), because its check starts the AWS CLI; longer when your credential tool has to renew a login.

`--version` is checked like any other command, so `time gh --version` inside a project shows the full cost. To measure Keyfit alone on your Mac, with stub tools and no accounts or network: `python3 scripts/bench_shim.py`.

## What is checked

| Connection | Map it with | What the tool gets | Checked before it starts |
|---|---|---|---|
| AWS | `keyfit add aws --profile acme-dev` | `AWS_PROFILE` and `AWS_EXPECTED_ACCOUNT_ID` (and the region, if you set one). Inherited AWS keys, tokens, profile, config-file and region variables are removed | A named profile (never `default`) exists, and the account hints in its config match. The SSO or `aws login` session has not expired. Then `aws sts get-caller-identity` must return the expected 12-digit account |
| Linear | `keyfit add linear` | `LINEAR_API_KEY`, plus the project's own variable when the key is an `env:` reference. Every other Linear key variable is removed | The key is present, shaped like a Linear key and not pasted twice. Then Linear's API must report the expected workspace |
| GitHub | `keyfit add github --login octocat` | `GH_TOKEN`, read from `gh`'s stored login for that account. Other GitHub token variables, `GH_HOST` and `GH_CONFIG_DIR` are removed | `gh` has a token for the login, and `api.github.com/user` must return that login. A different git author email or SSH host alias in the repository shows as a warning |
| Vercel | `keyfit add vercel` | The login's folder as `--global-config` (nothing for the CLI's default folder) and the team as `--scope`. `VERCEL_ORG_ID`, `VERCEL_PROJECT_ID`, `VERCEL_TEAM_ID` (and their older `NOW_` names) and `VERCEL_TOKEN` are removed | The folder holds a login, and the repository isn't linked (`.vercel/project.json`, `.vercel/repo.json`) to another team. Then `api.vercel.com` must return the expected username and, with a team, let that login reach the team |
| Supabase | `keyfit add supabase` | `SUPABASE_ACCESS_TOKEN`, the token Keyfit keeps for the account, and `SUPABASE_PROFILE=supabase`, so the token only goes to `api.supabase.com` | The token is present and shaped like a Supabase access token. Then `api.supabase.com` must accept it and list the expected organization. When the folder is linked (`supabase/.temp/project-ref`, or `SUPABASE_PROJECT_ID`), the linked project must belong to that organization |
| Cloudflare | `keyfit add cloudflare` | `CLOUDFLARE_ACCOUNT_ID`, the project's account, so wrangler (also `npx wrangler`) works on it or fails. `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_API_KEY`, `CLOUDFLARE_EMAIL` and their older `CF_` names are removed: they beat every wrangler profile. wrangler's folder binding picks the sign-in. `CLOUDFLARE_API_BASE_URL` is set to Cloudflare's own API, and inherited `WRANGLER_AUTH_DOMAIN`, `WRANGLER_AUTH_URL`, `WRANGLER_TOKEN_URL`, `WRANGLER_REVOKE_URL` and `WRANGLER_CLIENT_ID` are removed, so the profile's sign-in goes only to Cloudflare | No `.env`, `.env.local` or `.env.<env>` in the folder sets one of those five or the API host (wrangler loads them into its environment). The wrangler profile exists, and the folder resolves to it: bound there or in a folder above, not shadowed by a subfolder bound to another profile. The `wrangler.json`, `wrangler.jsonc` or `wrangler.toml` wrangler would read names no other `account_id` (also under `[env.*]`), and the wrangler `npx` would run is 4.106 or newer. Then `wrangler whoami` in the folder must list the account |
| Convex | `keyfit add convex --cli` | `CONVEX_OVERRIDE_ACCESS_TOKEN`, the account's token (the convex CLI takes it before its own login and before deploy keys). `CONVEX_PROVISION_HOST` is removed, so the token only goes to `api.convex.dev`, and so are inherited `CONVEX_DEPLOYMENT`, `CONVEX_DEPLOY_KEY` and `CONVEX_DEPLOYMENT_TOKEN` that the folder's own `.env.local` or `.env` disagree with | A token is present and isn't a deploy key. Then `api.convex.dev` must accept it and list the expected team. The deployment the folder points at (`CONVEX_DEPLOYMENT` or a deploy key, from the environment, `.env.local` or `.env`) must belong to that team. Local and self-hosted deployments need no account |
| Claude Code | `keyfit add claude` | The account's config folder: `CLAUDE_CONFIG_DIR`, left unset for the default `~/.claude`. API keys, OAuth tokens, gateway URLs and cloud-provider switches are removed | `claude auth status` must report a claude.ai subscription login (not an API key), in the expected config folder and organization. With `--pin-email`, it must also be the exact login |
| Codex | `keyfit add codex` | `CODEX_HOME`. `CODEX_API_KEY` is removed, and interactive Codex runs without its shared daemon | `auth.json` must hold a ChatGPT login and no API key. With `--pin-account`, it must also be the exact ChatGPT account. This check is local; there is no online check |
| OpenCode | `keyfit add opencode` | `OPENCODE_DB`, the account's database of sign-ins. `opencode` starts without `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`, `GOOGLE_GENERATIVE_AI_API_KEY`, `GOOGLE_API_KEY`, `GITHUB_TOKEN` and `OPENCODE_API_KEY`, and without every other key or token from your shell that OpenCode's own report names as a sign-in (`OPENROUTER_API_KEY`, `GROQ_API_KEY`, `AWS_BEARER_TOKEN_BEDROCK`, `CLOUDFLARE_API_TOKEN`, `GITLAB_TOKEN`, …; the names come from the last check, cached with it). Claude, Codex, Copilot CLI and Gemini CLI sessions keep them all. Sessions run on a private server (`--standalone`) instead of OpenCode's shared background server | The database must hold a sign-in for each provider recorded with the account (read-only, never a credential). Then `opencode auth list` in that environment must list them as stored. Each key or token it lists from your shell (a name containing `KEY` or `TOKEN`) gets a warning, "from your shell, not the project's account", and is left out of the session; a setting such as the `CLOUDFLARE_ACCOUNT_ID` Keyfit sets is not a key and stays. No provider is asked |
| Copilot CLI | `keyfit add copilot` | `COPILOT_GITHUB_TOKEN`, read from `gh`'s stored login for the account (the project's GitHub account unless you name another `--login`); Copilot CLI takes it before `GH_TOKEN`, `GITHUB_TOKEN`, its own login and gh's. Inherited Copilot tokens, `COPILOT_GH_HOST`, `GH_HOST` and the `COPILOT_PROVIDER_…` variables (a model provider of your own instead of the GitHub account) are removed | `gh` has a token for the login and it isn't a classic personal access token (`ghp_…`, which Copilot CLI refuses). Then `api.github.com/user` must return that login. Whether the account has a Copilot plan, Copilot CLI itself says when it starts |
| Google account | `keyfit add google --email me@acme.com` | Nothing of its own: it is the email every Google tool of the project is checked against (`gcloud`, the firebase CLI and Gemini CLI) | It is an email address. Each Google tool's own check compares its sign-in with it |
| Google Cloud | `keyfit add gcloud` | `CLOUDSDK_ACTIVE_CONFIG_NAME` (the project's gcloud configuration, for this process only; the one active everywhere else is untouched), `CLOUDSDK_CORE_ACCOUNT` (the project's Google account) and `CLOUDSDK_CORE_PROJECT`. Inherited `CLOUDSDK_…` variables that pick another configuration, account, project, credentials or endpoint are removed (those for gcloud's Python, a proxy or prompts stay), and so is `GOOGLE_APPLICATION_CREDENTIALS` | The configuration exists in gcloud's folder, its account is the project's Google account and its project the expected one, it takes no credentials from a file (`auth/access_token_file`), and gcloud holds a sign-in for the account. Then the token `gcloud auth print-access-token` prints for it must belong to that account (Google's tokeninfo) and see the project (Cloud Resource Manager) |
| Firebase | `keyfit add firebase` | Nothing is set: the folder's binding (`firebase login:use`) picks the firebase CLI's sign-in, for `npx firebase-tools` too. `FIREBASE_TOKEN` and `GOOGLE_APPLICATION_CREDENTIALS` are removed: they would act instead of the sign-in. Outside every Firebase folder (no `firebase.json` there or above), `firebase` gets `--account` with the project's Google account when the CLI's default login is another one | The firebase CLI is signed in as the project's Google account. In the CLI's project folder (the nearest one with a `firebase.json`) that is the account it uses (bound there with `firebase login:use`, or its default login), and the Firebase project it picks (`firebase use`, else `.firebaserc`) is the expected one. Then Google's tokeninfo must name the account for the access token the CLI keeps for it (renewed by the CLI first when it has expired), and the Firebase Management API must show the project to it |
| Gemini CLI | `keyfit add gemini` | `GEMINI_CLI_HOME`, the account's Gemini CLI folder (left unset for your own `~/.gemini`), and for a key account `GEMINI_API_KEY`. `gemini` starts without `GEMINI_API_KEY` (unless it is the account's), `GOOGLE_API_KEY`, `GOOGLE_CLOUD_PROJECT` (the account's `--cloud-project`, if you gave one), `GOOGLE_APPLICATION_CREDENTIALS`, `GOOGLE_GENAI_USE_VERTEXAI` and the other variables that would pick another sign-in, endpoint or credential store; Claude and Codex sessions keep them | Google sign-in: the folder's sign-in method is Google's (or not chosen yet), it holds a Google sign-in (`oauth_creds.json`), and the email Google wrote into it is the project's Google account. Then, while the sign-in's token is still valid, Google's tokeninfo must name that account. API key: the key is there and the folder uses the API-key method; then the Gemini API must accept the key |
| MCP ownership | `keyfit add mcp 'globex_cloud_*'` | Claude gets `--settings` with the servers to hide. Codex gets `-c mcp_servers.NAME.enabled=false`. OpenCode gets them switched off in `OPENCODE_CONFIG_CONTENT`. Copilot CLI gets `--disable-mcp-server=NAME`. Gemini CLI gets `--allowed-mcp-server-names` with every server it keeps | A Linear server (`mcp.linear.app`) in your user config stays visible only if it reads `LINEAR_API_KEY` or the project's own key variable, both of which hold the project's checked key; Linear servers in the folder's `.mcp.json` (or `opencode.json`, `.github/mcp.json` for Copilot CLI, `.gemini/settings.json` for Gemini CLI) are hidden. Servers another project claimed are hidden unless this project claims them too. All other servers stay visible |

Only the accounts you map are touched. For example, a project without a Linear mapping leaves your Linear variables as they are and checks nothing for Linear.

Inside a registered project, `aws`, `gh` and `vercel` don't start until the project has an AWS, GitHub or Vercel account mapped. Also, `aws --profile <another profile>` is refused there, and so are `vercel --scope <another team>`, `vercel --global-config <another folder>` and `vercel --token …`. `supabase` refuses `--profile` (other than `supabase`), `--org-id` of another organization, and a project of another organization named with `--project-ref`, `gen types --project-id`, `link <ref>`, `projects delete <ref>` or the link in a `--workdir` folder. `wrangler` refuses `--profile` (other than the project's), and a `--cwd` or `--config` whose folder is bound to another profile or whose config names another `account_id`, in every spelling wrangler's parser takes (`-c FILE`, `-c=FILE`, `-c/path/wrangler.toml`, `-ec FILE`, `--c FILE`; each config of `wrangler dev`). `convex` refuses `--admin-key`, `--team` of another team, and another team's deployment named with `--deployment`, `--deployment-name` or an `--env-file`. `gcloud` refuses `--configuration`, `--account` and `--project` with other values than the project's, `--access-token-file` (a token instead of the account) and `--flags-file` (Keyfit can't see what it holds). `firebase` refuses `--account` and `--project` with other values than the project's (an alias counts as the project `.firebaserc` names; `-P other`, `-Pother` and `-jP other` too), `--token`, and a `--config` whose folder is bound to another account or works on another project. The exception is AWS profiles of machine-wide tools that serve every project, such as a CI worker. List them in `~/.config/keyfit/registry.toml` and they run unchanged anywhere:

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

Starting a tool yourself in a terminal in a folder of the project that isn't bound yet (a new worktree, say) asks `Bind this folder now? [Y/n]`. Press Enter: Keyfit runs `wrangler auth activate` with the project's wrangler, checks again and starts the tool. In a Superset worktree it offers the folder that holds the repository's worktrees instead (`Bind ~/.superset/worktrees/acme-api now, for every Superset worktree of acme-api?`), so the next workspace needs nothing; when a worktree of another project is in that folder too, it offers only this worktree. Keyfit binds only the project's own profile, and only to a folder of that project: never another project's folder or one outside every project, never your home folder or a folder above it. Agents and scripts are never asked; they get the printed command.

**Convex** keeps one login per Mac (`~/.convex/config.json`) and has no account switch. Keyfit hands each session its project's token in `CONVEX_OVERRIDE_ACCESS_TOKEN`, which the convex CLI takes before its own login, so `npx convex` inside a Claude, Codex, OpenCode, Copilot CLI or Gemini CLI session acts as the project's account. An account is either the CLI's own login (`--cli`, read whenever a session starts) or a token Keyfit keeps in the Keychain (`--copy` copies the CLI's current login; without an option you paste one). The team comes from `--team`, else the team of the deployment in the folder's `.env.local`, else the login's only team:

```bash
npx convex login                          # sign the convex CLI in, if it isn't yet
keyfit add convex --cli                   # this project uses the CLI's login; its team is recorded
keyfit add convex --copy --name acme      # or keep a copy of that login, then sign the CLI in as your other account
keyfit add convex --cli --team globex     # a login that sees several teams
```

When the CLI's own login expires or is signed out, starting `claude`, `codex`, `opencode`, `copilot` or `convex` in a terminal offers `Sign in now?` and runs `npx convex login --force`. Convex's MCP server (`npx convex mcp start`) runs the same CLI, so inside a session it uses the project's account too.

**OpenCode** keeps every sign-in (`opencode auth login`, API keys too) in its database, `~/.local/share/opencode/opencode.db`, next to its sessions. `OPENCODE_DB` moves that database, so an account is a database: `keyfit add opencode` records OpenCode's own one and the providers signed in there. For a second set of sign-ins:

```bash
keyfit add opencode --new --name globex   # creates ~/.config/keyfit/opencode/globex for this project
opencode auth login                       # inside the project: Keyfit sends the sign-in to that database
keyfit add opencode --name globex --db ~/.config/keyfit/opencode/globex/opencode.db   # records the providers
```

OpenCode normally runs sessions in a background server that serves every folder and keeps the environment of whichever start launched it. Inside a project, Keyfit adds `--standalone` instead (a private server with the project's environment) to sessions (`opencode`, `run`, `mini`), sign-ins (`auth …`) and `models`, `stats` and `session …`, and refuses `--server`. `opencode DIR` with a folder of another project is refused too: start it there. Commands of the background server itself (`mcp`, `serve`, `service`, `api`, `plugin`, `debug`, `reload`, `pair`) and `upgrade` run unchanged, as outside a project. Inside a Claude, Codex, OpenCode, Copilot CLI or Gemini CLI session, though, the environment holds the project's accounts, and a background server started from there would keep them for every folder: there `api`, `reload` and an `opencode` in a folder outside every project get `--standalone`, and the other server commands (and `acp --login`) are refused; run them in a terminal of your own. Your configuration (`~/.config/opencode`, or the `OPENCODE_CONFIG_DIR` Superset's wrapper sets) stays shared; MCP servers other projects own are switched off for the session.

**Copilot CLI** signs in with GitHub, and Keyfit already knows the project's GitHub account, so that is its account: `keyfit add copilot` records the project's GitHub login (`--login` for another one gh is signed in to), and each session gets gh's token for it in `COPILOT_GITHUB_TOKEN`, which Copilot takes before its own login (`copilot login`) and gh's. Copilot's own folder (`~/.copilot`, or `COPILOT_HOME`: settings, sessions, `mcp-config.json`) stays shared. Sessions (`copilot`, `copilot -p …`, `init`, `workflow`) start with `--disable-mcp-server=NAME` for each server another project owns, from `mcp-config.json`, `.mcp.json` and `.github/mcp.json` up to the repository's top and `--additional-mcp-config`; `--enable-mcp-server` with such a server is refused, and so is `copilot -C DIR` with a folder of another project. Copilot's own commands (`mcp`, `config`, `plugin`, `update`, …) run as typed. The fix for a missing or wrong login is gh's sign-in, which Keyfit prints and never runs (it switches gh's active account):

```bash
gh auth login --hostname github.com --web   # sign in as the project's account, then switch back:
gh auth switch --user <your usual account>
keyfit add copilot                          # inside the project: Copilot CLI runs as its GitHub account
```

**Google Cloud** sign-ins stay with gcloud, which keeps named configurations: each has an account and a project, and `CLOUDSDK_ACTIVE_CONFIG_NAME` picks one for a single process. A project first says which Google account it uses, its work or its personal email, once; Keyfit then checks gcloud's configuration against it; the firebase CLI and Gemini CLI are checked against the same email. One configuration per client:

```bash
gcloud config configurations create acme --no-activate   # leaves the configuration you use elsewhere active
keyfit add google --email me@acme.com                    # the project's Google account
gcloud auth login me@acme.com                            # inside the project: the sign-in lands in configuration acme
gcloud config set project acme-prod --configuration acme
keyfit add gcloud --configuration acme                   # records the configuration and its project, checks both
```

`keyfit add gcloud` without `--configuration` takes the configuration signed in as the project's Google account (else the only one, else it asks), and its project (`--project-id ID` for another). When the project has no Google account yet, the configuration's account becomes it. Inside the project, `gcloud` runs as the project's account on its project whichever configuration is active; `gcloud config configurations activate` still switches it for the rest of your Mac.

**Firebase** sign-ins stay with the firebase CLI, which keeps several (`firebase login:add`) and binds one to each project folder, the folder with the `firebase.json` (`firebase login:use`). Keyfit checks that binding against the project's Google account, and the project the CLI picks there against the project's Firebase project:

```bash
keyfit add google --email me@acme.com   # the project's Google account, if it has none yet
firebase login:add me@acme.com          # sign the firebase CLI in as that account too (browser)
firebase login:use me@acme.com          # in the project's Firebase folder: binds it to that sign-in
keyfit add firebase                     # records the Firebase project the folder uses, checks both
```

`keyfit add firebase` takes the project the CLI uses in the folder (`firebase use`, else `.firebaserc`; `--project-id ID` for another). When the project has no Google account yet, the folder's sign-in becomes it. A binding belongs to one folder, matched by its exact path, so every worktree needs its own: starting a tool yourself in a terminal in a worktree that isn't bound yet asks `Bind this folder now? [Y/n]` and runs `firebase login:use` there. An expired or revoked sign-in asks `Sign in now? [Y/n]` and runs `firebase login:add` (choose the account in the browser: it renews that sign-in), or `firebase login --reauth` when it is the CLI's default login.

**Gemini CLI** keeps everything in `~/.gemini`: settings, sessions, extensions and the Google sign-in. `GEMINI_CLI_HOME` moves that folder, so an account is a folder, and its sign-in is checked against the project's Google account, the work or the personal email. `keyfit add gemini` takes your own `~/.gemini` when it is signed in as that account; for the other one, the project gets a folder of its own:

```bash
keyfit add google --email me@acme.com      # the project's Google account
keyfit add gemini --new                    # creates ~/.config/keyfit/gemini/acme (your GEMINI.md, commands, skills and extensions linked in)
gemini                                     # inside the project: Sign in now? opens Gemini CLI's own sign-in there; sign in as me@acme.com
```

Gemini CLI has no sign-in command; it signs in inside a session. So the sign-in Keyfit offers (`Sign in now?`) and prints is `gemini --prompt-interactive /quit`: Gemini CLI starts in the project's folder, shows its sign-in dialog with Sign in with Google selected, and quits once you are signed in (after Gemini CLI's own "press R to restart"); Keyfit checks the account and starts the session. Every other project with the same Google account shares that folder, so one sign-in serves them all. Signed in as the wrong account, the check names the file to remove (`oauth_creds.json` in Keyfit's folder; Keyfit never suggests removing your own `~/.gemini` sign-in, it suggests a folder of the project's own instead). A Gemini API key works too: `keyfit add gemini --key` checks a pasted key with the Gemini API and keeps it in the Keychain (`--key env:VAR` for one you keep elsewhere), with a folder of its own that uses the API-key method. A Workspace account that needs a Google Cloud project for Gemini Code Assist gets one with `--cloud-project ID`.

Sessions (`gemini`, `gemini QUERY`, `gemini -p …`) start with `--allowed-mcp-server-names` naming every server they keep, when the project must not see one: servers of the folder's settings (yours in the Gemini CLI folder, the folder's `.gemini/settings.json`, the system's) and of your extensions. Your own `mcp.allowed` and `mcp.excluded` settings are carried over, as the option replaces them for the session. An `--allowed-mcp-server-names` of your own that names another project's server is refused. Gemini CLI's own commands (`mcp`, `extensions`, `skills`, `hooks`, `gemma`) run as typed.

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
keyfit add copilot                 # Copilot CLI runs as the project's GitHub account; another one: --login LOGIN
keyfit add gemini                  # Gemini CLI's Google sign-in in ~/.gemini; another one: --new; a Gemini API key: --key
keyfit add vercel                  # the login the vercel CLI has now, and its team; another login: --new --name NAME
keyfit add supabase                # paste a Supabase token (hidden): checked, stored in the Keychain; or --cli
keyfit add cloudflare              # the wrangler profile bound to this folder and its Cloudflare account
keyfit add convex --cli            # the login the convex CLI has now, and the team of the folder's deployment
keyfit add google --email me@acme.com   # the project's Google account (work or personal), for every Google tool
keyfit add gcloud                  # the gcloud configuration signed in as that account, and its project
keyfit add firebase                # the Firebase project the folder uses; the firebase CLI's sign-in there must be that account
keyfit add mcp 'globex_cloud_*'    # these MCP servers belong to this project; every other project stops seeing them
keyfit run -- terraform plan       # run any other command with the project's accounts (checked first)
keyfit secret set NAME             # store a key from a hidden prompt; prints keychain:keyfit/NAME to use
keyfit secret list                 # names of the keys Keyfit stored (never the values)
```

`--project NAME` makes any `keyfit add …` change another project. Account names (`--name`) can be shared between projects. The registry lives in `~/.config/keyfit/registry.toml`. It stores where secrets live, never their values; see [security](https://github.com/FlamurMaliqi/keyfit/blob/main/docs/security.md).

## Limits

- **macOS only, for now.** Keys live in the macOS Keychain, and `keyfit setup` stops on other systems.
- **zsh and bash.** In another shell, put `~/.config/keyfit/bin` first on PATH yourself (after `~/.superset/bin` if you use Superset).
- **Thirteen tools.** Only `claude`, `codex`, `opencode`, `copilot`, `gemini`, `aws`, `gh`, `vercel`, `supabase`, `wrangler`, `convex`, `gcloud` and `firebase` go through Keyfit, when they are found through PATH (typed in a terminal, or run by an agent Keyfit started). These don't go through Keyfit:
  - other commands (terraform, npm, git over SSH), including `vc`, the Vercel CLI's short name, `npx supabase`, `npx wrangler`, `npx convex`, `npx firebase-tools`, `npx @google/gemini-cli`, `bq` and `gsutil` (inside a Claude, Codex, OpenCode, Copilot CLI or Gemini CLI session they get the project's token or account, but no checks of their own; outside one, `npx wrangler` and `npx firebase-tools` follow only the CLI's own folder binding, `npx convex` uses the CLI's own login and `bq` and `gsutil` gcloud's active configuration)
  - tools called by their full path
  - apps that don't start from your shell, such as IDE extensions and desktop apps

  To run any other command with the project's accounts: `keyfit run -- <command>`.
- **Online checks are cached for 30 minutes.** If a key is revoked within that window, Keyfit notices when the cached result expires, or at once with `keyfit status`. A new AWS SSO or console sign-in, a different Linear key, GitHub token, Vercel login or Supabase token or link, a new wrangler sign-in, a Convex login or deployment, a new gcloud sign-in or a changed gcloud configuration, a new firebase sign-in, a different Claude organization, a new OpenCode sign-in, a new token for the Copilot CLI account or a new Gemini CLI sign-in or key is checked again right away. When a service cannot be reached, a success from the last 24 hours is accepted with a warning; a wrong account never is.
- **One account of each kind per project:** one AWS profile, one Linear workspace, one GitHub login, one Vercel login and team, one Supabase token and organization, one wrangler profile and Cloudflare account, one Convex login and team, one Google account, one gcloud configuration and Google Cloud project, one Firebase project, one Claude login, one Codex login, one OpenCode database, one GitHub login for Copilot CLI, one Gemini CLI folder (with its sign-in or key).
- **Vercel is checked by login and team.** `keyfit add vercel` records the team selected in the login, if there is one. A project mapped without a team gets whichever team is selected at the time (`vercel switch` changes it), and a repository linked to another team isn't noticed.
- **Supabase is checked by organization.** A token belongs to a person, who may be in several organizations; Keyfit records the project's and checks the token can see it and the folder's linked project belongs to it. A folder linked to a preview branch shows a warning: Supabase's API doesn't say which organization a branch belongs to. `--db-url` and other direct database connections don't use the account, so Keyfit doesn't check them.
- **Cloudflare is checked by account.** wrangler 4.106 or newer is needed (older ones ignore profiles). A `CLOUDFLARE_API_TOKEN` in the folder's `.env` still beats the profile, as wrangler loads it itself; the account stays pinned, and the online check runs with what wrangler uses there. wrangler matches bound folders by their path as written, so a folder reached through a symlink or moved elsewhere needs its binding again. wrangler runs on Cloudflare's public API and sign-in only: a project that deliberately uses another host (Cloudflare's staging, a `compliance_region` such as FedRAMP, `CLOUDFLARE_API_BASE_URL`) isn't supported yet; `KEYFIT_DISABLE=1 wrangler …` runs such a command without Keyfit.
- **Convex is checked by team.** A login belongs to a person, who may be in several teams; Keyfit records the project's team and checks that the login sees it and that the folder's deployment belongs to it. The Convex project inside that team is shown, not enforced. Where a project deploy key (`project:…`) is in the folder, the convex CLI accepts no other login, so Keyfit leaves that key in charge and doesn't check it (a warning says so); local and self-hosted deployments aren't checked either. `CONVEX_OVERRIDE_ACCESS_TOKEN` is the CLI's own variable but not documented by Convex; it was verified on convex 1.46.
- **Google Cloud is checked by account and project.** The project's Google account must be the configuration's account and own the token gcloud prints for it, and the account must see the project (any role on it will do: Viewer includes `resourcemanager.projects.get`). Other Google Cloud projects are refused with `--project`, even ones the account may use: map the project the folder works on. A configuration that impersonates a service account (`auth/impersonate_service_account`) is checked as that service account. Application default credentials (`gcloud auth application-default login`, what the client libraries and Terraform use) are one login per Mac, not per configuration: Keyfit removes an inherited `GOOGLE_APPLICATION_CREDENTIALS` in a project with a Google Cloud account but doesn't check that login. Verified with gcloud 580.0.0.
- **Firebase is checked by account and project.** The firebase CLI binds a sign-in to the exact path of a folder with a `firebase.json`, so a worktree, or a folder reached through a symlink, needs its own `firebase login:use`. Outside every Firebase folder (a monorepo's top, say) the CLI uses its default login: Keyfit's `firebase` adds `--account`, and agents started there get a warning, as `npx firebase-tools` there uses the default login. Other Firebase projects are refused with `--project` and `firebase use`, also another alias of the same app (`staging`): map the project the folder works on. The online check reads the access token the CLI keeps for the account and sends it only to Google; the refresh token is never used or sent (the CLI renews the access token itself). The account must see the project in the Firebase Management API. Application default credentials aren't checked; `GOOGLE_APPLICATION_CREDENTIALS` is removed. Verified with firebase-tools 15.26.0.
- **GitHub means github.com.** GitHub Enterprise hosts are not supported, for `gh` or for Copilot CLI.
- **Gemini CLI is checked by Google account.** The email in the sign-in is Google's own (the id_token Google issued with it). Google confirms the sign-in online only while its token is valid (an hour after Gemini CLI last renewed it): renewing it takes Gemini CLI's own OAuth client, so Keyfit leaves that to Gemini CLI, and until then the local check decides (`not confirmed online` in the details). A Gemini API key is checked by whether the Gemini API accepts it, not whose it is. A sign-in you change inside a running session (`/auth`) is checked at the next start. `GEMINI_FORCE_ENCRYPTED_FILE_STORAGE` is removed in a project with a Gemini CLI account: it would move the sign-in into one Keychain item that every folder shares. The folder's `.gemini/settings.json` counts only in the folder Gemini CLI starts in, as Gemini CLI reads it; a server with a comma in its name can't be named in the allowlist, so it stays hidden. Verified with Gemini CLI 0.64.0.
- **Copilot CLI is checked by GitHub login.** The check confirms the login behind gh's token, not the Copilot plan: GitHub has no public endpoint for that, and Copilot CLI says so itself when it starts. MCP servers of installed Copilot plugins aren't seen by Keyfit, so they aren't hidden. Verified with Copilot CLI 1.0.97.
- **OpenCode is checked by provider.** OpenCode labels a sign-in with the provider's name, not the person, so Keyfit checks that the project's database holds a sign-in for each provider it recorded and that OpenCode itself sees them there, not whose they are. No provider is asked: OpenCode has no cheap call that confirms a sign-in without using it. A key in your shell for any provider (`OPENROUTER_API_KEY`, say) is left out of OpenCode sessions, with a warning, as soon as OpenCode's own report (`opencode auth list`) names it; a new one is checked at the next start. That report lists the keys and tokens OpenCode takes from the environment, not every setting it uses: Amazon Bedrock also starts from `AWS_PROFILE` or `AWS_ACCESS_KEY_ID`, which the report does not show. In a project with an AWS account those are the project's; in one without, they stay as in your shell. Verified with OpenCode 2.0.25.
- **Claude is checked by organization, Codex by login type.** Codex only has to be a ChatGPT login without an API key. Neither is checked down to the person unless you pin it with `--pin-email` or `--pin-account`. That way, login pools that rotate between accounts keep working.
- **Linear MCP servers are matched by the key variable they read.** Keyfit puts each project's checked key into `LINEAR_API_KEY`, so one Linear server that reads `${LINEAR_API_KEY}` works in every project, always with that project's workspace. Servers that read another variable are hidden unless it is the project's own `env:` variable. To add such a server once:

  ```bash
  claude mcp add --scope user --transport http linear https://mcp.linear.app/mcp --header 'Authorization: Bearer ${LINEAR_API_KEY}'
  codex mcp add linear --url https://mcp.linear.app/mcp --bearer-token-env-var LINEAR_API_KEY
  ```

## Turning it off

- **Skip Keyfit once:** `KEYFIT_DISABLE=1 claude` runs that one command without checks, in your unchanged environment. Everything it starts inherits the variable, so an agent started this way runs its own `aws`, `gh`, `vercel`, `supabase`, `wrangler`, `convex`, `gcloud` and `firebase` without Keyfit too.
- **See what would run:** `KEYFIT_DRY_RUN=1 claude` runs the checks in a registered project, then prints the command and the names of the environment variables that would change, and starts nothing. Commands that would run without checks are printed too, with the reason, and don't start either: `supabase`, `wrangler`, `convex`, `gcloud` or `firebase` in a project without an account of their kind, and, in any folder, OpenCode's own commands (`opencode serve`, `opencode mcp`, …) and AWS profiles under `[settings] shared_aws_profiles`. Three things still run as usual: sign-in commands, every other command in a folder that isn't registered, and anything run with `KEYFIT_DISABLE=1`.
- **Turn it off:** `keyfit setup --remove` deletes the shims and PATH files and takes Keyfit's line out of your shell startup files. Open a new terminal afterwards. Your registry stays, so `keyfit setup --rc` turns everything back on.
- **Delete its data too:** `keyfit setup --remove --purge` also deletes `~/.config/keyfit`: the registry, the check cache, and the Claude, Vercel, OpenCode and Gemini CLI login folders Keyfit made (with the logins in them). It asks first (`--yes` skips the question). Keys Keyfit stored in the Keychain are kept; it lists their names so you can delete them in Keychain Access.
- **Uninstall:** run `keyfit setup --remove` first, then `pipx uninstall keyfit` or `brew uninstall keyfit`. Done the other way round, the thirteen tools stop with a note that names the line to remove.

## More

- [Security](https://github.com/FlamurMaliqi/keyfit/blob/main/docs/security.md): what Keyfit reads, stores and sends, and what it does not protect against
- [Troubleshooting](https://github.com/FlamurMaliqi/keyfit/blob/main/docs/troubleshooting.md)
- [Contributing](https://github.com/FlamurMaliqi/keyfit/blob/main/CONTRIBUTING.md): running the tests and the rules every change follows
- [Reporting a vulnerability](https://github.com/FlamurMaliqi/keyfit/blob/main/SECURITY.md)
- [Changelog](https://github.com/FlamurMaliqi/keyfit/blob/main/CHANGELOG.md)

MIT licensed.
