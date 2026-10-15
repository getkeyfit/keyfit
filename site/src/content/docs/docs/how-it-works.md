---
title: How it works
description: How Keyfit resolves the project, checks its accounts and starts the tool.
sidebar:
  order: 2
---

<!-- Generated from README.md by site/scripts/sync_docs.py. Edit the source, not this file. -->

## How it works

1. **Resolve.** The shim asks git for the folder's common git directory. That way the main checkout and every worktree of a registered repository resolve to the same project, wherever the worktrees live (Superset's included). Outside a registered repository, the nearest registered parent folder counts. If nothing matches, the real tool runs unchanged.
2. **Verify.** Keyfit builds the project's environment and checks each account you mapped. Fast local checks run on every launch: does the profile exist, has the sign-in expired, is the key well-formed. Then come the online checks: who do AWS, Linear, GitHub (for `gh` and for Copilot CLI), Vercel, Supabase, Cloudflare, Convex, Google and Claude say you are, and which sign-ins does OpenCode see? A successful online check is reused for 30 minutes. If any check fails, the tool does not start.
3. **Inject.** The real tool starts with that environment: the project's credentials are set, and conflicting ones inherited from your shell are removed. MCP servers that belong to other projects are hidden for that session. Nothing is written into your repository or into the tools' config files.

Some details:

