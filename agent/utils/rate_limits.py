"""
Provider-specific rate limits by tier and model.

These are INPUT token limits per minute (not counting output tokens).
Buffer ratio of 0.875 (87.5%) gives headroom for tool schemas and safety margin.

NOTE: Rate limits vary by provider, model, and tier. These are for Anthropic Sonnet 4.5 models.
"""

RATE_LIMITS = {
    "anthropic": {
        "claude-sonnet-4-5-20250929": {
            "tier-1": {"tokens_per_minute": 30_000, "requests_per_minute": 50},
            "tier-2": {"tokens_per_minute": 450_000, "requests_per_minute": 1_000},
            "tier-3": {"tokens_per_minute": 800_000, "requests_per_minute": 2_000},
            "tier-4": {"tokens_per_minute": 2_000_000, "requests_per_minute": 5_000},
        },
        "default": {
            "tier-1": {"tokens_per_minute": 30_000, "requests_per_minute": 50},
            "tier-2": {"tokens_per_minute": 450_000, "requests_per_minute": 1_000},
            "tier-3": {"tokens_per_minute": 800_000, "requests_per_minute": 2_000},
            "tier-4": {"tokens_per_minute": 2_000_000, "requests_per_minute": 5_000},
        }
    },
    "openai": {
        "default": {
            "tier-1": {"tokens_per_minute": 200_000, "requests_per_minute": 500},
            "tier-3": {"tokens_per_minute": 1_000_000, "requests_per_minute": 10_000},
            "tier-4": {"tokens_per_minute": 4_000_000, "requests_per_minute": 10_000},
            "tier-5": {"tokens_per_minute": 30_000_000, "requests_per_minute": 30_000},
        }
    },
    "xai": {
        "grok-4-0709": {
            "tier-3": {"tokens_per_minute": 2_000_000, "requests_per_minute": 10_000},
        },
        "default": {
            "tier-3": {"tokens_per_minute": 2_000_000, "requests_per_minute": 10_000},
        }
    }
}


BUFFER_RATIO = 0.875


def get_rate_limits(provider: str, tier: str, model: str = None):
    """
    Get rate limits for provider and tier with safety buffer applied.

    Args:
        provider: "anthropic" or "openai"
        tier: Tier level (e.g., "tier-3")
        model: Optional model name for model-specific limits

    Returns:
        Dict with tokens_per_minute and requests_per_minute
    """
    provider_limits = RATE_LIMITS[provider]

    if model and model in provider_limits:
        limits = provider_limits[model][tier]
    else:
        limits = provider_limits["default"][tier]

    return {
        "tokens_per_minute": int(limits["tokens_per_minute"] * BUFFER_RATIO),
        "requests_per_minute": limits["requests_per_minute"]
    }
