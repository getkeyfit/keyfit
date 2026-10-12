from .aws import AwsAdapter
from .claude import ClaudeAdapter
from .codex import CodexAdapter
from .github import GithubAdapter
from .linear import LinearAdapter

ADAPTERS = {
    "aws": AwsAdapter(),
    "linear": LinearAdapter(),
    "claude": ClaudeAdapter(),
    "codex": CodexAdapter(),
    "github": GithubAdapter(),
}
