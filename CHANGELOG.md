# Changelog

All notable changes to Keyfit are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Gmail, Drive and Calendar MCP servers on the project's Google account.** `keyfit add google-mcp
  --server NAME` gives a project a Google Workspace MCP server that runs on the Mac with a login of its
  own, signed in as the project's Google account (the work or the personal email of `keyfit add
  google`); servers of one Google account are shared by its projects (`[accounts.google-mcp.NAME]`).
  Four servers are known by the package they run, with where each keeps its login: Google Workspace MCP
  (`workspace-mcp`, a folder of `<email>.json`), Gmail AutoAuth (`@gongrzhe/server-gmail-autoauth-mcp`),
  Google Calendar MCP (`@cocal/google-calendar-mcp`) and Google Drive MCP
  (`@piotr-agier/google-drive-mcp`); any other server works with `--login PATH`. The login path comes
  from the server's own definition (`env`) in the five agents' configs, else the server's default.
  Before an agent starts, every definition of the server (yours and the folder's, in every agent) must
  read that login, and every account in it must be the project's Google account (by the email it names:
  the file's name, `cached_email`, `email` or an `id_token`). Google must name that account too
  (tokeninfo, Gmail's profile or the primary calendar), asked with the saved token while it is valid,
  else with one renewed the way the server does: the login's refresh token and the server's own OAuth
  client (from the Workspace server's login, `GMAIL_OAUTH_PATH` or `~/.gmail-mcp/gcp-oauth.keys.json`,
  `GOOGLE_OAUTH_CREDENTIALS`, `GOOGLE_DRIVE_OAUTH_CREDENTIALS` or the Drive server's config folder) go
  to Google's token endpoint, pinned, and the fresh token answers once and is dropped; nothing is
  written to the login. This is the one place Keyfit uses a refresh token, so Gmail AutoAuth, whose
  login names no account, is confirmed at every check, hours after its sign-in; a signed-out or revoked
  sign-in fails with the server's own sign-in as the fix. Without a refresh token or OAuth client the
  local check decides, and a login that names no account is a warning, `account not confirmed`, at
  every agent start; a server not signed in yet is a warning too. Another account blocks the start,
  with a fix that gives the server a login of its own (Keyfit never removes the other one). The servers
  join the project's MCP ownership: every other project's sessions don't see them, in all five agents,
  and a project with Google Workspace servers doesn't see any other Gmail, Drive or Calendar server
  (Google's own remote servers included; `keyfit add mcp` still gives one to a project). Sessions start
  without inherited `GMAIL_CREDENTIALS_PATH`, `WORKSPACE_MCP_CREDENTIALS_DIR`,
  `GOOGLE_MCP_CREDENTIALS_DIR`, `GOOGLE_CALENDAR_MCP_TOKEN_PATH`, `GOOGLE_DRIVE_MCP_TOKEN_PATH` and
  `USER_GOOGLE_EMAIL`, so each server reads the login Keyfit checked. `keyfit init` offers the servers
  signed in as the project's account; `doctor` shows each server, its login and the account it names.
  claude.ai's own Gmail, Google Drive and Google Calendar connectors belong to the Claude account and
  stay outside Keyfit's reach (documented).
- **Gemini CLI, the fifth agent (thirteenth tool), on the project's Google account.** `gemini` starts with
  the project's accounts, checked, like the other agents, and signed in as the project's Google account
  (the work or the personal email of `keyfit add google`). Gemini CLI keeps its settings, sessions and
  Google sign-in in `~/.gemini`, and `GEMINI_CLI_HOME` moves that folder, so a Gemini CLI account is a
  folder: `keyfit add gemini` takes your own `~/.gemini` when it is signed in as the project's account,
  and `--new` makes one (`~/.config/keyfit/gemini/NAME`, with your `GEMINI.md`, commands, skills,
  agents, policies and extensions linked in) that every project with the same Google account shares;
  `--key` uses a Gemini API key instead (checked with the Gemini API, kept in the Keychain or where
  `--key env:VAR` points, with a folder of its own set to the API-key method), and `--cloud-project`
  hands a Workspace account its Google Cloud project. Every session of the project gets
  `GEMINI_CLI_HOME` (and a key account's `GEMINI_API_KEY`); `gemini` itself starts without inherited
  `GEMINI_API_KEY`, `GOOGLE_API_KEY`, `GOOGLE_CLOUD_PROJECT`, `GOOGLE_APPLICATION_CREDENTIALS`,
  `GOOGLE_GENAI_USE_VERTEXAI` and the other variables that would pick another key, sign-in method,
  endpoint or credential store (`GEMINI_FORCE_ENCRYPTED_FILE_STORAGE` would move the sign-in into one
  Keychain item every folder shares). Before a start, the folder's sign-in method must be Google's,
  and the email Google wrote into its sign-in (`oauth_creds.json`) must be the project's Google
  account; while the sign-in's token is still valid, Google's tokeninfo must name that account too (an
  expired token isn't renewed: that takes Gemini CLI's own OAuth client). Gemini CLI has no sign-in
  command, so `Sign in now?` runs `gemini --prompt-interactive /quit` in the project's folder: Gemini
  CLI's own sign-in dialog, then it quits, and Keyfit checks the account before the session starts. A
  wrong account names the sign-in file to remove in a folder Keyfit made, and a folder of the
  project's own (`--new`) for your own `~/.gemini`. Sessions start with `--allowed-mcp-server-names`
  naming every server they keep, when the project must not see one (from your settings, the folder's
  `.gemini/settings.json`, the system's and your extensions, with your own `mcp.allowed` and
  `mcp.excluded` carried over); an allowlist of your own that names another project's server is
  refused, and Gemini CLI's own commands (`mcp`, `extensions`, …) run as typed. `keyfit init` offers it
  after Firebase; `doctor` shows the folder and the sign-in (a key only by its name). Verified with
  Gemini CLI 0.64.0.
- **Firebase, the twelfth tool.** `firebase` and every agent session are checked against the
  project's Google account (the one `gcloud` uses too) and its Firebase project (`keyfit add
  firebase`: the project the CLI uses in the folder, `--project-id` for another; `keyfit add google`
  and `keyfit init` now also offer the firebase CLI's sign-ins). The firebase CLI already keeps
  several sign-ins and binds one to each project folder (`firebase login:use`), so Keyfit keeps no
  Google token: it reads the CLI's configstore and `.firebaserc`, and removes `FIREBASE_TOKEN` and
  `GOOGLE_APPLICATION_CREDENTIALS`, which would act instead of the sign-in; `npx firebase-tools` in a
  session uses the folder's binding. Before a start, the CLI must be signed in as the project's
  Google account, its project folder (the nearest with a `firebase.json`) must use that account and
  pick the expected Firebase project (`firebase use`, else `.firebaserc`); then Google's tokeninfo
  must name the account for the access token the CLI keeps for it (the CLI renews an expired one
  first, with `firebase projects:list`), and the Firebase Management API must show the project.
  Outside every Firebase folder the CLI uses its default login: `firebase` then gets `--account`, and
  agents a warning. Inside a project, `firebase` refuses another `--account` or `--project` (aliases
  are resolved, `-jP` read like commander), `--token`, a `--config` folder bound to another account,
  `firebase use` of another project, a `login:use` that would bind a registered project's folder to
  another account (or change the default login), and a `login --reauth` that would replace the
  default login. `login`, `login:add`, `login:use`, `login:list`, `logout` and `use` run without
  checks. `Sign in now?` runs `firebase login:add` (or `login --reauth` for the default login), and
  `Bind this folder now?` runs `firebase login:use EMAIL`. A project without a Firebase account runs
  `firebase` as before. `keyfit init` offers it after Google Cloud; `doctor` shows the sign-in and
  project the CLI uses here. Verified with firebase-tools 15.26.0.
- **Google Cloud, the eleventh tool, and the project's Google account.** A project now says once which
  Google account it uses, the work or the personal email (`keyfit add google --email`, or picked from
  gcloud's sign-ins); every Google tool is checked against it (`gcloud` here; Firebase and Gemini CLI
  above). `gcloud` uses the project's gcloud configuration (`keyfit add gcloud`: the one
  signed in as that account, and its Google Cloud project, `--project-id` for another). gcloud
  already keeps named configurations, so Keyfit keeps no Google token: sessions and `gcloud` get
  `CLOUDSDK_ACTIVE_CONFIG_NAME` (for that process only; the configuration active everywhere else is
  untouched), `CLOUDSDK_CORE_ACCOUNT` and `CLOUDSDK_CORE_PROJECT`, so `bq` and `gsutil` in a session
  use them too, and inherited `CLOUDSDK_…` variables that pick another configuration, account,
  project, credentials or endpoint are removed, with `GOOGLE_APPLICATION_CREDENTIALS`. Before a
  start, the configuration must exist, its account must be the project's Google account and its
  project the expected one, it must take no credentials from a file, and gcloud must hold a sign-in
  for the account (read from the account column of `credentials.db`, never a credential); then the
  token `gcloud auth print-access-token` prints must belong to the account (Google's tokeninfo, the
  token in the request body) and see the project (Cloud Resource Manager). Inside a project, `gcloud`
  refuses another `--configuration`, `--account` or `--project`, `--access-token-file` and
  `--flags-file`; `gcloud auth login`, `auth list`, `config set` and `config configurations create`,
  `list` and `describe` run without checks in the project's configuration, except a sign-in as
  someone else and a `config set`/`unset`, `configurations delete` or `rename` that would move a
  registered project's configuration off its account or project. `Sign in now?` runs
  `gcloud auth login EMAIL`. A project without a Google Cloud account runs `gcloud` as before.
  `keyfit init` offers both after Convex; `doctor` shows the configuration and gcloud's folder.
  Verified with gcloud 580.0.0.
- **`Bind this folder now? [Y/n]`.** When you start a tool yourself in a terminal and a Cloudflare check
  fails only because the project's folder isn't bound to its wrangler profile (a new worktree, say),
  Keyfit offers to run `wrangler auth activate NAME FOLDER` with the project's wrangler, checks again
  and starts the tool, like `Sign in now?` (both are asked, one after the other, when a sign-in is
  needed too). In a Superset worktree it offers the folder that holds the repository's worktrees
  (`~/.superset/worktrees/<repository>`), so later workspaces start without a question, unless a
  worktree of another project is in it. Only the project's own profile, and only for a folder of the
  project: never another project's folder or one outside every project, never the home folder or a
  folder above it. Agents and scripts still get the printed command. `keyfit add cloudflare`'s
  `Run it now?` runs the binding the same way, away from the folder, so a `CLOUDFLARE_API_TOKEN` in
  its `.env` no longer makes wrangler refuse.
- **Copilot CLI, the fourth agent (tenth tool).** `copilot` starts with the project's accounts, checked,
  like `claude`, `codex` and `opencode`, and as the project's GitHub account. Copilot CLI signs in to
  GitHub and takes `COPILOT_GITHUB_TOKEN` before `GH_TOKEN`, `GITHUB_TOKEN`, its own login and gh's, so
  a Copilot account is a GitHub login gh holds: `keyfit add copilot` records the project's GitHub
  account (`--login` for another), and every session of the project gets gh's token for it in
  `COPILOT_GITHUB_TOKEN` (`npx @github/copilot` inside a Claude session too). Copilot's own folder
  (`~/.copilot`, or `COPILOT_HOME`) stays shared. Before a start, gh must hold a token for the login
  that isn't a classic personal access token (`ghp_…`, which Copilot refuses), and `api.github.com/user`
  must return the login, as for GitHub; a token the project's GitHub check already confirmed is reused
  without asking gh again. Inherited Copilot tokens (also the coding agent's `GITHUB_COPILOT_…_TOKEN`),
  `COPILOT_GH_HOST`, `GH_HOST` and `COPILOT_PROVIDER_…` (a model provider of your own instead of the
  account) are removed. Sessions (`copilot`, `-p`, `init`, `workflow`) start with
  `--disable-mcp-server=NAME` for each server another project owns, from `mcp-config.json`,
  `.mcp.json` and `.github/mcp.json` up to the repository's top and `--additional-mcp-config`;
  `--enable-mcp-server` of such a server and `copilot -C DIR` with a folder of another project are
  refused, and so is `copilot login` where the project has a Copilot account (it would change only
  Copilot's own login). The fixes are gh's sign-ins, which Keyfit prints and never runs. `keyfit init`
  offers Copilot CLI right after OpenCode when the project has a GitHub account; `doctor` shows the
  account and Copilot's folder. Verified with Copilot CLI 1.0.97.
- **OpenCode, the third agent (ninth tool).** `opencode` starts with the project's accounts, checked,
  like `claude` and `codex`. OpenCode 2 keeps every sign-in in its SQLite database, which
  `OPENCODE_DB` moves, so an OpenCode account is a database: `keyfit add opencode` records OpenCode's
  own one (`~/.local/share/opencode/opencode.db`) and the providers signed in there; `--new --name
  NAME` makes another under `~/.config/keyfit/opencode`. Before a start, the database must hold a
  sign-in for each recorded provider (read-only, never a credential) and `opencode auth list` must
  see them, with no shell key next to them; no provider is asked (there is no cheap call that
  confirms a sign-in), and the check starts OpenCode with no configuration, so no MCP server starts.
  Sessions (`opencode`, `run`, `mini`), sign-ins (`auth …`), `models`, `stats` and `session …` get
  `--standalone`: OpenCode's background server serves every folder and keeps the environment of the
  start that launched it, so Keyfit never starts it with a project's environment and runs its own
  commands (`mcp`, `serve`, `service`, …) unchanged from your terminal; inside a session, where the
  environment holds a project's accounts, they get `--standalone` or are refused. In a project with an
  OpenCode account, `opencode` starts without inherited `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`,
  `GEMINI_API_KEY`, `GOOGLE_GENERATIVE_AI_API_KEY`, `GOOGLE_API_KEY`, `GITHUB_TOKEN` and
  `OPENCODE_API_KEY`, or any other key or token OpenCode's own report (`opencode auth list`) names
  as coming from your shell (`OPENROUTER_API_KEY`, `GROQ_API_KEY`, `AWS_BEARER_TOKEN_BEDROCK`,
  `CLOUDFLARE_API_TOKEN`, `GITLAB_TOKEN`, …): the check warns "from your shell, not the project's
  account" and keeps their names (never values) with the cached result, so cached starts remove them
  too, and a new key-like variable is checked again. Only names containing `KEY` or `TOKEN` count, so
  the `CLOUDFLARE_ACCOUNT_ID` Keyfit sets stays. Claude, Codex and Copilot CLI sessions keep them all;
  Gemini CLI sessions keep them too, apart from the Google keys its own account removes
  (`GEMINI_API_KEY`, `GOOGLE_API_KEY`). Other projects' MCP servers are
  switched off in `OPENCODE_CONFIG_CONTENT` for the folder OpenCode opens (your own content is kept). `--server`, `acp --login` and `opencode DIR` with a
  folder of another project are refused. `Sign in now?` runs `opencode auth login PROVIDER --standalone`;
  `keyfit init` offers OpenCode's sign-ins; `doctor` shows the project's database. Verified with
  OpenCode 2.0.25.
- **Convex, the eighth tool.** `convex` and `npx convex` use the project's Convex account. The convex
  CLI keeps one login per Mac (`~/.convex/config.json`) and has no account switch, so Keyfit hands each
  session the project's token in `CONVEX_OVERRIDE_ACCESS_TOKEN`, which the CLI takes before its own
  login and before deploy keys. An account is the CLI's own login (`keyfit add convex --cli`, read
  whenever a session starts) or a token Keyfit keeps in the Keychain (`--copy` copies the CLI's current
  login, or paste one; `--token env:VAR`), plus the team. Before a start, Convex must accept the token
  and list the team (`GET api.convex.dev/api/teams`, what `npx convex login status` shows), and the
  deployment the folder points at (`CONVEX_DEPLOYMENT` or a deploy key, from `.env.local` or `.env`)
  must belong to that team. Inherited `CONVEX_DEPLOYMENT`, `CONVEX_DEPLOY_KEY` and
  `CONVEX_DEPLOYMENT_TOKEN` that the folder's files disagree with are removed, and so is
  `CONVEX_PROVISION_HOST`. Inside a project, `convex` refuses `--admin-key`, another `--team`, and
  another team's deployment named with `--deployment`, `--deployment-name` or `--env-file`;
  `convex login` and `logout` are refused where Keyfit keeps the project's token. When the account is
  the CLI's own login and it expired, `Sign in now?` runs `npx convex login --force`. A project without
  a Convex account runs `convex` as before. `keyfit init` offers the CLI's login; `doctor` accepts a
  convex used only through npx.
- **Cloudflare, the seventh tool.** `wrangler` uses the project's Cloudflare account. wrangler keeps
  named sign-ins bound to folders since 4.106 (`wrangler auth create NAME`, `wrangler auth activate
  NAME DIR`), so Keyfit keeps no Cloudflare token: an account is a wrangler profile plus the expected
  account ID (`keyfit add cloudflare` offers the profiles and the accounts the chosen one reaches).
  Before a start, the folder must resolve to the profile (bound there or above, not shadowed by a
  subfolder bound to another profile), the wrangler config wrangler would read must name no other
  `account_id` (`env.*` too), the wrangler `npx` would run must be 4.106 or newer, and `wrangler
  whoami` in the folder must list the account. `wrangler`, `claude` and `codex` start with
  `CLOUDFLARE_ACCOUNT_ID` set to the account and without inherited `CLOUDFLARE_API_TOKEN`,
  `CLOUDFLARE_API_KEY` and `CLOUDFLARE_EMAIL` (they beat every profile), so `npx wrangler` and
  `npm run deploy` inside a session work on the project's account or fail. Inside a project, another
  `--profile`, a `--cwd` or `--config` pointing elsewhere, `wrangler login`/`logout` (they change only
  the default sign-in) and `wrangler auth activate`/`deactivate`/`create`/`delete` that would move a
  registered folder off its project's profile are refused. A project without a Cloudflare account runs
  `wrangler` as before. `Sign in now?` runs `wrangler auth create NAME`; `keyfit init` offers the
  profile bound to the folder; `doctor` shows the profile, its binding and the account.
- **Supabase, the sixth tool.** `supabase` uses the project's Supabase account: a personal access token
  Keyfit keeps in the Keychain (`keyfit add supabase` checks it with Supabase and records its
  organization; `--cli` copies the token of the supabase CLI's current login; `--token env:VAR` names
  one you keep elsewhere). It reaches the CLI as `SUPABASE_ACCESS_TOKEN`, with
  `SUPABASE_PROFILE=supabase`, so `npx supabase` and a Supabase MCP server reading
  `${SUPABASE_ACCESS_TOKEN}` in a Claude or Codex session use it too. Before a start, Supabase must accept
  the token and list the organization, and a linked folder (`supabase/.temp/project-ref`,
  `SUPABASE_PROJECT_ID`) must point at a project of it. Inside a project, `--project-ref`, `link REF`,
  `projects delete REF`, `gen types --project-id` and `--workdir` pointing at another organization's
  project are refused, and so are `--org-id` of another organization and another `--profile`.
  `supabase login` and `logout` are refused there too, since they change only the CLI's own login.
  A project without a Supabase account runs `supabase` as before (it is often used only locally).
  `keyfit init` offers the CLI's login.
- **`Sign in now? [Y/n]`.** When you start a tool yourself in a terminal and every failed check is fixed
  by a plain sign-in (`aws login`, `aws sso login`, `claude auth login`, `codex login`, `vercel login`,
  `wrangler auth create NAME`, `npx convex login --force`),
  Keyfit offers to run it, checks again and starts the tool. Without a terminal (agents, scripts, CI)
  nothing is asked. gh's sign-in is never run for you, since it switches gh's active account.

### Changed

- **Faster starts, about 30–55 ms per launch.** urllib and TLS load only when a check goes online,
  shims run `python3 -I -S`, GitHub's two git questions run side by side, and `gh` inside a session
  Keyfit started keeps the token its project's check confirmed in the last 30 minutes (until gh's
  sign-ins change). Run `keyfit setup` again to rewrite your shims. `scripts/bench_shim.py` measures
  what a shim adds on your Mac.
- **First run reads like a guide.** `keyfit` alone prints its help with a "start here" list; `setup` and
  `init` come first in it, and every command's `--help` says what it does. After `keyfit setup --rc`,
  Keyfit says what to do next; with `--rc FILE` it prints the matching `--remove --rc FILE`.
  Running `keyfit setup` again (after an upgrade) says when your startup file already loads the shims.
  The line it adds ends in `# keyfit: tool shims on PATH` instead of naming four of the tools; the
  line of earlier versions still counts (`setup --rc` rewrites it in place, `setup --remove` and
  `doctor` find it, and a shim whose Keyfit is gone names both).
