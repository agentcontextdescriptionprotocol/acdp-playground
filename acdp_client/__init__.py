"""HTTP client + Pydantic wire types for ACDP registries.

The `acdp` package (built via maturin from acdp-rs/bindings/acdp-py)
owns all crypto. This package adds the async HTTP transport
(:class:`AcdpClient`) and Pydantic aliases for the wire types the
registry returns.
"""

from acdp_client.client import AcdpClient, AcdpHTTPError
from acdp_client.models import (
    Body,
    FullContext,
    PublishResponse,
    SearchResponse,
    Signature,
    StepEvent,
    WebhookEvent,
)
from acdp_client.token_manager import (
    CachedToken,
    ChallengeError,
    RefreshReason,
    TokenAuthError,
    TokenError,
    TokenIssueError,
    TokenManager,
    default_token_manager,
)

__all__ = [
    "AcdpClient",
    "AcdpHTTPError",
    "Body",
    "CachedToken",
    "ChallengeError",
    "FullContext",
    "PublishResponse",
    "RefreshReason",
    "SearchResponse",
    "Signature",
    "StepEvent",
    "TokenAuthError",
    "TokenError",
    "TokenIssueError",
    "TokenManager",
    "WebhookEvent",
    "default_token_manager",
]
