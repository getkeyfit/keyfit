# Template of the formula for the Homebrew tap (getkeyfit/homebrew-keyfit). Copy it to Formula/keyfit.rb
# there and set url/sha256 to the PyPI release: url is the sdist's "Source" link on PyPI's files page, not
# this packages/source/ shortcut, which `brew audit` refuses (see RELEASING.md). Users then run:
#   brew install <owner>/<tap>/keyfit && keyfit setup --rc
class Keyfit < Formula
  include Language::Python::Virtualenv

  desc "Per-project, verified environments for AI coding tools"
  homepage "https://keyfit.dev"
  url "https://files.pythonhosted.org/packages/source/k/keyfit/keyfit-0.1.0.tar.gz"
  sha256 "0000000000000000000000000000000000000000000000000000000000000000" # sha256 of that sdist
  license "MIT"

  depends_on :macos # keys live in the macOS Keychain; `keyfit setup` refuses elsewhere for now
  depends_on "python@3.13"

  # No resources: Keyfit uses the standard library only.

  def install
    virtualenv_install_with_resources
  end

  def caveats
    <<~EOS
      Put Keyfit in front of claude, codex, opencode, copilot, gemini, aws, gh, vercel, supabase, wrangler, convex, gcloud and firebase (new terminals pick it up):
        keyfit setup --rc
      The shims run #{opt_libexec}/bin/python, so they survive `brew upgrade`.
      Run `keyfit setup` again if this formula ever moves to another Python version.
      Before `brew uninstall keyfit`, run: keyfit setup --remove
    EOS
  end

  test do
    assert_match "keyfit #{version}", shell_output("#{bin}/keyfit --version")
  end
end