- **`keyfit init` refuses your home folder** (and any folder above it), and a `--root` that doesn't
  exist: every folder under it would have belonged to one project. Outside git, it says that every
  folder below belongs to the project. It also says when Keyfit isn't active in this terminal yet.
- **`keyfit status`** says which of `aws`, `gh` and `vercel` won't start in the project until their
  account is mapped, and how to register a folder that isn't.
- **Every error names the next command.** A tool that isn't installed shows how to install it; an
  unmapped `gh` shows `keyfit add github --login <login>`; `keyfit add aws` lists the profiles there
  are; a rejected AWS access key points at `aws configure --profile P`, which runs inside the project.
- **A shorter README.** It now covers what Keyfit is, install, a first project, everyday commands
  and limits. Everything about each kind of account (what the tool gets, what is checked and refused,
  how to set it up, its limits) moved to one section per account in `docs/accounts.md`.

### Fixed

- A mistake in `registry.toml` (a typo, `roots` written as a string, a root that is the home folder)
  is reported with its place and the fix instead of a Python traceback, in `keyfit` commands and in
  the shims. A connection that names a missing account, or a misspelled one, fails only its own
  project's check.
- With Python older than 3.11 (macOS's own `python3` is 3.9), Keyfit says which version it needs
  instead of failing on `import tomllib`.
- Shims whose Keyfit installation is gone (uninstalled before `keyfit setup --remove`, or moved) say
  what to do instead of sh's "No such file or directory". The tool still doesn't start.
- `KEYFIT_DRY_RUN=1` started commands Keyfit lets through without checks: `supabase`, `wrangler`,
  `convex`, `gcloud` and `firebase` in a project without an account of their kind
  (`KEYFIT_DRY_RUN=1 wrangler deploy` deployed), OpenCode's own commands (`opencode serve`, …) and
  AWS profiles under `shared_aws_profiles`. They are now printed with the reason, the command and
  the names of the variables that would change, like a checked start. Sign-in commands, other
  commands in a folder that isn't registered, and `KEYFIT_DISABLE=1` still run, as documented.
- `cloudflare` lines in `keyfit status`, `doctor` and failed checks started one column later than
  the others: the kind column is now ten characters wide.
- Guided `keyfit init` on a Mac without Codex said `no clean ChatGPT login in ~/.codex … sign in
  (codex login)`; it now says `codex not found on PATH — skipped`, like the other tools.
- Python 3.14 is tested and listed in the package's classifiers. CI now also runs the tests that
  install a built wheel (they were skipped on every Python), and a release installs its wheel into a
  fresh virtualenv and runs `keyfit --version` and `keyfit setup` before anything is published.

### Security

From the pre-launch security review:

- **A `.git` file or symlink no longer lends a folder another project.** A folder (for example an
  unpacked archive) whose `.git` pointed at a registered repository resolved to that project. A
  worktree now counts only when its repository lists it; a moved worktree needs `git worktree repair`.
- **Unclear command lines always run the checks.** `claude --add-dir auth` or `codex -C login` was
  taken for a sign-in command and started a session without checks.
- **`aws --prof other` is refused** like `--profile other`: the AWS CLI accepts abbreviations.
- **A session whose project is no longer registered** runs no `aws`, `gh` or `vercel` instead of
  running them unchecked.
- **An account kind Keyfit does not know** (a typo such as `githb`) fails its check instead of warning.
- **No key in error output.** A malformed `~/.aws/credentials` line (which can be a secret key) was
  printed in the "internal error" message; internal errors now show the exception type and place
  only. A key typed where a reference or name belongs (`--key lin_api_…`, `secret set lin_api_…`) is
  refused without being repeated, and hidden prompts never fall back to echoing.
- **Keys go only to their own service.** Only values shaped like a Linear key are sent to Linear
  (`--key env:VAR` and `.envrc` imports could name any variable), and Keyfit's HTTPS checks no
  longer follow redirects, which would have carried the key or token to the new address.
- **Owner-only files from the first byte:** the registry, the check cache and a new Claude
  account's `.claude.json`; the cache folder and Claude account folders are 700.
- **Control characters are escaped** in check output, so text from a repository
  (`.vercel/project.json`) cannot drive the terminal.
- **The loop guard counts every launch**, so a wrapper that calls a tool by name stops after a few
  rounds in unregistered folders too.
- **`bin/keyfit` runs Python isolated**, so a repository it runs in cannot replace a module.
- **Codex never sees a server it cannot hide.** Codex splits a `-c` key on every dot and does not
  understand quotes, so a server name with a dot or `=` that had to be hidden either stayed visible
  or broke the start. Such a server now stops `codex` with a message to rename it; names with inner
  spaces are passed bare, which Codex understands.
- **A new Claude login trusts no more than your default one.** `keyfit add claude --new` pre-trusted
  every folder of the project; trust is what lets a repository's own hooks and settings run, so it
  now carries over only folders your default login already trusts.
- **Release workflow:** actions pinned to commit hashes, `build` and `twine` to versions.
- `docs/security.md` now says plainly what Keyfit sees, stores, sends and does not protect against.

From the second pre-launch review:

- **wrangler's other spellings of `--config` are refused too.** wrangler's parser also reads `-c` with
  the value attached (`-c/path/wrangler.toml`, `-c~/…`), in a group of short options (`-ec FILE`,
  `-ec./FILE`), as `-c=FILE` and as `--c FILE`. A config of another account, or in a folder bound to
  another wrangler profile, given that way reached wrangler without the refusal. `wrangler dev` with
  several configs has each one checked.
- **A folder's `.env` no longer decides where a wrangler sign-in goes.** wrangler loads `.env`,
  `.env.local` and `.env.<env>` into its environment, also for Keyfit's own `wrangler whoami`, so a file
  there could set `CLOUDFLARE_API_BASE_URL` (the host every request, with the profile's token, goes to)
  or the sign-in endpoints wrangler renews a sign-in at. wrangler now always runs with
  `CLOUDFLARE_API_BASE_URL` set to Cloudflare's own API (a variable already set beats `.env`), without
  inherited `WRANGLER_AUTH_DOMAIN`, `WRANGLER_AUTH_URL`, `WRANGLER_TOKEN_URL`, `WRANGLER_REVOKE_URL` and
  `WRANGLER_CLIENT_ID`, and a `.env` file that sets any of them fails the check before wrangler runs,
  naming the file and the variable, never the value. wrangler's sign-in commands, a `--cwd` folder and
  `--env-file` files are held to the same. A project that deliberately uses another Cloudflare host
  (staging, a compliance region) isn't supported yet.

From the third pre-launch review:

- **Claude's background sessions no longer run with another project's accounts.** Claude Code runs
  them (`claude --bg`, agent view, `←` and `/background`, `claude respawn`) under one supervisor per
  config folder, which keeps the environment of the start that launched it and gives it to every
  background session, in any folder. A supervisor started in a project kept that project's accounts
  for all of them, and Keyfit's check of the folder never applied. Claude sessions in a project now
  start with background sessions and agent view off (`"disableAgentView": true` in `--settings`,
  `CLAUDE_CODE_DISABLE_AGENT_VIEW=1`), and `claude --bg`, `agents`, `attach`, `respawn` and
  `daemon run` are refused in a project and inside a session. `claude daemon status`, `logs` and
  `stop` run as typed (Keyfit's `--settings` in front of `daemon` kept them from running). A
  supervisor that already runs keeps what it got: stop it once in a terminal outside every project,
  with `claude daemon stop --any`.
- **Codex's shared server no longer gets a project's accounts either.** It keeps the environment of
  the start that launched it, for every folder. `codex resume` and `codex fork` now start with
  `--no-daemon`, like `codex`, and `codex agents`, `queue`, `remote-control` and `app-server`, which
  work on that server, are refused in a project. One that already runs: stop it with
  `codex app-server daemon stop`, outside every project. Inside a session, in a folder outside every
  project, Codex is held to the same: `codex`, `resume` and `fork` start with `--no-daemon`, and the
  server's commands are refused (they ran unchanged with the session's accounts).
- **Two clients' setups are no longer merged silently.** `keyfit init` in a second folder with a
  registered project's name (`~/work/globex/api` after `~/work/acme/api`) added it to that project:
  it ran with acme's accounts, and its own `.envrc` was never imported. `init` now stops and asks for
  a name of its own (`--name globex-api`); naming the existing project (`--name api`) still adds the
  folder, and says so. `keyfit add codex`, `claude`, `vercel` and `opencode` without `--name` gave a
  second login the first one's default name (two ChatGPT pro logins `pro`, two personal Claude logins
  `personal`, two Vercel logins in one team), which replaced the first project's account with the
  second's; the second login now gets the next free name (`pro-2`). A `--name` shared on purpose but
  given another login (also for `github` and `copilot`) changes it for every project using it, as
  before, and now names them; `github` no longer keeps the old login's email and ssh host then.
- **`--name` must be a name.** `keyfit add claude`, `opencode` and `vercel --new --name ../../x`
  created folders outside `~/.config/keyfit`; `--name` of every `keyfit add` is now letters, digits,
  `.`, `_` and `-`.
- **Supabase: an option before the command no longer hides it.** `supabase --agent no projects delete
  <ref>` and `supabase --agent yes gen types --project-id <ref>` ran on a project of another
  organization: Keyfit took `--agent`'s value for the command. `--agent` and `--output-format` now
  count as options with a value, and every word shaped like a project ref is checked wherever it
  stands (`gen … --project-id` too), so an option Keyfit doesn't know can't hide one either.
- **Supabase: the TypeScript CLI's other links are checked.** Its `branches` commands act on the
  parent project in `supabase/.temp/linked-project.json`, and its stack mode links through
  `supabase/.link.json`; a project of another organization named there now fails the check, like one
  in `supabase/.temp/project-ref`.
- **Vercel: a folder to deploy is checked like `--cwd`.** `vercel deploy <path>`, `vercel <path>` and
  `vercel dev <dir>` use that folder's link and take the team from it, whatever `--scope` says. A
  folder linked to another team is now refused.
- **Vercel: `.vercel/repo.json` in its current format.** `vercel link --repo` records a team per
  project, and Keyfit read only the older top-level `orgId`, so a repository linked to another team
  that way passed the check. It now takes the project whose directory holds the folder (else every
  project), like the CLI.
- **Convex reads `.env.local`, `.env` and `--env-file` files the way the convex CLI does.** The CLI
  parses them with dotenv, which skips a byte-order mark, reads invalid UTF-8 as replacement characters,
  accepts `KEY: value` lines and lets a quoted value span several lines. Keyfit read them line by line,
  so a file written any of these ways could point the folder at another team's deployment while the
  check said "login and team confirmed". Keyfit now parses them with dotenv's own pattern. A file that
  decides the deployment but can't be read fails the check (`.env.local cannot be read, …`), and an
  `--env-file` Keyfit can't read is refused.
- **A deploy key in `CONVEX_DEPLOYMENT` is never sent or printed.** A key put there (`prod:NAME|…`) went
  to Convex in the deployment lookup's address and appeared in the failure line and in `keyfit add
  convex` errors. Only a deployment name (like `happy-animal-123`) is now sent or printed: such a
  `CONVEX_DEPLOYMENT` fails the check before the network, and `--team`, `--deployment` and
  `--deployment-name` values holding a key, or a `--deployment-name` that isn't a deployment name, are
  refused without being repeated.
- **Inherited `CONVEX_SELF_HOSTED_URL` and `CONVEX_SELF_HOSTED_ADMIN_KEY` are removed** in a project with
  a Convex account, unless the folder's own files set the same. The CLI takes them before
  `CONVEX_DEPLOYMENT`, so another project's self-hosted deployment hid the folder's own, unchecked.
- **gcloud no longer acts as another identity in a project.** `--impersonate-service-account` is
  refused, and a configuration that sets `auth/impersonate_service_account`, `auth/token_host`,
  `auth/mtls_token_host`, `auth/disable_ssl_validation` or an `api_endpoint_overrides/…` fails its
  check, with `gcloud config unset … --configuration NAME` as the fix (it now runs while checks fail).
  Before, an impersonating configuration was reported as "me@acme.io as deployer@… · account and project
  confirmed". `gcloud config set auth/…` and `api_endpoint_overrides/…` on a configuration a project
  uses are refused.
- **gcloud's and firebase's other spellings are read like the CLIs read them.** A leading release track
  (`gcloud beta config configurations delete globex`, `gcloud alpha config set account other@x.com`)
  and arguments after `--` (`gcloud auth login -- other@x.com`, `firebase use -- other-project`,
  `firebase login:use -- other@x.com`) slipped past the refusals, and so did `gcloud auth login
  --remote-bootstrap URL other@x.com`. `gcloud auth login` without the project's account and `gcloud
  auth activate-service-account` as anyone else are refused in a project with a Google Cloud account:
  they write whichever account signs in into the project's configuration.
- **firebase is checked on the project its CLI really picks.** firebase.json's deprecated `"firebase"`
  key, which the CLI takes before `.firebaserc`, was ignored; and without a `firebase.json` (a
  monorepo's top) the `.firebaserc` there was not checked, though `firestore:delete`, `database:get`
  or `auth:export` act on its default.
- **`keyfit add gcloud` never takes another account's configuration.** It took the only configuration
  whatever its account (the default one, active everywhere else) and printed `gcloud config set
  account … --configuration default`, which would move every gcloud outside projects to the project's
  account. It now names `gcloud config configurations create … --no-activate`, as `keyfit init` does.
- **firebase's sign-in can't be sent elsewhere.** Inherited `FIREBASE_TOKEN_URL` and
  `FIREBASE_GOOGLE_URL` (where the firebase CLI posts the refresh token), the CLI's other
  `FIREBASE_…_URL` endpoints and `FIREBASE_CLIENT_ID`/`_SECRET` are removed, also for Keyfit's own
  renewal with `firebase projects:list`.
- **A git that can't answer no longer makes a project folder look unregistered.** When git failed
  (Command Line Tools broken by a macOS update, Xcode's licence not accepted, git missing or slow,
  `dubious ownership`, a `.git` file naming a folder that is gone), a registered folder counted as
  one outside every project: tools started with your shell's credentials, without checks and with
  every MCP server, and `keyfit doctor` said `All good`. Inside a repository (a `.git` there or
  above), tools now don't start and show git's message; folders without one run as before.
  `keyfit doctor` has a `git` line. The lookup also ignores the `GIT_DIR`, `GIT_WORK_TREE`,
  `GIT_COMMON_DIR`, `GIT_INDEX_FILE` and `GIT_CEILING_DIRECTORIES` a git hook exports: a tool a hook
  ran in a worktree's subfolder resolved to no project.
- **Submodules and nested repositories in a worktree belong to its project.** In a Superset worktree,
  git's top folder is the submodule's and no registered folder sits above it. When a repository isn't
  registered, Keyfit now resolves the checkout around it before trying registered parent folders.
- **A failed check no longer leaves its cached success behind.** After `keyfit status` showed ✘ (a
  profile that now signs in to another account, say), launches kept starting the tool on the success
  cached up to 30 minutes before. Any other answer now removes it, and only successes are cached
  (with the names of the shell keys OpenCode reported); warnings such as `account not confirmed`
  were cached for 30 minutes too.
- **A success trusted while a service can't be reached is shown for every tool.** `aws`, `gh`,
  `vercel`, `supabase`, `wrangler`, `convex`, `gcloud` and `firebase` started on one up to 24 hours
  old without the `could not re-check now` warning.
- **No `Sign in now?` inside a session Keyfit started** (`KEYFIT_PROJECT` set): agents run commands
  in a terminal of their own, where an `aws` waited for an answer.
- **MCP servers acting on another workspace are hidden wherever they are defined.** A Linear server was
  recognised only by the exact address `mcp.linear.app` and the first `${VAR}` of its headers, so a
  server with another workspace's key in its `env` (Claude's form and Codex's `env = {…}`),
  `npx mcp-remote https://mcp.linear.app/mcp --header "Authorization: Bearer ${LINEAR_GLOBEX_API_KEY}"`
  and `https://MCP.LINEAR.APP/mcp` or `https://mcp.linear.app:443/mcp` stayed visible in every project.
  A Linear server is now one with `mcp.linear.app` in its address or command, or a `LINEAR_…` variable
  or a Linear key in its `env`; it stays visible only when every variable it names holds the project's
  key and a key written into it is the project's own. One signed in inside the agent (OAuth) is hidden
  unless a project claims it: `keyfit add mcp NAME` now works for Linear servers too. Keyfit now also
  reads Claude's local scope (`claude mcp add` without `--scope`: the folder's entry in `~/.claude.json`,
  also for the repository's top and a worktree's main checkout), Codex's profile file
  (`codex --profile NAME`: `NAME.config.toml` next to `config.toml`) and the `.codex/config.toml` of the
  folders Codex trusts, so servers defined there are hidden, and a Google Workspace server defined
  there is checked. A `.mcp.json`, `opencode.json`, Copilot CLI `mcp-config.json` or Gemini CLI
  `settings.json` with a byte-order mark or a leading `//` comment counted as empty; it is read now,
  and a config file an agent loads that still can't be parsed stops the agent with a message instead
  of starting it with servers Keyfit couldn't see.
