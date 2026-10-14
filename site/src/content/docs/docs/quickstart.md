---
title: Quickstart
description: Install Keyfit and register your first project in five minutes.
sidebar:
  order: 1
---

<!-- Generated from README.md by site/scripts/sync_docs.py. Edit the source, not this file. -->

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

## Quickstart

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

If something seems off, run `keyfit doctor`. It checks the installation and prints the fix under every ✘. See also the [troubleshooting guide](/docs/troubleshooting/).

More on setup:

- **Without `--rc`,** `keyfit setup` changes nothing outside `~/.config/keyfit` and prints the line for you to add yourself.
- **The shell** comes from `$SHELL`; choose it with `--shell zsh|bash`.
- **zsh:** the line goes into `~/.zshrc`, or `$ZDOTDIR/.zshrc` when `ZDOTDIR` is set.
- **bash:** the line goes into the login profile macOS terminals read. That is the first of `~/.bash_profile`, `~/.bash_login` and `~/.profile` that exists. It also goes into `~/.bashrc` when that profile sources it.
- **Another file:** `--rc PATH`.
- **Keep the line last** in the file, so nothing added to PATH later in that file comes before the shims.

Already using direnv? `keyfit init` also imports `use aws PROFILE ACCOUNT_ID` and `export LINEAR_API_KEY=$VARIABLE` lines from the repository's `.envrc`. It reads the file as text and never runs it. Without a terminal, or with `--yes`, `init` asks nothing: it registers the repository and imports from `.envrc` only.
