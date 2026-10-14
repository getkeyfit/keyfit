from .aws import AwsAdapter
from .claude import ClaudeAdapter
from .cloudflare import CloudflareAdapter
from .codex import CodexAdapter
from .convex import ConvexAdapter
from .github import GithubAdapter
from .linear import LinearAdapter
from .supabase import SupabaseAdapter
from .vercel import VercelAdapter

ADAPTERS = {
    "aws": AwsAdapter(),
    "linear": LinearAdapter(),
    "claude": ClaudeAdapter(),
    "codex": CodexAdapter(),
    "github": GithubAdapter(),
    "vercel": VercelAdapter(),
    "supabase": SupabaseAdapter(),
    "cloudflare": CloudflareAdapter(),
    "convex": ConvexAdapter(),
}