- **wrangler's config is found the way wrangler finds it.** Given an entry file (`wrangler deploy
  ../other/src/index.ts`, also `versions upload`, `dev`, `preview` and `--script`), wrangler reads the
  config of that file's folder, whose `account_id` beats the account Keyfit pins; such a config of
  another account is now refused like `--config`. The config a build generated, which
  `.wrangler/deploy/config.json` sends `wrangler deploy` to (the Vite plugin's
  `dist/<name>/wrangler.json`), is checked too, so a stale one no longer keeps an old account.
- **A wrangler config Keyfit cannot read fails the check** instead of being skipped. A byte order mark
  and bytes that aren't UTF-8 are read as wrangler reads them (a `wrangler.toml` of another account
  with a byte order mark passed), so is a `//` comment ended by a lone CR, and what Keyfit still cannot
  parse (TOML 1.1 syntax) is refused with a message that says so.
- **`--cwd` follows symlinks, as wrangler does,** so a link into another project's folder is refused,
  and a refusal about another project's folder no longer suggests binding it.
- **wrangler's binding commands are checked everywhere.** `wrangler auth activate`, `deactivate` and
  `delete` typed outside every project ran unchecked, and so did binding a folder that holds projects'
  folders (`..`, `~/code`, `~/.superset/worktrees/<repository>`) to another profile, or unbinding it;
  they are now refused when they would move a project's folder (a root or a worktree) off its
  profile. `keyfit add cloudflare`'s `Run it now?` binds the project's own folder when the
  repository holds more than the project (`~/mono/apps/web` in `~/mono`), under the same rules as
  `Bind this folder now?`.
