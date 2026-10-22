# Releasing Keyfit

1. **Version.** Set `__version__` in `keyfit/__init__.py`, the only place it lives
   (`pyproject.toml` reads it; `keyfit --version` prints it).
2. **Changelog.** In `CHANGELOG.md`, turn `Unreleased` into the version and today's date.
3. **Check.** `python3 -m unittest discover -s tests` passes and CI is green on `main`.
4. **Tag.** Commit, then `git tag -a vX.Y.Z -m "Keyfit X.Y.Z"` and `git push origin main vX.Y.Z`.
5. **PyPI** happens by itself: pushing the tag runs `.github/workflows/release.yaml`, which tests,
   checks the tag against `keyfit.__version__`, builds the sdist and wheel, and publishes them with
   PyPI trusted publishing (no token on any laptop or in GitHub secrets). One-time setup on
   pypi.org → Account → Publishing → "Add a new pending publisher": project `keyfit`, owner
   `FlamurMaliqi`, repository `keyfit`, workflow `release.yaml` (the exact file name: PyPI matches
   it letter for letter), environment `pypi`. Done for `keyfit` (checked 2026-10-16).
   To build locally instead: `python3 -m build && python3 -m twine check dist/*`, and try the wheel
   in a fresh virtualenv (`keyfit --version`).
6. **Check** <https://pypi.org/project/keyfit/> shows the version, then `pipx install keyfit` on a
   clean account works.
7. **Homebrew.** In the tap repository, update `Formula/keyfit.rb` (template:
   `packaging/homebrew/keyfit.rb`): `url` and `sha256` to the sdist PyPI serves, from PyPI's JSON:
   ```bash
   curl -fsS https://pypi.org/pypi/keyfit/X.Y.Z/json | python3 -c 'import json, sys
   for file in json.load(sys.stdin)["urls"]:
       if file["packagetype"] == "sdist": print(file["url"], file["digests"]["sha256"])'
   ```
   The `url` is the long `files.pythonhosted.org/packages/xx/yy/…` form (`brew audit` refuses the
   `packages/source/k/keyfit/…` shortcut). Never take the sha256 from a local `dist/` build: two sdist
   builds of the same commit differ, even with `SOURCE_DATE_EPOCH`. Commit,
   push, then check it the way users get it:
   ```bash
   brew update && brew reinstall --build-from-source <owner>/<tap>/keyfit
   brew test <owner>/<tap>/keyfit && brew audit --strict <owner>/<tap>/keyfit
   ```

## The first release

Once, before the first tag. Private vulnerability reporting and the environment rules need a public
repository on the free plan, and they must be in place before the tag: pushing it starts the publish.

1. **Public.** `gh repo edit FlamurMaliqi/keyfit --visibility public --accept-visibility-change-consequences`,
   and the same for the tap, `FlamurMaliqi/homebrew-keyfit`.
2. **Private vulnerability reporting**, which `SECURITY.md` links to:
   `gh api -X PUT repos/FlamurMaliqi/keyfit/private-vulnerability-reporting`.
3. **The `pypi` environment** (Settings → Environments → `pypi`): Flamur as required reviewer, and
   deployments only from tags matching `v*`. A pushed tag then waits for that approval before it publishes.
4. **Tag** (steps 1–4 above). `v0.1.0` already exists, on an older commit, from a release run that never
   published: delete it first with `git tag -d v0.1.0 && git push origin :refs/tags/v0.1.0`. Approve the
   waiting `pypi` deployment in the release run, then check PyPI (steps 5–6).
5. **GitHub release**, with the version's notes from `CHANGELOG.md`:
   ```bash
   awk '/^## \[/ { notes = /^## \[0\.1\.0\]/; next } /^\[[^]]+\]: / { next } notes' CHANGELOG.md > notes.md
   gh release create v0.1.0 --verify-tag --title "Keyfit 0.1.0" --notes-file notes.md
   ```
6. **The tap** (step 7 above), then `brew install flamurmaliqi/keyfit/keyfit` works on a clean account.
