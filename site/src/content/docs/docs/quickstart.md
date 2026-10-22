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

If something seems off, run `keyfit doctor`. It checks the installation and prints the fix under every ✘. See also the [troubleshooting guide](/docs/troubleshooting/).

### Setup options

- **Without `--rc`,** `keyfit setup` changes nothing outside `~/.config/keyfit` and prints the line for you to add yourself.
- **The shell** comes from `$SHELL`; choose it with `--shell zsh|bash`.
- **zsh:** the line goes into `~/.zshrc`, or `$ZDOTDIR/.zshrc` when `ZDOTDIR` is set.
- **bash:** the line goes into the login profile macOS terminals read. That is the first of `~/.bash_profile`, `~/.bash_login` and `~/.profile` that exists. It also goes into `~/.bashrc` when that profile sources it.
- **Another file:** `--rc PATH`.
- **Keep the line last** in the file, so nothing added to PATH later in that file comes before the shims.