- **wrangler's options are read as wrangler reads them:** `--env NAME` loads `.env.NAME` wherever that
  path leads (`--env /../settings.txt` loads `settings.txt`), and `--config -0x.toml` and `--config -`
  name files.
- **Both wranglers must be new enough for profiles:** the one `npx wrangler` runs (also pnpm's script
  in `node_modules/.bin`, whose version went unread) and the one `wrangler` runs from PATH. A version
  Keyfit cannot tell is a warning instead of passing silently.
- **The online Cloudflare check gets none of the project's other tokens.** At an agent start it ran the
  repository's own `node_modules/.bin/wrangler` with every account of the project in its environment
  (a Convex or GitHub token, say); it now runs with your shell's environment and the Cloudflare
  account only.
- **A folder's `.env` cannot move wrangler's profiles.** `XDG_CONFIG_HOME` there would have wrangler
  read and write its profiles and bindings where Keyfit doesn't look (and `Sign in now?` sign in
  there); it now fails the check like the sign-in endpoints.
- **Sign-in commands are only the sign-ins, so they no longer print another account's token.** Whole
  command groups ran without checks or refusals. `gh auth token` in a project printed the token of
  gh's active account, not the project's: only `gh auth login`, `logout`, `refresh`, `switch` and
  `setup-git` are sign-ins now, `gh auth token`, `status` and `git-credential` are checked and answer
  with the project's `GH_TOKEN`, and `--user` another account, `--secure-storage` without `--user` and
  `--show-token` without `--active` are refused. In a project without a GitHub account, `gh auth
  status` still runs unchecked, to list the logins to map. `aws configure`, `sso login` and `login`
  took any `--profile`, so `aws configure export-credentials --profile other` printed that profile's
  keys: another profile is refused there too (shared profiles still run). `vercel teams invite`,
  `members`, `add`, `request` and `sso` ran unchecked on the team selected in the login folder: only
  `teams switch` and `teams ls` are sign-ins now, and the rest are checked and get the project's team.
