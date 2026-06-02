"""HTTP client + Pydantic wire types for ACDP registries.

The `acdp` package (built via maturin from acdp-rs/bindings/acdp-py)
owns all crypto. This package adds the async HTTP transport
(:class:`AcdpClient`) and Pydantic aliases for the wire types the
registry returns.
"""

from acdp_client.client import AcdpClient, AcdpHTTPError
from acdp_client.models import (
    Body,
    CursorError,
    FullContext,
    PublishResponse,
    SearchHit,
    SearchResponse,
    Signature,
    StepEvent,
    WebhookEvent,
)
from acdp_client.signing import (
    ALG_ED25519,
    ALG_P256,
    is_p256,
    producer_algorithm,
    public_key_material,
    verify_signature,
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
    "ALG_ED25519",
    "ALG_P256",
    "AcdpClient",
    "AcdpHTTPError",
    "Body",
    "CachedToken",
    "ChallengeError",
    "CursorError",
    "FullContext",
    "PublishResponse",
    "RefreshReason",
    "SearchHit",
    "SearchResponse",
    "Signature",
    "StepEvent",
    "TokenAuthError",
    "TokenError",
    "TokenIssueError",
    "TokenManager",
    "WebhookEvent",
    "default_token_manager",
    "is_p256",
    "producer_algorithm",
    "public_key_material",
    "verify_signature",
]
