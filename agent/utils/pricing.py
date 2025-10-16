"""
Provider-specific pricing per million tokens.

Prices are in USD per 1 million tokens.
"""

PRICING = {
    "Anthropic": {
        "claude-sonnet-4-5": {
            "input_per_million": 3.0,
            "output_per_million": 15.0,
            "cached_input_per_million": 0.3,
        },
        "claude-haiku-4-5": {
            "input_per_million": 1.0,
            "output_per_million": 5.0,
            "cached_input_per_million": 0.1,
        },
        "claude-opus-4-1": {
            "input_per_million": 15,
            "output_per_million": 75.0,
            "cached_input_per_million": 1.50,
        }
    },
    "OpenAI": {
        "gpt-5": {
            "input_per_million": 1.25,
            "output_per_million": 10,
            "cached_input_per_million": 0.125,
        },
        "gpt-5-mini": {
            "input_per_million": 0.25,
            "output_per_million": 2.0,
            "cached_input_per_million": 0.025,
        },
    },
    "XAI": {
        "grok-4": {
            "input_per_million": 3.0,
            "output_per_million": 15.0,
            "cached_input_per_million": 0.75
        },
    },
    "Groq": {
        "openai/gpt-oss-120b": {
            "input_per_million": 0.15,
            "output_per_million": 0.75,
            "cached_input_per_million": 0.075
        },
        "openai/gpt-oss-20b": {
            "input_per_million": 0.10,
            "output_per_million": 0.50,
            "cached_input_per_million": 0.05
        },
        "moonshotai/kimi-k2-instruct-0905": {
            "input_per_million": 1,
            "output_per_million": 3,
            "cached_input_per_million": 50
        }
    },
    "Google": {
        "gemini-2.5-flash": {
            "input_per_million": 0.30,
            "output_per_million": 2.50,
            "cached_input_per_million": 0.30
        },
        "gemini-2.5-pro": {
            "input_per_million": 1.25,
            "output_per_million": 10,
            "cached_input_per_million": 1.25
        }
    }

}


def get_pricing(provider: str, model: str = None):
    """
    Get pricing for provider and model.

    Args:
        provider: "openai", "anthropic", or "xai"
        model: Optional model name for model-specific pricing

    Returns:
        Dict with input_per_million, output_per_million, and optionally cached_input_per_million
    """
    provider_pricing = PRICING[provider]

    if model and model in provider_pricing:
        return provider_pricing[model]
    else:
        return provider_pricing["default"]


def calculate_cost(
        uncached_input_tokens: int,
        cached_input_tokens: int,
        output_tokens: int,
        provider: str,
        model: str
) -> float:
    """Calculate cost in USD for token usage."""
    pricing = get_pricing(provider, model)

    input_cost = (uncached_input_tokens / 1_000_000) * pricing["input_per_million"]

    cached_cost = 0.0
    if cached_input_tokens > 0 and "cached_input_per_million" in pricing:
        cached_cost = (cached_input_tokens / 1_000_000) * pricing["cached_input_per_million"]

    output_cost = (output_tokens / 1_000_000) * pricing["output_per_million"]

    return input_cost + cached_cost + output_cost
