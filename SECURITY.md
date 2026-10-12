# Security policy

Keyfit hands credentials to the tools you run, so security reports matter more here than in
most small projects. Thank you for taking the time.

What Keyfit reads, stores and sends, and what it does not protect against, is described in
[docs/security.md](docs/security.md).

## Supported versions

Only the latest release gets security fixes. Please check that a problem still happens with
the latest version before you report it.

## Reporting a vulnerability

Please do not open a public issue, pull request or discussion for a vulnerability.

Report it privately instead:

- on GitHub: the repository's **Security** tab, then **Report a vulnerability**
  (<https://github.com/FlamurMaliqi/keyfit/security/advisories/new>), or
- by email to <contact@flamur-maliqi.de>.

Please include:

- what an attacker can do, and what they need first (local access, a malicious repository, …)
- steps to reproduce, ideally with made-up accounts and keys
- the output of `keyfit --version` and your macOS version

Never send real tokens, keys or passwords, not even your own expired ones.

## What counts

For example:

- a secret value showing up in Keyfit's output, logs, process arguments, the registry or
  its cache
- a tool started with another project's accounts, or with credentials Keyfit should have
  removed
- a failed check that still lets the tool start (apart from the documented cases: a network
  error within 24 hours of a successful check, sign-in commands such as `aws sso login`, or
  `KEYFIT_DISABLE=1`)
- Keyfit writing into a repository, or a repository's files making Keyfit run code or pick
  accounts it should not
