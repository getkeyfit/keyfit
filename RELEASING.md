# Releasing Keyfit

1. **Version.** Set `__version__` in `keyfit/__init__.py`, the only place it lives
   (`pyproject.toml` reads it; `keyfit --version` prints it).
2. **Changelog.** In `CHANGELOG.md`, turn `Unreleased` into the version and today's date.
3. **Check.** `python3 -m unittest discover -s tests` passes and CI is green on `main`.
4. **Tag.** Commit, then `git tag -a vX.Y.Z -m "Keyfit X.Y.Z"` and `git push origin main vX.Y.Z`.
5. **PyPI** happens by itself: pushing the tag runs `.github/workflows/release.yml`, which tests,
   checks the tag against `keyfit.__version__`, builds the sdist and wheel, and publishes them with
   PyPI trusted publishing (no token on any laptop or in GitHub secrets). One-time setup on
   pypi.org → Account → Publishing → "Add a new pending publisher": project `keyfit`, owner
   `FlamurMaliqi`, repository `keyfit`, workflow `release.yml`, environment `pypi`.
   To build locally instead: `python3 -m build && python3 -m twine check dist/*`, and try the wheel
   in a fresh virtualenv (`keyfit --version`).
6. **Check** <https://pypi.org/project/keyfit/> shows the version, then `pipx install keyfit` on a
   clean account works.
7. **Homebrew.** In the tap repository, update `Formula/keyfit.rb` (template:
   `packaging/homebrew/keyfit.rb`): `url` to the sdist's "Source" link on
   <https://pypi.org/project/keyfit/#files> (the long `files.pythonhosted.org/packages/xx/yy/…` form;
   `brew audit` refuses the `packages/source/k/keyfit/…` shortcut) and `sha256` to
   `shasum -a 256 dist/keyfit-X.Y.Z.tar.gz` (the same value PyPI shows). Commit,
   push, then check it the way users get it:
   ```bash
   brew update && brew reinstall --build-from-source <owner>/<tap>/keyfit
   brew test <owner>/<tap>/keyfit && brew audit --strict <owner>/<tap>/keyfit
   ```
