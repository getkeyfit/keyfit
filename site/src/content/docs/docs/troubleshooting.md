---
title: Troubleshooting
description: When a tool doesn't go through Keyfit, or a check fails.
sidebar:
  order: 6
---

<!-- Generated from docs/troubleshooting.md by site/scripts/sync_docs.py. Edit the source, not this file. -->

Start with these three commands, run in the folder where something went wrong:

```bash
keyfit doctor      # is Keyfit installed, and does it come first on PATH? The fix is under every ✘
keyfit which -v    # which project this folder belongs to, why, and its accounts
keyfit status      # check every account of the project now (always online, no cached results)
```

None of them prints a secret value.

## The tool doesn't go through Keyfit

Signs that the tool started without Keyfit:

- Claude Code shows no Keyfit status line (unless you set a status line of your own).
- A tool used the wrong account.
- `KEYFIT_DRY_RUN=1 claude` in a registered project started Claude instead of printing `would run: …`.

1. **Run `keyfit doctor` and read the `shims` and `PATH` lines.**
   - `✘ PATH … not on PATH`: this terminal was opened before setup. Open a new one. If doctor says no startup file loads Keyfit, run `keyfit setup --rc` first.
   - `✘ PATH … <folder> comes first: …` or `✘ PATH … comes before Superset's agent wrapper`: something put another folder ahead of the shims after Keyfit's line ran. Fix it in three steps:
     1. Make Keyfit's line (it ends in `# keyfit: tool shims on PATH`, or `# keyfit: claude/codex/aws/gh shims on PATH` from an earlier version) the last line of the startup file doctor names.
     2. Run `keyfit setup` again. This rewrites the PATH files in `~/.config/keyfit`, and earlier versions placed the shims differently.
     3. Open a new terminal.
   - `✘ shims … runs <path>, which no longer exists`: the Python or the checkout that ran `keyfit setup` has moved, for example after a Python upgrade. Run `keyfit setup` again.