- **Which accounts each tool gets:** `claude`, `codex`, `opencode` and `copilot` get, and check, all of the project's accounts. `aws` gets only the AWS account, `gh` only the GitHub account, `vercel` only the Vercel login, `supabase` only the Supabase token, `wrangler` only the Cloudflare account, `convex` only the Convex login and `gcloud` only the Google Cloud account.
- **Expired sign-ins are one keypress.** When you start a tool in a terminal and the only problem is an expired or missing sign-in, Keyfit asks `Sign in now? [Y/n]`, runs the sign-in, checks again and starts the tool. A project folder that isn't bound to its wrangler profile yet works the same way, with `Bind this folder now? [Y/n]`. Agents and scripts are never asked; they get the message and the fix.
- **Sign-in commands always run**, with the project's environment, so they fix the right account. These are `aws sso login`, `aws login`, `aws configure`, `claude auth …`, `codex login`, `opencode auth …` (with `--standalone`, so the sign-in lands in the project's OpenCode database), `copilot login` (except in a project with a Copilot account: there it would change only Copilot's own login, which the project doesn't use, so Keyfit stops it and names the `gh` sign-in instead), `gh auth …` and `vercel login`, `logout`, `switch`, `whoami` and `teams`. `supabase login` and `logout` run too, except in a project with a Supabase account: there they would change only the CLI's own login, which that project doesn't use, so Keyfit stops them and names `keyfit add supabase` instead. `wrangler auth create`, `activate`, `deactivate`, `list` and `whoami` run as well, unless they would move a registered folder to another project's profile (or a `.env` there sets wrangler's API host or sign-in); `wrangler login` and `logout` only change wrangler's default sign-in, so in a project with its own profile Keyfit stops them and names `wrangler auth create NAME`. `convex login` and `logout` run where the project's Convex account is the CLI's own login, and are stopped where Keyfit keeps the project's token. `gcloud auth login`, `auth list`, `config set` and `config configurations create`, `list` and `describe` run in the project's configuration, so a sign-in or a setting lands there; `gcloud auth login` as anyone but the project's Google account is stopped, and so is a `config set` that would move a project's configuration to another account or project.
- **Inside a Claude, Codex, OpenCode or Copilot CLI session,** the `aws`, `gh`, `vercel`, `supabase`, `wrangler`, `convex` and `gcloud` commands it runs stay on that session's project, even after a `cd` elsewhere. The session also has the project's `SUPABASE_ACCESS_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`, `CONVEX_OVERRIDE_ACCESS_TOKEN`, `COPILOT_GITHUB_TOKEN` and gcloud configuration (`CLOUDSDK_ACTIVE_CONFIG_NAME`, `CLOUDSDK_CORE_ACCOUNT`, `CLOUDSDK_CORE_PROJECT`), so `npx supabase`, `npx wrangler`, `npx convex`, `npx @github/copilot`, `bq` and `gsutil` (and `npm run deploy`) use the project's accounts too.
- **Claude Code's status line** shows the project and its accounts, unless you configured a status line of your own.
- **Superset:** when Superset's agent wrapper (`~/.superset/bin`) is on PATH, it goes first and the shims right behind it, ahead of everything else. The chain is Superset wrapper → Keyfit → real tool, and Superset's hooks keep working (for OpenCode too: Keyfit leaves the `OPENCODE_CONFIG_DIR` the wrapper sets). Without Superset, the shims go first.

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
| OpenCode | `keyfit add opencode` | `OPENCODE_DB`, the account's database of sign-ins. `opencode` starts without `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`, `GOOGLE_GENERATIVE_AI_API_KEY`, `GOOGLE_API_KEY`, `GITHUB_TOKEN` and `OPENCODE_API_KEY` (OpenCode takes them as sign-ins of their providers; Claude and Codex sessions keep them). Sessions run on a private server (`--standalone`) instead of OpenCode's shared background server | The database must hold a sign-in for each provider recorded with the account (read-only, never a credential). Then `opencode auth list` in that environment must list them as stored, and no variable from your shell next to them. No provider is asked |
| Copilot CLI | `keyfit add copilot` | `COPILOT_GITHUB_TOKEN`, read from `gh`'s stored login for the account (the project's GitHub account unless you name another `--login`); Copilot CLI takes it before `GH_TOKEN`, `GITHUB_TOKEN`, its own login and gh's. Inherited Copilot tokens, `COPILOT_GH_HOST`, `GH_HOST` and the `COPILOT_PROVIDER_…` variables (a model provider of your own instead of the GitHub account) are removed | `gh` has a token for the login and it isn't a classic personal access token (`ghp_…`, which Copilot CLI refuses). Then `api.github.com/user` must return that login. Whether the account has a Copilot plan, Copilot CLI itself says when it starts |
| Google account | `keyfit add google --email me@acme.com` | Nothing of its own: it is the email every Google tool of the project is checked against (`gcloud` now; the firebase CLI and Gemini CLI later) | It is an email address. Each Google tool's own check compares its sign-in with it |
| Google Cloud | `keyfit add gcloud` | `CLOUDSDK_ACTIVE_CONFIG_NAME` (the project's gcloud configuration, for this process only; the one active everywhere else is untouched), `CLOUDSDK_CORE_ACCOUNT` (the project's Google account) and `CLOUDSDK_CORE_PROJECT`. Inherited `CLOUDSDK_…` variables that pick another configuration, account, project, credentials or endpoint are removed (those for gcloud's Python, a proxy or prompts stay), and so is `GOOGLE_APPLICATION_CREDENTIALS` | The configuration exists in gcloud's folder, its account is the project's Google account and its project the expected one, it takes no credentials from a file (`auth/access_token_file`), and gcloud holds a sign-in for the account. Then the token `gcloud auth print-access-token` prints for it must belong to that account (Google's tokeninfo) and see the project (Cloud Resource Manager) |
| MCP ownership | `keyfit add mcp 'globex_cloud_*'` | Claude gets `--settings` with the servers to hide. Codex gets `-c mcp_servers.NAME.enabled=false`. OpenCode gets them switched off in `OPENCODE_CONFIG_CONTENT`. Copilot CLI gets `--disable-mcp-server=NAME` | A Linear server (`mcp.linear.app`) in your user config stays visible only if it reads `LINEAR_API_KEY` or the project's own key variable, both of which hold the project's checked key; Linear servers in the folder's `.mcp.json` (or `opencode.json`, or `.github/mcp.json` for Copilot CLI) are hidden. Servers another project claimed are hidden unless this project claims them too. All other servers stay visible |

Only the accounts you map are touched. For example, a project without a Linear mapping leaves your Linear variables as they are and checks nothing for Linear.

Inside a registered project, `aws`, `gh` and `vercel` don't start until the project has an AWS, GitHub or Vercel account mapped. Also, `aws --profile <another profile>` is refused there, and so are `vercel --scope <another team>`, `vercel --global-config <another folder>` and `vercel --token …`. `supabase` refuses `--profile` (other than `supabase`), `--org-id` of another organization, and a project of another organization named with `--project-ref`, `gen types --project-id`, `link <ref>`, `projects delete <ref>` or the link in a `--workdir` folder. `wrangler` refuses `--profile` (other than the project's), and a `--cwd` or `--config` whose folder is bound to another profile or whose config names another `account_id`, in every spelling wrangler's parser takes (`-c FILE`, `-c=FILE`, `-c/path/wrangler.toml`, `-ec FILE`, `--c FILE`; each config of `wrangler dev`). `convex` refuses `--admin-key`, `--team` of another team, and another team's deployment named with `--deployment`, `--deployment-name` or an `--env-file`. `gcloud` refuses `--configuration`, `--account` and `--project` with other values than the project's, `--access-token-file` (a token instead of the account) and `--flags-file` (Keyfit can't see what it holds). The exception is AWS profiles of machine-wide tools that serve every project, such as a CI worker. List them in `~/.config/keyfit/registry.toml` and they run unchanged anywhere:

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

