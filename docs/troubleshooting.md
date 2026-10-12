# Troubleshooting

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
     1. Make Keyfit's line (it ends in `# keyfit: claude/codex/aws/gh shims on PATH`) the last line of the startup file doctor names.
     2. Run `keyfit setup` again. This rewrites the PATH files in `~/.config/keyfit`, and earlier versions placed the shims differently.
     3. Open a new terminal.
   - `✘ shims … runs <path>, which no longer exists`: the Python or the checkout that ran `keyfit setup` has moved, for example after a Python upgrade. Run `keyfit setup` again.
2. **Run `type -a claude`.** The first line should be `~/.config/keyfit/bin/claude`. In Superset terminals, it should be `~/.superset/bin/claude` with the Keyfit shim right after it (see [below](#superset-terminals)). If it says `claude is an alias for …` or `claude is a shell function`, that alias or function runs before PATH is searched. Remove it from your startup file; some installers add one.
3. **The shell may remember an old location.** Run `hash -r` (bash) or `rehash` (zsh), or open a new terminal.
4. **Check which file got the line.** `keyfit setup --rc` prints the file it changed.
   - zsh uses `$ZDOTDIR/.zshrc` when `ZDOTDIR` is set. If your terminal app sets `ZDOTDIR`, that may not be the file you expect. Pass the file yourself: `keyfit setup --rc ~/.zshrc`.
   - bash uses the login profile macOS terminals read. It also uses `~/.bashrc`, but only when that profile sources it; a `bash` started inside a terminal reads only `~/.bashrc`.
5. **The folder may not be registered.** If `keyfit which` says `not registered`, run `keyfit init`. In folders that aren't registered, tools run unchanged on purpose.
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
  ✔ PATH      ~/.config/keyfit/bin   right after Superset's agent wrapper, before every real tool
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
  ✘ aws       acme-dev → …4321   SSO session expired
    → aws sso login --sso-session acme
  (bypass once: KEYFIT_DISABLE=1 claude …)
```

Run the command after `→`. Sign-in commands always go through, even while checks fail, and they run with the project's environment. That way they sign in the account the project needs. Then start the tool again; failed checks are never cached.

| Message | What to do |
|---|---|
| **AWS** `not signed in to SSO`, `SSO session expired` | `aws sso login --sso-session NAME` (or `--profile PROFILE`), as printed |
| **AWS** `not signed in (aws login)`, `console sign-in expired (12 h limit)` | `aws login --profile PROFILE`. A console sign-in lasts at most 12 hours |
| **AWS** `profile 'P' is signed in to account …1234, project expects …5678` | the profile belongs to another account. Map the right one: `keyfit add aws --profile <the right profile>` |
| **AWS** `no profile 'P' in ~/.aws/config` | create it (`aws configure sso --profile P`), or map an existing profile |
| **AWS** `AWS did not answer within 40 s (a Keychain prompt may be waiting — choose Always Allow)` | a `credential_process` profile (Granted, for example) is waiting for a Keychain prompt. Answer it, then try again |
| **Linear** `key not found ($LINEAR_ACME_API_KEY)` | `env:` keys are read from the environment you start the tool in, and that variable isn't set there. Export it, or run `keyfit add linear` to keep the key in the Keychain instead |
| **Linear** `Linear rejected the key (revoked, expired or wrong)` | create a new personal API key in Linear (Settings → Security & access), then run `keyfit add linear` |
| **Linear** `the key belongs to workspace 'x', project expects 'y'` | paste the key for the expected workspace: `keyfit add linear --workspace y` |
| **GitHub** `gh has no stored login for octocat`, `the stored token for octocat was rejected` | `gh auth login --hostname github.com --git-protocol ssh --web` as octocat, then `gh auth switch --user <your usual account>` |
| **GitHub** `! … commits here are authored as …` (a warning, the tool still starts) | `git config user.email <the account's email>`, as printed |
| **Claude** `not signed in` | `claude auth login`. For an account in another config folder, use `CLAUDE_CONFIG_DIR=… claude auth login`, as printed |
| **Claude** `signed in with api_key instead of a Claude subscription` | remove `ANTHROPIC_API_KEY` or `apiKeyHelper` from your Claude settings, or sign in again |
| **Claude** `signed in to org 'X', project expects 'Y'` | sign in to the expected organization (`claude auth login`) |
| **Vercel** `not signed in` | `vercel login` inside the project: Keyfit sends it to the project's login folder. The printed command names that folder, so it works anywhere |
| **Vercel** `no expected identity recorded` | the folder was made with `keyfit add vercel --new` and nobody signed in yet. Run `vercel login` inside the project, then the `keyfit add vercel --name … --config-dir …` command printed after it |
| **Vercel** `signed in as X, project expects Y` | that folder holds another login. Inside the project, run `vercel logout`, then `vercel login` as Y |
| **Vercel** `this login has no access to Vercel team T` | sign in as a member of T, or map a team of this login: `keyfit add vercel --team <slug>` |
| **Vercel** `… links this repository to another Vercel team (team_…)` | the repository was linked with another login or team. Remove the `.vercel` folder it names, then run `vercel link` inside the project; it links with the project's login and team |
| `this folder belongs to acme-api (Vercel team acme); refusing --scope globex` (also `--team`, `--global-config`, `--token`) | inside a project, `vercel` works only with the project's login and team. Keyfit already adds them, so leave the option out |
| **Codex** `not signed in` | `codex login` (another home: `CODEX_HOME=… codex login`) |
| **Codex** `Codex is signed in with an API key`, `auth.json holds an API key next to the ChatGPT login` | `codex logout && codex login`, with your ChatGPT account |
| `… did not answer (…)`, `→ check the network and retry` | the service could not be reached, even after one retry, and there is no success from the last 24 hours to fall back on. Check the network |
| `project acme-api has no aws account mapped, so aws was not started` (same for `gh` and `github`, `vercel` and `vercel`) | map one: `keyfit add aws --profile …`, `keyfit add github --login …` or `keyfit add vercel`. To skip Keyfit once: `KEYFIT_DISABLE=1 aws …` |
| `this folder belongs to acme-api (AWS profile acme-dev); refusing --profile other` | another project's profile was asked for inside this project. If the profile belongs to a machine-wide tool, list it under `[settings] shared_aws_profiles` in `~/.config/keyfit/registry.toml` |

When a service doesn't answer but a check succeeded within the last 24 hours, the tool starts and Keyfit shows a warning, for example `workspace confirmed 3h 5m ago · could not re-check now (…)`. This fallback only covers network failures. A rejected key or a wrong account always fails.

## Results look stale

Successful online checks are reused for 30 minutes (see [the verification cache](security.md#the-verification-cache)). Keyfit checks again right away when the identity behind a connection changes:

- a new AWS SSO or console sign-in
- a different Linear key, GitHub token or Vercel login
- a different Claude organization
- a connection changed with `keyfit add`

Other changes are only noticed after the 30 minutes, for example a key revoked in the meantime. To check right now:

```bash
keyfit status        # always checks online and refreshes the cache
```

Deleting `~/.config/keyfit/cache/checks.json` is also safe; the next launch simply checks everything online. `keyfit status --offline` runs the local checks only and marks the online ones `not checked online`.

## "internal error"

```
keyfit: internal error (TOMLDecodeError: Expected ']' at the end of a table declaration (at line 1, column 12)) — claude was not started.
  Bypass once: KEYFIT_DISABLE=1 claude …
```

When Keyfit itself fails, it stops the tool instead of starting it unchecked. If it cannot read its registry, that happens in every folder, including folders that aren't registered.

1. Run `keyfit doctor`. If the `registry` line fails with a TOML error, fix the line it names in `~/.config/keyfit/registry.toml` (usually a hand edit).
2. Otherwise, please [open a bug report](https://github.com/FlamurMaliqi/keyfit/issues/new/choose) and include:
   - the full `internal error` line
   - the output of `keyfit doctor`, run in the same folder
   - the output of `keyfit --version`
   - your macOS version

   None of these contain secret values, but remove project names, account IDs or paths you don't want public.
3. Until it's fixed, `KEYFIT_DISABLE=1 claude …` runs the tool without Keyfit.

## Other messages

- **`keyfit: the real claude was not found on PATH`.** The tool isn't installed, or its folder isn't on PATH. Keyfit skips its own shims and Superset's wrappers when looking for the real tool. `keyfit doctor` shows where it found each tool.
- **`keyfit: claude nested 6 levels deep through Keyfit — stopping to avoid a loop`.** Another wrapper on PATH calls the tool by name and leads back into Keyfit. Look at `type -a claude`.
- **A Keychain prompt appears.** Keys stored through Keyfit are read with `/usr/bin/security`, which those items trust without asking. A `keychain:service/account` reference to an item another app created may make macOS ask for permission. Allow it, or store the key through Keyfit with `keyfit secret set NAME`.

## Uninstall

```bash
keyfit setup --remove            # delete the shims and PATH files; take Keyfit's line out of your startup files
keyfit setup --remove --purge    # also delete ~/.config/keyfit, after asking (--yes skips the question)
pipx uninstall keyfit            # or: brew uninstall keyfit
```

- **Which startup files.** `--remove` takes Keyfit's line, and the blank line before it, out of `~/.zshrc` (and `$ZDOTDIR/.zshrc`), `~/.bash_profile`, `~/.bash_login`, `~/.profile` and `~/.bashrc`. If you used `keyfit setup --rc PATH` for another file, run `keyfit setup --remove --rc PATH`.
- **What stays.** Without `--purge`, the registry stays, and `keyfit setup --rc` turns everything back on.
- **What `--purge` deletes:** the registry, the check cache, the Claude config folders made by `keyfit add claude --new` and the Vercel login folders made by `keyfit add vercel --new` (with the logins in them). It refuses if `~/.config/keyfit` holds files Keyfit didn't write; delete those yourself if you are sure. Without a terminal it needs `--yes`.
- **Keychain items** Keyfit stored (service `keyfit`) are never deleted; `--purge` prints their names. Delete them in Keychain Access, or with `security delete-generic-password -s keyfit -a NAME`.
- **Afterwards,** open a new terminal (or run `hash -r`). `claude`, `codex`, `aws`, `gh` and `vercel` then run directly again.

If `keyfit` itself no longer runs, do it by hand:

1. Remove the line ending in `# keyfit: claude/codex/aws/gh shims on PATH` from your startup files. Do this first, or new shells will complain that the file it loads is missing.
2. Delete `~/.config/keyfit`.
