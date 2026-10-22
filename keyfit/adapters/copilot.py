"""Copilot CLI: which GitHub account a project's Copilot CLI sessions run as.

Account:  [accounts.copilot.octocat]  login = "octocat"
Project:  copilot = "octocat"   (`keyfit add copilot` takes the project's GitHub account)

Copilot CLI (`copilot`, verified with 1.0.97) signs in to GitHub: with its own login (`copilot login`, kept in the
Keychain), else with gh's. COPILOT_GITHUB_TOKEN, GH_TOKEN and GITHUB_TOKEN, in that order, come before both, and it
accepts gh's OAuth token (not a classic personal access token, ghp_). So an account is a GitHub login gh holds, like
the GitHub account's, and the token decides it: Copilot's config folder (~/.copilot, or COPILOT_HOME: settings,
sessions, mcp-config.json) stays shared, and Copilot's own login is never used in the project.

inject  — COPILOT_GITHUB_TOKEN = the login's token, read from gh (`gh auth token --user`, read-only) unless a
          session's token this project's check confirmed is at hand (as the GitHub adapter does). Removed: the
          variables that would point Copilot at another account, host or model provider — inherited Copilot tokens
          (also the coding agent's GITHUB_COPILOT_…_TOKEN), COPILOT_GH_HOST and GH_HOST, and COPILOT_PROVIDER_…
          (bring-your-own-key: another provider's key, no GitHub account at all). Only Copilot reads them. Copilot
          takes model providers from a file before those variables: COPILOT_PROVIDERS_CONFIG, else providers.json in
          its folder (yours, in every project). COPILOT_PROVIDERS_CONFIG is set to a file that never exists, as
          when you have no providers.json.
forget  — in a project without a Copilot account, the token and the providers file Keyfit set for another project
          go.
local   — gh holds a token for the login, and it is not a classic personal access token, which Copilot refuses.
remote  — the GitHub check with that token: GET api.github.com/user must return the login. Whether the account has
          Copilot, Copilot itself says when it starts (there is no public endpoint that tells).
"""

from __future__ import annotations

from ..checks import Check
from .github import GithubAdapter, read_token

TOKEN = "COPILOT_GITHUB_TOKEN"
MARK = "KEYFIT_COPILOT_LOGIN"  # set next to the token Keyfit injected: the account's GitHub login
# Copilot's coding agent passes its token on in these; GH_HOST and COPILOT_GH_HOST pick another GitHub host.
SCRUB = ("GITHUB_COPILOT_GITHUB_TOKEN", "GITHUB_COPILOT_AGENT_GITHUB_TOKEN", "GITHUB_COPILOT_INFERENCE_TOKEN",
         "GITHUB_COPILOT_API_TOKEN", "COPILOT_GH_HOST", "GH_HOST")
PROVIDER = "COPILOT_PROVIDER_"  # bring-your-own-key: a model provider instead of the GitHub account
PROVIDERS = "COPILOT_PROVIDERS_CONFIG"  # a file of model providers, else <Copilot's folder>/providers.json
NO_PROVIDERS = "/var/empty/providers.json"  # never exists: /var/empty is root's and stays empty
CLASSIC = "ghp_"


class CopilotAdapter(GithubAdapter):
    """The GitHub adapter's token, fix and checks (remote, fingerprint, confirmed), with Copilot's variable."""

    kind = "copilot"
    token_var = TOKEN

    def inject(self, conn, env: dict) -> None:
        login = conn.get("login", "")
        candidates = [env.get(TOKEN)]  # a session Keyfit started, or your shell
        if env.get("GH_TOKEN") and env.get("KEYFIT_GITHUB_LOGIN", "").lower() == login.lower():
            candidates.append(env["GH_TOKEN"])  # the project's GitHub account is the same login
        for var in [v for v in env if v in SCRUB or v == TOKEN or v.startswith(PROVIDER) or v == PROVIDERS]:
            env.pop(var)
        env[PROVIDERS] = NO_PROVIDERS
        token = next((t for t in candidates if t and self.confirmed(conn, env, t)), None)
        if token is None:
            token, _ = read_token(login, env)
        if token:
            env[TOKEN] = token
        env[MARK] = login

    def forget(self, env: dict) -> None:
        if MARK in env:  # Keyfit gave Copilot another project's account
            for var in (TOKEN, MARK):
                env.pop(var, None)
            if env.get(PROVIDERS) == NO_PROVIDERS:
                env.pop(PROVIDERS)

    def local(self, conn, env: dict) -> Check:
        label = self.label(conn)
        if not conn.get("login"):
            return Check(self.kind, "fail", label, "no GitHub login recorded", "keyfit add copilot --login <login>")
        token = env.get(TOKEN)
        if not token:
            return Check(self.kind, "fail", label, f"gh has no stored login for {conn['login']}", self.login_fix(conn))
        if token.startswith(CLASSIC):
            return Check(self.kind, "fail", label,
                         f"gh's stored login for {conn['login']} is a classic personal access token, which Copilot "
                         f"CLI refuses", self.login_fix(conn))
        return Check(self.kind, "ok", label, "", pending=True)