- **Gmail, Drive and Calendar servers no longer run as another Google account while Keyfit says
  "confirmed by Google".** Google Drive MCP signs in with a service account (`GOOGLE_APPLICATION_CREDENTIALS`,
  `GOOGLE_DRIVE_MCP_SUBJECT`) or a token of its own (`GOOGLE_DRIVE_MCP_ACCESS_TOKEN`, `_REFRESH_TOKEN`,
  `_CLIENT_ID`, `_CLIENT_SECRET`) ahead of its login, and moves an old token file (`GOOGLE_TOKEN_PATH`) into
  a missing one; Google Calendar MCP takes a service account (`GOOGLE_SERVICE_ACCOUNT_KEY`, `_SUBJECT`, and
  `GOOGLE_APPLICATION_CREDENTIALS` without a client file), Google Workspace MCP one that acts as any user of
  its domain (`GOOGLE_SERVICE_ACCOUNT_KEY_FILE`, `_JSON`). A shell exporting `GOOGLE_APPLICATION_CREDENTIALS`
  for Terraform made the Drive server ignore the login Keyfit checked. A project with such a server now
  starts its sessions without these variables, a definition that sets one fails the check (`remove … from
  gdrive's env in Claude's MCP config`), and Google Drive MCP not signed in yet blocks when the folder
  holds `google-tokens.json` or `.gcp-saved-tokens.json`, which the server would move into its login. A
  default login (and OAuth client) is looked for under the `HOME` and `XDG_CONFIG_HOME` the server's
  definition sets, as the server does. The OAuth client Keyfit renews a token with now comes from your own
  configs only (Claude's local scope included), never from a definition the folder brings (`.mcp.json`,
  `opencode.json`, `.github/mcp.json`, `.gemini/settings.json`, Codex's project config): a repository's
  client turned "the token belongs to someone@gmail.com" into an `account not confirmed` warning. It is
  read only from a regular file of at most 64 KB: a FIFO or `/dev/zero` hung the check.
- **An agent pointed at another project's folder is refused, also after options.**
  `opencode -m anthropic/claude-sonnet-4-5 ~/code/globex` opened globex with this folder's accounts:
  Keyfit stopped looking for the folder at the first word that wasn't one, and hid the MCP servers of
  this folder, not globex's. OpenCode's options are now read from its CLI reference, `opencode run
  --dir DIR` and `opencode acp --cwd DIR` are checked too, and a folder after an option Keyfit doesn't
  know is refused, since it may be that option's value. `codex -C DIR` (`--cd`) had no folder check
  at all; it now has the one `copilot -C` has.
- **A later word no longer turns a session into the agent's own command.** Keyfit took the first known
  word anywhere on the line: `gemini summarize the mcp setup`, `gemini why do my hooks fail` and
  `claude --add-dir ../lib agents` started sessions without `--allowed-mcp-server-names` or
  `--settings`, so every MCP server was visible (`codex`, `copilot` and `opencode` alike). Only the
  first command word, after the agent's own options, counts now; a line Keyfit can't read for sure
  starts as a session, with the checks and the hiding.
- **Each agent's options are read as that agent reads them.** One list of options with a value served
  all thirteen tools, and in it `-c` took one, though for Claude and OpenCode it is `--continue`:
  `claude -c fix auth` and `opencode -c . auth` were taken for sign-in commands and started without
  checks, refusals or hidden servers (OpenCode also without `--standalone`).
- **Your own options can't bring hidden MCP servers back.** A `--settings` of yours after Keyfit's
  replaced it (Claude keeps the last one), and Codex's `-c mcp_servers.NAME.enabled=true` after
  Keyfit's `enabled=false` turned the server back on. Keyfit now merges its keys into your last
  `--settings` (a file or JSON) and passes one: the hidden servers added to your `deniedMcpServers`
  and `disabledMcpjsonServers`, `disableAgentView` on, its status line only where you set none. A
  `--settings` file that sets `env` is refused rather than put on Claude's command line, where every
  process can read it. Servers of `claude --mcp-config` files and JSON are hidden like those of
  `~/.claude.json`. A Codex `-c` that turns a hidden server on, or sets its table or `mcp_servers`
  anew, is refused.

## [0.1.0] - 2026-10-12

First public version. macOS only; Python 3.11 or newer, no other dependencies.

### Added

- **Shims for `claude`, `codex`, `aws`, `gh` and `vercel`.** `keyfit setup` writes them and a small shell
  file that puts them on PATH in new terminals (right after Superset's agent wrapper when it is
  there, so its hooks keep working). You keep typing the commands as usual.
- **zsh and bash.** `keyfit setup` writes a PATH file for each; `keyfit setup --rc` adds the
  line to `~/.zshrc`, or for bash to the login profile (and `~/.bashrc` when the profile sources
  it). `--shell zsh|bash` overrides `$SHELL`, `--rc PATH` picks another file, `--zshrc PATH`
  still works. On other systems than macOS, `keyfit setup` says so and stops.
- **Install with pip, pipx or Homebrew.** Keyfit is a self-contained package (`pip install
  keyfit`, a Homebrew formula for a tap); the shims and the Claude status line run the
  installed package with the Python that ran `keyfit setup`. `keyfit --version` prints the
  version.
- **Guided first run.** In a terminal, `keyfit init` registers the repository, then offers what
  it finds on this Mac one at a time: an AWS profile from `~/.aws/config` (or `AWS_CONFIG_FILE`),
  a GitHub account `gh` is signed in to, the current Claude, Codex and Vercel logins, and a Linear key
  (hidden input, stored in the Keychain). Enter skips; anything already set is left alone; it
  ends with `keyfit status`. `--yes`, or no terminal, registers without asking.
- **`keyfit setup --remove`** undoes setup: it deletes the shims and both PATH files and takes
  Keyfit's line out of `~/.zshrc`, `~/.bash_profile`, `~/.bash_login`, `~/.profile` and
  `~/.bashrc` (or only `--rc PATH`), leaving every other line as it was. The registry stays;
  `--purge` also deletes `~/.config/keyfit`, with the Claude and Vercel login folders Keyfit made, after
  asking (`--yes` to skip the question).
  Keychain items are never deleted.
- **`keyfit doctor`** checks the installation with a ✔/✘ line each and the fix under every ✘:
  macOS, Python, the shims, their place on PATH (before every real tool, right after Superset's
  wrapper), the real `claude`, `codex`, `aws`, `gh` and `vercel`, the registry, the Keychain and the
  current folder's project. It never reads a secret.
- **Projects recognised by folder.** `keyfit init` registers a repository. Every subfolder and
  every git worktree of it, wherever it lives on disk (Superset's included), belongs to that
  project, and so do worktrees of repositories nested inside a project folder. `keyfit which`
  tells you which project a folder belongs to and why; `keyfit projects` lists them all. In a
  folder that is not registered, every tool runs exactly as before.
- **Verified accounts per project.** Before a tool starts, Keyfit hands it only the project's
  accounts, removes conflicting credentials inherited from your shell, and checks each account.
  When one is wrong or expired, the tool does not start and you see the command that fixes it.
  - AWS: a named profile and the account it must resolve to (`keyfit add aws`, or imported
    from a `use aws` line in `.envrc` on `keyfit init`); a profile found through `AWS_CONFIG_FILE`
    keeps that file. `aws --profile <another project's profile>` is refused inside a project.
  - Linear: one API key per project, read from an environment variable or the macOS Keychain,
    checked against the expected workspace (`keyfit add linear`).
  - GitHub: the project's account from `gh`'s stored logins, checked against the expected login;
    a different git author email or SSH host shows as a warning (`keyfit add github`).
  - Claude Code: the project's login, checked to be a claude.ai subscription in the expected
    organisation, optionally the exact login (`keyfit add claude`, `--new` for another account).
  - Codex: the project's ChatGPT login, optionally pinned to one account (`keyfit add codex`).
  - Vercel: the project's login (the CLI's global config folder) and team, checked with Vercel's API
    against the expected username and team membership (`keyfit add vercel`, `--new` for another
    login). `vercel` gets the folder (`--global-config`) and the team (`--scope`); inside a project,
    `--scope` or `--global-config` of another team or folder and `--token` are refused, and so is a
    repository linked (`.vercel/project.json`, `.vercel/repo.json`) to another team. Inherited
    `VERCEL_ORG_ID`, `VERCEL_PROJECT_ID`, `VERCEL_TEAM_ID` and `VERCEL_TOKEN` are removed.
- **Sign-in always works.** `aws sso login`, `claude auth login`, `codex login`, `gh auth …` and
  `vercel login` (also `logout`, `switch`, `whoami`, `teams`) run even when a check fails, with the
  project's environment (and for `vercel`, the project's login folder), so they fix the right account.