2. **Run `type -a claude`.** The first line should be `~/.config/keyfit/bin/claude`. In Superset terminals, it should be `~/.superset/bin/claude` with the Keyfit shim right after it (see [below](#superset-terminals)). If it says `claude is an alias for …` or `claude is a shell function`, that alias or function runs before PATH is searched. Remove it from your startup file; some installers add one.
3. **The shell may remember an old location.** Run `hash -r` (bash) or `rehash` (zsh), or open a new terminal.
4. **Check which file got the line.** `keyfit setup --rc` prints the file it changed.
   - zsh uses `$ZDOTDIR/.zshrc` when `ZDOTDIR` is set. If your terminal app sets `ZDOTDIR`, that may not be the file you expect. Pass the file yourself: `keyfit setup --rc ~/.zshrc`.
   - bash uses the login profile macOS terminals read. It also uses `~/.bashrc`, but only when that profile sources it; a `bash` started inside a terminal reads only `~/.bashrc`.
5. **The folder may not be registered.** If `keyfit which` says `not registered`, run `keyfit init`. In folders that aren't registered, tools run unchanged on purpose.
   A worktree counts for its repository's project only when the repository lists it (`git worktree list`). If you moved a worktree's folder by hand, run `git worktree repair` inside it.
6. **The tool may not be found through PATH.** IDE extensions, desktop apps and tools started by their full path bypass the shims. For any other command, use `keyfit run -- <command>`.

## Superset terminals

In a Superset terminal the chain is **Superset's agent wrapper → Keyfit → the real tool**.

- **Order on PATH.** When `~/.superset/bin` is on PATH, Keyfit's line moves it to the very front and puts the shims right behind it, ahead of everything else. That includes folders a login profile adds after Superset set PATH, such as `eval "$(brew shellenv)"` in `~/.zprofile`.
- **No loops.** Superset's wrapper finds the next `claude` on PATH, which is Keyfit's shim. When Keyfit looks for the real tool, it skips its own shims and Superset's wrapper folders (`~/.superset/bin`, `~/.superset-*/bin`).
- **Hooks.** Superset's wrapper still runs first, so its hooks keep working.

To check, run this in a Superset terminal:

```
$ type -a codex
codex is /Users/you/.superset/bin/codex
codex is /Users/you/.config/keyfit/bin/codex
codex is /opt/homebrew/bin/codex
$ keyfit doctor
  …
  ✔ PATH       ~/.config/keyfit/bin   right after Superset's agent wrapper, before every real tool
```

If the `PATH` line fails:

1. Open a new Superset terminal.
2. If it still fails, run `keyfit setup` again. The PATH files of earlier versions put the shims only right after the wrapper, which left them behind folders a login profile had put in front.
3. Make sure Keyfit's line is the last line of your startup file.

Superset workspaces are git worktrees of your repository, so they resolve to the same project wherever Superset keeps them. Run `keyfit which` in a workspace terminal to confirm. It shows `git common-dir of <workspace> → <your main checkout>`.

## A check fails

The tool doesn't start, and every failed check is shown with its fix:

```
keyfit: claude not started, acme-api needs attention
  ✘ aws        acme-dev → …4321   SSO session expired
    → aws sso login --sso-session acme
  (bypass once: KEYFIT_DISABLE=1 claude …)
```

When you started the tool yourself in a terminal and the fix is only a sign-in (`aws login`, `aws sso login`, `claude auth login`, `codex login`, `opencode auth login … --standalone`, `vercel login`, `wrangler auth create NAME`, `wrangler login`, `npx convex login --force`, `gcloud auth login EMAIL`, `gemini --prompt-interactive /quit`, `firebase login`, `firebase login:add`, `firebase login --reauth`), Keyfit asks `Sign in now? [Y/n]`: press Enter, sign in, and the tool starts once the checks pass. When the fix is `wrangler auth activate NAME FOLDER` for a folder of the project, or `firebase login:use EMAIL` for the folder you are in, it asks `Bind this folder now? [Y/n]` the same way (both questions, one after the other, when both are needed). Agents and scripts are never asked. Otherwise, run the command after `→`. Sign-in commands always go through, even while checks fail, and they run with the project's environment. That way they sign in the account the project needs. Then start the tool again; failed checks are never cached.

| Message | What to do |
|---|---|
| **AWS** `not signed in to SSO`, `SSO session expired` | `aws sso login --sso-session NAME` (or `--profile PROFILE`), as printed |
| **AWS** `not signed in (aws login)`, `console sign-in expired (12 h limit)` | `aws login --profile PROFILE`. A console sign-in lasts at most 12 hours |
| **AWS** `profile 'P' is signed in to account …1234, project expects …5678` | the profile belongs to another account. Map the right one: `keyfit add aws --profile <the right profile>` |
| **AWS** `no profile 'P' in ~/.aws/config` | create it (`aws configure sso --profile P`), or map an existing profile |
| **AWS** `InvalidClientTokenId`, `SignatureDoesNotMatch` (a profile with an access key) | AWS rejected the key. `aws configure --profile P` and enter a working one; it runs inside the project although the check fails |
| **AWS** `aws CLI not found on PATH` | install it: `brew install awscli` |
| **AWS** `AWS did not answer within 40 s (a Keychain prompt may be waiting — choose Always Allow)` | a `credential_process` profile (Granted, for example) is waiting for a Keychain prompt. Answer it, then try again |
| **Linear** `key not found ($LINEAR_ACME_API_KEY)` | `env:` keys are read from the environment you start the tool in, and that variable isn't set there. Export it, or run `keyfit add linear` to keep the key in the Keychain instead |
| **Linear** `Linear rejected the key (revoked, expired or wrong)` | create a new personal API key in Linear (Settings → Security & access), then run `keyfit add linear` |
| **Linear** `the key belongs to workspace 'x', project expects 'y'` | paste the key for the expected workspace: `keyfit add linear --workspace y` |
| **GitHub** `gh has no stored login for octocat`, `the stored token for octocat was rejected` | `gh auth login --hostname github.com --git-protocol ssh --web` as octocat, then `gh auth switch --user <your usual account>` |
| **GitHub** `! … commits here are authored as …` (a warning, the tool still starts) | `git config user.email <the account's email>`, as printed |
| **Copilot CLI** `gh has no stored login for octocat`, `the stored token for octocat was rejected`, `gh's stored login for octocat actually authenticates as hubot` | Copilot CLI runs with gh's token for the account, so the fix is gh's: `gh auth login --hostname github.com --git-protocol ssh --web` as octocat, then `gh auth switch --user <your usual account>`. Keyfit prints it and never runs it, as it switches gh's active account |
| **Copilot CLI** `gh's stored login for octocat is a classic personal access token, which Copilot CLI refuses` | gh was signed in with a classic token (`ghp_…`). Sign gh in again as octocat in the browser (the command above); that replaces the token with gh's own login token, which Copilot CLI accepts |
| **Copilot CLI** starts, then says the account has no Copilot access | Keyfit checks the GitHub login, not the Copilot plan. Enable Copilot for that account (or its organization) on github.com, or run Copilot CLI as another account: `keyfit add copilot --login <login>` |
| `this folder belongs to acme-api, whose Copilot CLI runs as GitHub octocat with the token gh keeps for it; copilot login would change only Copilot's own login` | inside a project with a Copilot CLI account, Copilot's own login (and `/login` in a session) isn't used. Sign gh in as the account instead (the command printed). To change Copilot's own login, run `copilot login` outside the project |
| `this folder belongs to acme-api; refusing --enable-mcp-server globex_cloud` | that MCP server belongs to another project, so Keyfit switched it off for the session. Leave the option out |
| `copilot not started: ~/code/globex belongs to globex, this folder to acme-api` | `copilot -C DIR` would work in DIR with this folder's accounts. Start it there: `cd ~/code/globex && copilot` |
| `copilot mcp list` shows another project's servers | Copilot's own commands (`mcp`, `config`, `plugin`, …) run as typed. Sessions (`copilot`, `copilot -p …`) start with those servers switched off (`--disable-mcp-server`; see `KEYFIT_DRY_RUN=1 copilot`) |
| **Gemini CLI** `not signed in with Google in ~/.config/keyfit/gemini/acme/.gemini` | the project's Gemini CLI folder has no sign-in yet. `GEMINI_CLI_HOME=… gemini --prompt-interactive /quit`, as printed (in a terminal, Keyfit offers it): Gemini CLI opens its own sign-in dialog there, with Sign in with Google selected; sign in as the project's Google account, press R when Gemini CLI asks to restart, and it quits. Then start `gemini` again |
| **Gemini CLI** `Gemini CLI is signed in as someone@gmail.com, the project's Google account is me@acme.com` | the folder holds the other Google account's sign-in. In a folder Keyfit made: remove that sign-in (`rm …/oauth_creds.json`, as printed) and sign in again. In your own `~/.gemini`, Keyfit never suggests removing your sign-in: give the project a folder of its own with `keyfit add gemini --new` and sign in there |
| **Gemini CLI** `Gemini CLI is set to a Gemini API key in …/settings.json, not Sign in with Google` (or Vertex AI, …) | that folder's Gemini CLI uses another sign-in method (`/auth` changed it). Set `"security": {"auth": {"selectedType": "oauth-personal"}}` in that file, as printed (`gemini-api-key` for a key account) |
| **Gemini CLI** `Google does not accept the sign-in's token (signed out or revoked)` | the sign-in was revoked (in your Google account's security settings, say). Sign in again, as printed (in a terminal, Keyfit offers it): Gemini CLI replaces a sign-in that no longer works |
| **Gemini CLI** `Google does not accept the API key`, `the API key may not use the Gemini API` | create a key at aistudio.google.com/apikey (or allow the Generative Language API for this one), then `keyfit add gemini --key` |
| **Gemini CLI** `not confirmed online (the token has expired; Gemini CLI renews it)` | not a problem: the sign-in's token is renewed by Gemini CLI when it starts, and Keyfit doesn't renew it for it. The local check (the email in the sign-in) decides until then |
| **Gemini CLI** says this account needs `GOOGLE_CLOUD_PROJECT` | a Workspace account using Gemini Code Assist needs a Google Cloud project, and Keyfit removes the one inherited from your shell. Give the account one: `keyfit add gemini --cloud-project ID` (with the other options you used), or put `GOOGLE_CLOUD_PROJECT=ID` in the project's `.env` |
| `this folder belongs to acme-api; refusing --allowed-mcp-server-names globex_cloud` | that MCP server belongs to another project. Leave it out of the list |
| `gemini mcp list` shows another project's servers | Gemini CLI's own commands (`mcp`, `extensions`, `skills`, …) run as typed. Sessions (`gemini`, `gemini -p …`) start with `--allowed-mcp-server-names` naming only the servers they keep (see `KEYFIT_DRY_RUN=1 gemini`) |
| **Claude** `not signed in` | `claude auth login`. For an account in another config folder, use `CLAUDE_CONFIG_DIR=… claude auth login`, as printed |
| **Claude** `signed in with api_key instead of a Claude subscription` | remove `ANTHROPIC_API_KEY` or `apiKeyHelper` from your Claude settings, or sign in again |
| **Claude** `signed in to org 'X', project expects 'Y'` | sign in to the expected organization (`claude auth login`) |
| **Vercel** `not signed in` | `vercel login` inside the project: Keyfit sends it to the project's login folder. The printed command names that folder, so it works anywhere |
| **Vercel** `no expected identity recorded` | the folder was made with `keyfit add vercel --new` and nobody signed in yet. Run `vercel login` inside the project, then the `keyfit add vercel --name … --config-dir …` command printed after it |
| **Vercel** `signed in as X, project expects Y` | that folder holds another login. Inside the project, run `vercel logout`, then `vercel login` as Y |
| **Vercel** `this login has no access to Vercel team T` | sign in as a member of T, or map a team of this login: `keyfit add vercel --team <slug>` |
| **Vercel** `… links this repository to another Vercel team (team_…)` | the repository was linked with another login or team. Remove the `.vercel` folder it names, then run `vercel link` inside the project; it links with the project's login and team |
| `this folder belongs to acme-api (Vercel team acme); refusing --scope globex` (also `--team`, `--global-config`, `--token`) | inside a project, `vercel` works only with the project's login and team. Keyfit already adds them, so leave the option out |
| **Supabase** `token not found (Keychain item keyfit/supabase-…)`, `… does not hold a Supabase access token`, `Supabase rejected the token (deleted or expired?)` | give the account a new token: create one at supabase.com/dashboard/account/tokens, then `keyfit add supabase --name …` and paste it. Or sign the CLI in (`supabase login`, outside the project) and copy its login: `keyfit add supabase --name … --cli` |
| **Supabase** `this token has no access to organization …` | the token belongs to someone who isn't a member of the project's organization. Add a token of a member: `keyfit add supabase --name …` |
| **Supabase** `… links this folder to project REF of another organization`, `… this token cannot reach that project` | the folder was linked (`supabase/.temp/project-ref`, or `SUPABASE_PROJECT_ID`) to a project of another account or organization. Run `supabase link` inside the project and pick one of its organization's projects |
| **Supabase** `! supabase … names REF, which this token does not find as a project (a preview branch?)` | a warning, not a failure: Supabase's API doesn't say which organization a preview branch belongs to, so that link isn't checked |
| `this folder belongs to acme-api, whose Supabase token Keyfit keeps …; supabase login would change only the supabase CLI's own login` (also `logout`) | inside a project with a Supabase account, the CLI's own login isn't used. To change the project's token: `keyfit add supabase --name …`. To change the CLI's own login, run `supabase login` outside the project |
| `this folder belongs to acme-api (Supabase organization Acme); refusing --project-ref REF: it belongs to another organization` (also `link REF`, `projects delete REF`, `gen types --project-id`, `--workdir`, `--org-id`, `--profile`) | inside a project, `supabase` works only with projects of the project's organization. Leave the option out to use the folder's linked project |
| **Cloudflare** `wrangler has no profile NAME on this Mac`, `wrangler profile NAME: not signed in`, `… the sign-in expired and wrangler could not renew it` | `wrangler auth create NAME` signs that profile in again (in the browser); in a terminal, Keyfit offers to run it. For the default profile it is `wrangler login` |
| **Cloudflare** `FOLDER is not bound to wrangler profile NAME (wrangler would use its default login)` | `wrangler auth activate NAME FOLDER`, as printed: it binds the folder and everything under it. Started in a terminal, Keyfit asks `Bind this folder now? [Y/n]` and runs it for you (in a Superset worktree, for the folder that holds the repository's worktrees). It never binds another project's folder, a folder outside every project, or your home folder or one above it; there it only prints the command. Worktrees outside the repository's folder (Superset's) need their own binding; bind the folder that holds them once (`wrangler auth activate NAME ~/.superset/worktrees/<repository>`). A folder you moved needs its binding again: wrangler matches bound folders by path |
| **Cloudflare** `FOLDER uses wrangler profile OTHER (bound at SUBFOLDER), project expects NAME` | a subfolder has a binding of its own, and the nearest binding wins. Run the printed `wrangler auth activate NAME SUBFOLDER` (started in a terminal, Keyfit asks `Bind this folder now?` and runs it), or remove that binding: `wrangler auth deactivate SUBFOLDER`. `wrangler auth list` shows every binding |
| **Cloudflare** `wrangler profile NAME has no access to account A (…); it reaches …` | the profile is signed in as someone who isn't a member of the project's account. `wrangler auth create NAME` and sign in as a member, or, if the project moved accounts, `keyfit add cloudflare --account <the new one>` |
| **Cloudflare** `wrangler.toml sets account_id X, project expects A (…)` (also `wrangler.json`, `wrangler.jsonc`, `[env.NAME]`) | the repository's wrangler config targets another account. Set `account_id` to the project's account, or, if the project moved, map it: `keyfit add cloudflare --profile NAME --account X` |
| **Cloudflare** `… is wrangler 4.x, which ignores profiles (4.106 or newer needed)` | the wrangler `npx` runs here is too old for profiles. `npm install --save-dev wrangler@latest` in the project (or `npm install -g wrangler@latest` for the global one) |
| **Cloudflare** `FOLDER/.env sets WRANGLER_TOKEN_URL, which would decide where wrangler sends the profile's sign-in` (also `.env.local`, `.env.<env>`, `CLOUDFLARE_API_BASE_URL` and the other `WRANGLER_AUTH_…`, `WRANGLER_REVOKE_URL`, `WRANGLER_CLIENT_ID`; or `refusing wrangler: …` for a sign-in command, a `--cwd` folder or an `--env-file`) | wrangler loads that file into its environment, so it would send the profile's token or sign-in to the host it names. Remove the line, as printed. Keyfit keeps wrangler on Cloudflare's own API and sign-in; a project that really uses another host (staging, a compliance region) isn't supported yet: `KEYFIT_DISABLE=1 wrangler …` runs one command without Keyfit |
| **Cloudflare** `! cloudflare … not checked online: no wrangler here` (a warning, the tool still starts) | no wrangler in the project's `node_modules` or on PATH to check the sign-in with. `npm install` in the project, or `npm install -g wrangler` |
| `this folder belongs to acme-api (wrangler profile acme, Cloudflare account Acme); refusing --profile other` (also `--cwd`, `--config`) | inside a project, `wrangler` works only with the project's profile and account. Leave the option out; the folder's binding already picks the profile |
| `this folder belongs to acme-api, which uses wrangler profile acme; wrangler login would change only wrangler's default login` (also `logout`) | `wrangler login` and `logout` act on wrangler's default sign-in, which the project doesn't use. To sign the project's profile in again: `wrangler auth create acme`. To change the default sign-in, run `wrangler login` outside the project |
| `refusing wrangler auth activate OTHER for FOLDER: that folder belongs to acme-api` (also `auth deactivate`, `auth create` and `auth delete` of a project's profile) | that command would move a registered folder off its project's sign-in. Change the project's profile with `keyfit add cloudflare --profile …` first, or bypass once: `KEYFIT_DISABLE=1 wrangler auth …` |
| **Cloudflare**, wrangler still uses another account although the checks pass | a `CLOUDFLARE_API_TOKEN` (or `CLOUDFLARE_API_KEY`) beats every wrangler profile. Keyfit removes it from the environment, but wrangler also reads it from the folder's `.env` and `.env.local`: remove it there. While it is there, the account stays pinned (`CLOUDFLARE_ACCOUNT_ID`) and Keyfit's online check runs with that token. With such a token set, wrangler also refuses `wrangler auth create` and `activate` |
| **Convex** `the convex CLI is not signed in`, `Convex rejected the token (signed out or revoked?)` for an account that is the CLI's own login | `npx convex login --force`. Starting `claude`, `codex`, `opencode`, `copilot` or `convex` in a terminal offers to run it (`Sign in now?`) |
| **Convex** `token not found (Keychain item keyfit/convex-…)`, `Convex rejected the token`, `… holds a deploy key (…\|…), not an access token` for a token Keyfit keeps | give the account a working token: sign the CLI in as that account (`npx convex login --force`), then `keyfit add convex --name … --copy`. A deploy key (`prod:…\|…`) is not a login; it belongs in the project's `.env.local` or CI settings |
| **Convex** `this login has no access to team …` | the login belongs to someone who isn't a member of the project's team. Sign in as a member (`npx convex login --force`), or for a token Keyfit keeps, add a member's: `keyfit add convex --name … --copy` |
| **Convex** `CONVEX_DEPLOYMENT in .env.local points this folder at deployment NAME of another team`, `… this login cannot reach it` | the folder was configured with another account or team. Run `npx convex dev --configure existing --team <the project's team>` inside the project and pick one of the team's projects; it rewrites `.env.local` |
| **Convex** `! convex … holds a project deploy key, which the convex CLI uses instead of any login` | a warning, not a failure. With a project deploy key in the folder, the convex CLI refuses every other login, so Keyfit leaves it as it is and doesn't check it (it can't tell the key's team without sending the key). Remove the key from `.env.local` to use the project's Convex account there |
| `this folder belongs to acme-api, whose Convex token Keyfit keeps …; convex login would change only the convex CLI's own login` (also `logout`) | inside a project whose Convex token Keyfit keeps, the CLI's own login isn't used. To change the project's token: `keyfit add convex --name …`. To change the CLI's own login, run `npx convex login` (npx doesn't go through Keyfit) |
| `this folder belongs to acme-api (Convex team Acme); refusing --deployment-name NAME: it belongs to another team` (also `--deployment`, `--env-file`, `--team`, `--admin-key`) | inside a project, `convex` works only with deployments of the project's team. Leave the option out to use the folder's own deployment |
| `npx convex` in a plain terminal acts as another account | `npx convex` doesn't go through Keyfit; only a Claude, Codex, OpenCode or Copilot CLI session (or `keyfit run -- npx convex …`) gives it the project's account |
| **Google Cloud** `gcloud has no valid sign-in for EMAIL (signed out, expired or revoked)`, `gcloud is not signed in as EMAIL` | `gcloud auth login EMAIL`, as printed; in a terminal, Keyfit offers to run it. Inside the project the sign-in lands in the project's configuration |
| **Google Cloud** `gcloud has no configuration NAME in ~/.config/gcloud` | `gcloud config configurations create NAME --no-activate` (`--no-activate` leaves the configuration you use elsewhere active), then sign it in inside the project: `gcloud auth login EMAIL`. Configurations moved with `CLOUDSDK_CONFIG` are looked for where `keyfit add gcloud` found them |
| **Google Cloud** `configuration NAME uses OTHER, the project's Google account is EMAIL`, `configuration NAME has no account` | the configuration is signed in as someone else. `gcloud config set account EMAIL --configuration NAME` when gcloud already holds EMAIL's sign-in, else `gcloud auth login EMAIL` inside the project, as printed. If the project's Google account itself is wrong: `keyfit add google --email …` |
| **Google Cloud** `configuration NAME works on project OTHER, the project expects ID` | `gcloud config set project ID --configuration NAME`, or, if the project moved: `keyfit add gcloud --project-id OTHER` |
| **Google Cloud** `EMAIL cannot reach project ID (it does not exist, or the account has no access)` | the project ID is mistyped, or the account has no role on it. Ask for access (any role will do, Viewer included), or map the right project: `keyfit add gcloud --project-id …` |
| **Google Cloud** `the project has no Google account` | a project says once which Google account it uses: `keyfit add google --email you@work.com` |
| **Google Cloud** `configuration NAME sets auth/access_token_file, so gcloud would not use EMAIL's sign-in` (also `credential_file_override`, `disable_credentials`) | the configuration takes its credentials from a file instead of the account. `gcloud config unset auth/access_token_file --configuration NAME`, as printed |
| `this folder belongs to acme-api (gcloud configuration acme, Google account me@acme.com, project acme-prod); refusing --project other` (also `--account`, `--configuration`, `--access-token-file`, `--flags-file`) | inside a project, `gcloud` works only as the project's account on its project. Leave the option out; to work on another Google Cloud project there, map it: `keyfit add gcloud --project-id …` |
| `refusing gcloud auth login OTHER`, `refusing gcloud config set project OTHER for configuration acme: acme-api uses it with project acme-prod` (also `config unset`, `configurations delete` and `rename`) | that command would move a registered project's configuration off its Google account or project. Change the project's mapping first (`keyfit add google --email …` or `keyfit add gcloud --project-id …`), or bypass once: `KEYFIT_DISABLE=1 gcloud …` |
| **Google Cloud**, client libraries or Terraform use another Google account although the checks pass | they use application default credentials (`gcloud auth application-default login`), one login per Mac that Keyfit doesn't check, unless `GOOGLE_APPLICATION_CREDENTIALS` names a file. Keyfit removes an inherited `GOOGLE_APPLICATION_CREDENTIALS` in a project with a Google Cloud account. Sign in again with `gcloud auth application-default login` as the project's account when you switch projects |
| **Firebase** `the firebase CLI is not signed in as EMAIL (it has …)`, `the firebase CLI is not signed in` | `firebase login:add EMAIL` (`firebase login` for the first sign-in), as printed; in a terminal, Keyfit offers to run it. The browser decides which account signs in: pick EMAIL |
| **Firebase** `the firebase CLI's folder FOLDER is bound to OTHER` (or `uses the default login OTHER`), `the project's Google account is EMAIL` | the firebase CLI binds a sign-in to each folder with a `firebase.json`, by its exact path, so a new worktree starts unbound. `firebase login:use EMAIL` in that folder, as printed; in a terminal, Keyfit asks `Bind this folder now?` and runs it |
| **Firebase** `the firebase CLI could not renew its sign-in for EMAIL (expired or revoked)`, `Google does not accept the firebase CLI's sign-in for EMAIL` | sign in again: `firebase login:add` and choose EMAIL in the browser (it renews that sign-in; `firebase login:add EMAIL` refuses an account it already has), or `firebase login --reauth` when EMAIL is the CLI's default login, as printed. `firebase login --reauth` where another account is the default would replace that one, so Keyfit stops it |
| **Firebase** `the firebase CLI works on OTHER in FOLDER (…), the project expects ID`, `no Firebase project selected in FOLDER` | `firebase use ID` picks the project for that folder (it is kept with the CLI, not in the repository). If `.firebaserc`'s default is another project on purpose, map that one: `keyfit add firebase --project-id OTHER` |
| **Firebase** `EMAIL cannot reach Firebase project ID (it does not exist, has no Firebase, or the account has no access)` | the project ID is mistyped, Firebase isn't added to that Google Cloud project, or the account has no access to it. Ask for access, or map the right project: `keyfit add firebase --project-id ID` |
| **Firebase** `! firebase … no firebase.json here, so the firebase CLI uses its default login OTHER` | an agent started outside every Firebase folder (a monorepo's top, say), where the CLI uses its default login: Keyfit's `firebase` adds `--account`, but `npx firebase-tools` there doesn't. Start the agent in the Firebase folder, or make the project's account the CLI's default login |
| `this folder belongs to acme-api (Google account me@acme.com, Firebase project acme-prod); refusing --project other` (also `--account`, `--token`, `refusing firebase use OTHER`, `refusing firebase login:use OTHER`) | inside a project, `firebase` works as its account on its project; an alias in `.firebaserc` counts as the project it names. Map another project if the folder really works on it (`keyfit add firebase --project-id …`), or bypass once: `KEYFIT_DISABLE=1 firebase …` |
| **Codex** `not signed in` | `codex login` (another home: `CODEX_HOME=… codex login`) |
| **Codex** `Codex is signed in with an API key`, `auth.json holds an API key next to the ChatGPT login` | `codex logout && codex login`, with your ChatGPT account |
| **OpenCode** `not signed in (no OpenCode database yet)`, `not signed in to anthropic in …`, `OpenCode does not see the anthropic sign-in` | `opencode auth login anthropic --standalone`, as printed (with `OPENCODE_DB=…` for an account Keyfit made); in a terminal, Keyfit offers to run it. Inside the project plain `opencode auth login` works too: Keyfit adds `--standalone` and the project's database. Without `--standalone`, a running background server would take the sign-in to its own database |
| **OpenCode** `! opencode … GROQ_API_KEY from your shell, not the project's account: left out of OpenCode sessions` (any provider) | a warning, not a failure: OpenCode starts without that key, which it would otherwise offer as a sign-in billed to you. Nothing to do. To stop the warning, remove the variable from your shell startup file; to use that provider in the project, store a key in the project's database instead: `opencode auth login <provider>` inside the project, and pick the API key method |
| **OpenCode** `opencode auth list did not answer within 30 s`, or another error from it | OpenCode didn't start its private server, or stopped with that error. Run `opencode auth list` inside the project (Keyfit adds `--standalone`) to see why |
| `opencode not started: inside a session of acme-api, opencode mcp could start OpenCode's background server with that project's accounts` (also `debug`, `plugin`, `service`, `pair`, `serve`) | OpenCode's background server serves every folder and keeps the environment of whichever start launched it; started from an agent session, it would keep that project's accounts. Run the command in a terminal of your own |
| `refusing opencode acp --login` | it signs in through the background server. Sign in with `opencode auth login` inside the project instead |
| `this folder belongs to acme-api; refusing --server` | inside a project, an OpenCode session runs on its own private server with the project's accounts; another server has its own sign-ins and environment. Leave the option out |
| `opencode not started: ~/code/globex belongs to globex, this folder to acme-api` | `opencode DIR` would start in DIR with this folder's accounts. Start it there: `cd ~/code/globex && opencode` |
| `opencode mcp list` shows another project's servers, or `opencode` in a plain terminal uses other sign-ins | `opencode mcp`, `serve`, `service` and the like talk to OpenCode's background server, which serves every folder, so Keyfit runs them unchanged. Sessions inside the project run on their own server with the project's sign-ins and servers |
| `… did not answer (…)`, `→ check the network and retry` | the service could not be reached, even after one retry, and there is no success from the last 24 hours to fall back on. Check the network |
| `project acme-api has no aws account mapped, so aws was not started` (same for `gh` and `github`, `vercel` and `vercel`) | inside a project, these three start only with the project's account for them. Map one: `keyfit add aws --profile …`, `keyfit add github --login …` or `keyfit add vercel`. To skip Keyfit once: `KEYFIT_DISABLE=1 aws …`. `supabase`, `wrangler`, `convex` and `gcloud` are different: without a Supabase, Cloudflare, Convex or Google Cloud account they run as before |
| `✘ github  octocta  no account [accounts.github.octocta]`, `✘ asw … unknown connection (a typo? …)` | a hand edit of `~/.config/keyfit/registry.toml` names an account that isn't there, or a connection Keyfit doesn't know. Fix the name, or map it again with `keyfit add …`. Only this project is affected |
| `this folder belongs to acme-api (AWS profile acme-dev); refusing --profile other` | another project's profile was asked for inside this project. If the profile belongs to a machine-wide tool, list it under `[settings] shared_aws_profiles` in `~/.config/keyfit/registry.toml` |

When a service doesn't answer but a check succeeded within the last 24 hours, the tool starts and Keyfit shows a warning, for example `workspace confirmed 3h 5m ago · could not re-check now (…)`. This fallback only covers network failures. A rejected key or a wrong account always fails.

## Results look stale

Successful online checks are reused for 30 minutes (see [the verification cache](/docs/security/#the-verification-cache)). Keyfit checks again right away when the identity behind a connection changes:

- a new AWS SSO or console sign-in
- a different Linear key, GitHub token (also the one for Copilot CLI), Vercel login, Supabase token or Convex login, a different linked Supabase project or Convex deployment, a new (or renewed) wrangler sign-in, a new gcloud sign-in or a change to the project's gcloud configuration, a new firebase sign-in, and any `gh auth login`, `logout` or `switch`
- a different Claude organization
- a new Gemini CLI sign-in (also a renewed token) or settings change in the project's Gemini CLI folder, or a different Gemini API key
- a new, removed or switched OpenCode sign-in in the project's database, and a new `…_API_KEY` or `…_TOKEN` variable in your shell (by name)
- a connection changed with `keyfit add`

Other changes are only noticed after the 30 minutes, for example a key revoked in the meantime. To check right now:

```bash
keyfit status        # always checks online and refreshes the cache
```

Deleting `~/.config/keyfit/cache/checks.json` is also safe; the next launch simply checks everything online. `keyfit status --offline` runs the local checks only and marks the online ones `not checked online`.

## Starts feel slow

With the checks cached, a shim adds about 0.1 s, and about 0.2 s for `gh`, `claude`, `codex`, `opencode`, `copilot` and `gemini` in a project with a GitHub or Copilot CLI account (see [Speed](https://github.com/FlamurMaliqi/keyfit/blob/main/docs/../README.md#speed)). If every start takes longer:

- **A check goes online every time.** Only successful checks are cached. A check that fails, or that is accepted only from the last 24 hours because the service did not answer (`could not re-check now`), is tried online again at the next start, with one retry. Run `keyfit status` and fix what it shows.
- **The cache can't be written.** Keyfit keeps it in `~/.config/keyfit/cache/checks.json`. If that folder isn't writable, every start checks online.
- **gh answers slowly.** In a project with a GitHub or Copilot CLI account, every `gh`, `claude`, `codex`, `opencode`, `copilot` and `gemini` start runs `gh auth token`. Time it with `time gh auth token --hostname github.com --user <login> >/dev/null`; it should take about 0.1 s.

To see what a start costs, run `time KEYFIT_DRY_RUN=1 gh` in the project. It runs the checks and starts nothing.

## "registry.toml has a mistake"

```
keyfit: claude not started, ~/.config/keyfit/registry.toml has a mistake
  ✘ Expected ']' at the end of a table declaration (at line 1, column 12)
    → fix that line in ~/.config/keyfit/registry.toml
  (bypass once: KEYFIT_DISABLE=1 claude …)
```

Usually a hand edit. Without a readable registry Keyfit can't tell projects apart, so this happens in every folder, including folders that aren't registered, and `keyfit` commands say the same (`keyfit: cannot use ~/.config/keyfit/registry.toml: …`). Fix the line it names; `keyfit doctor` checks the file again. Besides TOML mistakes, Keyfit refuses `roots` that aren't a list of folders (`roots = ["~/code/acme"]`) and a root that is your home folder or above it, since every folder under it would belong to that project.

## "internal error"

```
keyfit: internal error (TypeError: …) — claude was not started.
  Bypass once: KEYFIT_DISABLE=1 claude …
```

When Keyfit itself fails, it stops the tool instead of starting it unchecked.

1. Run `keyfit doctor` and fix anything it marks with ✘.
2. If that doesn't help, please [open a bug report](https://github.com/FlamurMaliqi/keyfit/issues/new/choose) and include:
   - the full `internal error` line
   - the output of `keyfit doctor`, run in the same folder
   - the output of `keyfit --version`
   - your macOS version

   None of these contain secret values, but remove project names, account IDs or paths you don't want public.
3. Until it's fixed, `KEYFIT_DISABLE=1 claude …` runs the tool without Keyfit.

## Other messages

- **`keyfit: claude is not installed (no claude on PATH besides Keyfit's shim)`.** The tool isn't installed, or its folder isn't on PATH. Keyfit skips its own shims and Superset's wrappers when looking for the real tool. Install it with the command printed below the message; `keyfit doctor` shows where it found each tool. For `wrangler`, a project that has it in `node_modules` runs its own copy with `npx wrangler`.
- **`keyfit: claude not started: Keyfit is not installed where its shims expect it`.** The Python or the package that ran `keyfit setup` is gone. After an upgrade, a reinstall or a move to another Python, run `keyfit setup` again. If you uninstalled Keyfit before running `keyfit setup --remove`, remove its line from your shell startup file (see [Uninstall](#uninstall)) and open a new terminal.
- **`keyfit needs Python 3.11 or newer`.** The `python3` that comes with macOS is 3.9. Install a newer one (`brew install python@3.13`, or `brew install pipx`, which brings one) and install Keyfit with it.
- **`keyfit: claude nested 6 levels deep through Keyfit — stopping to avoid a loop`.** Another wrapper on PATH calls the tool by name and leads back into Keyfit. Look at `type -a claude`.
- **A Keychain prompt appears.** Keys stored through Keyfit are read with `/usr/bin/security`, which those items trust without asking. A `keychain:service/account` reference to an item another app created may make macOS ask for permission. Allow it, or store the key through Keyfit with `keyfit secret set NAME`.

## Uninstall

```bash
keyfit setup --remove            # first: delete the shims and PATH files; take Keyfit's line out of your startup files
keyfit setup --remove --purge    # or also delete ~/.config/keyfit, after asking (--yes skips the question)
pipx uninstall keyfit            # last, or: brew uninstall keyfit
```

- **Which startup files.** `--remove` takes Keyfit's line, and the blank line before it, out of `~/.zshrc` (and `$ZDOTDIR/.zshrc`), `~/.bash_profile`, `~/.bash_login`, `~/.profile` and `~/.bashrc`. If you used `keyfit setup --rc PATH` for another file, run `keyfit setup --remove --rc PATH`.
- **What stays.** Without `--purge`, the registry stays, and `keyfit setup --rc` turns everything back on.
- **What `--purge` deletes:** the registry, the check cache, the Claude config folders made by `keyfit add claude --new`, the Vercel login folders made by `keyfit add vercel --new`, the OpenCode databases made by `keyfit add opencode --new` and the Gemini CLI folders made by `keyfit add gemini --new` or `--key` (with the logins in them). It refuses if `~/.config/keyfit` holds files Keyfit didn't write; delete those yourself if you are sure. Without a terminal it needs `--yes`.
- **Keychain items** Keyfit stored (service `keyfit`) are never deleted; `--purge` prints their names. Delete them in Keychain Access, or with `security delete-generic-password -s keyfit -a NAME`.
- **Afterwards,** open a new terminal (or run `hash -r`). `claude`, `codex`, `opencode`, `copilot`, `gemini`, `aws`, `gh`, `vercel`, `supabase`, `wrangler`, `convex`, `gcloud` and `firebase` then run directly again.

If `keyfit` itself no longer runs, do it by hand:

1. Remove the line ending in `# keyfit: tool shims on PATH` (or `# keyfit: claude/codex/aws/gh shims on PATH`, from an earlier version) from your startup files. Do this first, or new shells will complain that the file it loads is missing.
2. Delete `~/.config/keyfit`.
