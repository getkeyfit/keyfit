---
title: Commands
description: Everyday Keyfit commands.
sidebar:
  order: 3
---

<!-- Generated from README.md by site/scripts/sync_docs.py. Edit the source, not this file. -->

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

`--project NAME` makes any `keyfit add …` change another project. Account names (`--name`) can be shared between projects. The registry lives in `~/.config/keyfit/registry.toml`. It stores where secrets live, never their values; see [security](/docs/security/).
