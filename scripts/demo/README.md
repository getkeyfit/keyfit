# Demo video

`record.sh` records the 33-second terminal video of keyfit.dev and the launch posts (`out/demo.mp4`, 1920×1080):

1. In `~/code/harbor-api`, `claude` starts. Keyfit's status line shows the project's accounts, and `/mcp` lists only
   harbor's Linear server.
2. In `~/code/lumen-web`, the AWS SSO sign-in has expired: Keyfit does not start Claude, offers the sign-in, then
   starts it.
3. `/mcp` there lists only lumen's Linear server.

Keyfit's real checks and the real Claude Code run. The accounts behind them are stand-ins, so no real account, key
or path can appear in the video:

- `stubs/aws.py`: an `aws` CLI that answers `sts get-caller-identity` and `sso login` for the two profiles. The
  sign-in opens no browser.
- `stubs/linear_api.py`: Linear's GraphQL API on 127.0.0.1:47123. `build.sh` points a copy of the package at it
  (the only changed line).
- `stubs/linear_mcp.py`: a Linear MCP server that lists tools.
- `stubs/anthropic_api.py`: `ANTHROPIC_BASE_URL` for Claude Code, which runs with a fake API key in a config folder
  of its own, so it never finds your login. Its banner says "API Usage Billing".

Needs macOS, [VHS](https://github.com/charmbracelet/vhs) (`brew install vhs`), Python 3.11 or newer (`PYTHON=…` to
pick one) and Claude Code's native install (else `CLAUDE_BIN=…`, the real binary, not a wrapper or shim). The demo
HOME is `/Users/Shared/you`, because Claude Code shows paths under it; `record.sh` creates it and deletes it
afterwards. VHS drives a headless browser, so expect about a minute and 1 GB of memory.

```bash
scripts/demo/record.sh
```
