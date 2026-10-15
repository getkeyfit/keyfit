from .aws import AwsAdapter
from .claude import ClaudeAdapter
from .cloudflare import CloudflareAdapter
from .codex import CodexAdapter
from .convex import ConvexAdapter
from .copilot import CopilotAdapter
from .gcloud import GcloudAdapter
from .github import GithubAdapter
from .google import GoogleAdapter
from .linear import LinearAdapter
from .opencode import OpenCodeAdapter
from .supabase import SupabaseAdapter
from .vercel import VercelAdapter

ADAPTERS = {
    "aws": AwsAdapter(),
    "linear": LinearAdapter(),
    "claude": ClaudeAdapter(),
    "codex": CodexAdapter(),
    "opencode": OpenCodeAdapter(),
    "copilot": CopilotAdapter(),
    "github": GithubAdapter(),
    "vercel": VercelAdapter(),
    "supabase": SupabaseAdapter(),
    "cloudflare": CloudflareAdapter(),
    "convex": ConvexAdapter(),
    "google": GoogleAdapter(),
    "gcloud": GcloudAdapter(),
}