**Convex** keeps one login per Mac (`~/.convex/config.json`) and has no account switch. Keyfit hands each session its project's token in `CONVEX_OVERRIDE_ACCESS_TOKEN`, which the convex CLI takes before its own login, so `npx convex` inside a Claude, Codex, OpenCode or Copilot CLI session acts as the project's account. An account is either the CLI's own login (`--cli`, read whenever a session starts) or a token Keyfit keeps in the Keychain (`--copy` copies the CLI's current login; without an option you paste one). The team comes from `--team`, else the team of the deployment in the folder's `.env.local`, else the login's only team:

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

OpenCode normally runs sessions in a background server that serves every folder and keeps the environment of whichever start launched it. Inside a project, Keyfit adds `--standalone` instead (a private server with the project's environment) to sessions (`opencode`, `run`, `mini`), sign-ins (`auth …`) and `models`, `stats` and `session …`, and refuses `--server`. `opencode DIR` with a folder of another project is refused too: start it there. Commands of the background server itself (`mcp`, `serve`, `service`, `api`, `plugin`, `debug`, `reload`, `pair`) and `upgrade` run unchanged, as outside a project. Inside a Claude, Codex, OpenCode or Copilot CLI session, though, the environment holds the project's accounts, and a background server started from there would keep them for every folder: there `api`, `reload` and an `opencode` in a folder outside every project get `--standalone`, and the other server commands (and `acp --login`) are refused; run them in a terminal of your own. Your configuration (`~/.config/opencode`, or the `OPENCODE_CONFIG_DIR` Superset's wrapper sets) stays shared; MCP servers other projects own are switched off for the session.

**Copilot CLI** signs in with GitHub, and Keyfit already knows the project's GitHub account, so that is its account: `keyfit add copilot` records the project's GitHub login (`--login` for another one gh is signed in to), and each session gets gh's token for it in `COPILOT_GITHUB_TOKEN`, which Copilot takes before its own login (`copilot login`) and gh's. Copilot's own folder (`~/.copilot`, or `COPILOT_HOME`: settings, sessions, `mcp-config.json`) stays shared. Sessions (`copilot`, `copilot -p …`, `init`, `workflow`) start with `--disable-mcp-server=NAME` for each server another project owns, from `mcp-config.json`, `.mcp.json` and `.github/mcp.json` up to the repository's top and `--additional-mcp-config`; `--enable-mcp-server` with such a server is refused, and so is `copilot -C DIR` with a folder of another project. Copilot's own commands (`mcp`, `config`, `plugin`, `update`, …) run as typed. The fix for a missing or wrong login is gh's sign-in, which Keyfit prints and never runs (it switches gh's active account):

```bash
gh auth login --hostname github.com --web   # sign in as the project's account, then switch back:
gh auth switch --user <your usual account>
keyfit add copilot                          # inside the project: Copilot CLI runs as its GitHub account
```

**Google Cloud** sign-ins stay with gcloud, which keeps named configurations: each has an account and a project, and `CLOUDSDK_ACTIVE_CONFIG_NAME` picks one for a single process. A project first says which Google account it uses, its work or its personal email, once; Keyfit then checks gcloud's configuration against it, and the firebase CLI and Gemini CLI will be checked against the same email. One configuration per client:

```bash
gcloud config configurations create acme --no-activate   # leaves the configuration you use elsewhere active
keyfit add google --email me@acme.com                    # the project's Google account
gcloud auth login me@acme.com                            # inside the project: the sign-in lands in configuration acme
gcloud config set project acme-prod --configuration acme
keyfit add gcloud --configuration acme                   # records the configuration and its project, checks both
```

`keyfit add gcloud` without `--configuration` takes the configuration signed in as the project's Google account (else the only one, else it asks), and its project (`--project-id ID` for another). When the project has no Google account yet, the configuration's account becomes it. Inside the project, `gcloud` runs as the project's account on its project whichever configuration is active; `gcloud config configurations activate` still switches it for the rest of your Mac.

Vercel's MCP server (`https://mcp.vercel.com`) signs in on its own, so Keyfit doesn't check it. If it is signed in to one project's account, claim it for that project with `keyfit add mcp <its name>`; every other project stops seeing it.
