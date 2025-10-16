from __future__ import annotations
from typing import TYPE_CHECKING

from agent.utils.pricing import calculate_cost

# Avoid circular import at runtime; only import for type checking
if TYPE_CHECKING:  # pragma: no cover
    from agent.agent import Agent
    from agent.clients.base_client import BaseClient


def print_run_summary(agent: "Agent"):
    """Print a concise run summary: model, iterations, tokens, estimated cost.

    Args:
        agent: The Agent instance (must implement get_token_usage and have num_iter)
    """
    cost = calculate_cost(
        cached_input_tokens=agent.usage.cached_prompt_tokens,
        uncached_input_tokens=agent.usage.uncached_prompt_tokens,
        output_tokens=agent.usage.thinking_tokens + agent.usage.completion_tokens,
        provider=agent.client.provider,
        model=agent.client.model
    )

    print("\n" + "=" * 80)
    print("RUN SUMMARY")
    print("=" * 80)
    print(f"Model: {agent.client.model} ({agent.client.provider})")
    print(f"Reasoning effort: {agent.client.reasoning_effort}")
    print(f"Iterations: {agent.num_iter}")
    print(f"Duration: {agent.duration:2f} seconds")
    print(f"Tokens: {agent.usage.input_tokens} in / {agent.usage.completion_tokens} out / {agent.usage.thinking_tokens} thinking")
    print(f"Estimated Cost: ${cost:.4f}")
    print("=" * 80 + "\n")