- **MCP servers per project.** In a project, Claude and Codex see only that project's Linear
  server and never a server another project owns. Your servers keep their names.
- **`keyfit add mcp NAME…`** marks MCP servers (names or patterns such as `'globex_cloud_*'`) as
  belonging to the current project; every other project stops seeing them.
- **Status line.** Claude Code sessions started through Keyfit show the project and its
  accounts, unless you configured a status line of your own.
- **`keyfit status`** checks every connection of the current project (`--offline` for local
  checks only).
- **Fast, forgiving checks.** Successful remote checks are cached for 30 minutes. A network
  blip is retried once, then a success from the last 24 hours is accepted with a warning; a
  wrong account never is.
- **Switches.** `KEYFIT_DRY_RUN=1 <tool>` shows what would run and which environment variables
  would change (names only, never values). `KEYFIT_DISABLE=1 <tool>` skips Keyfit once.
  Removing the shell line turns Keyfit off.
- **Keys stored for you.** `keyfit add linear` without `--key` asks for the key with hidden
  input (or reads one line from stdin in scripts), checks it with Linear, then stores it in the
  macOS Keychain. `keyfit secret set NAME` stores any key the same way and prints its
  `keychain:keyfit/NAME` reference; `keyfit secret list` shows the names. Values never appear
  in output or process arguments.
- The registry (`~/.config/keyfit/registry.toml`) stores where secrets live, never their values.
- **Documentation.** A README for first-time users, `docs/security.md` (everything Keyfit
  reads, stores and sends, and its limits) and `docs/troubleshooting.md`.

### Removed

- WorkOS and Bitwarden Secrets Manager support from the private prototype are not part of the
  public version.
