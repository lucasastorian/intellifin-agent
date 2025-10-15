from agent.agent import Agent
from agent.clients.base_client import BaseClient
from agent.utils.pricing import calculate_cost


def print_run_summary(agent: Agent):
    """Print a concise run summary: model, iterations, tokens, estimated cost.

    Args:
        agent: The Agent instance (must implement get_token_usage and have num_iter)
        client: The client used (must have model attribute)
    """

    client_name = type(agent.client).__name__.lower()
    if "anthropic" in client_name:
        provider = "anthropic"
    elif "xai" in client_name:
        provider = "xai"
    else:
        provider = "openai"

    cost = calculate_cost(
        cached_input_tokens=agent.usage.cached_prompt_tokens,
        uncached_input_tokens=agent.usage.uncached_prompt_tokens,
        output_tokens=agent.usage.thinking_tokens + agent.usage.completion_tokens,
        provider=provider,
        model=agent.client.model
    )

    print("\n" + "=" * 80)
    print("RUN SUMMARY")
    print("=" * 80)
    print(f"Model: {getattr(agent.client, 'model', 'unknown')} ({provider})")
    print(f"Reasoning effort: {agent.client.reasoning_effort}")
    print(f"Iterations: {getattr(agent, 'num_iter', 0)}")
    print(f"Tokens: {agent.usage.input_tokens} in / {agent.usage.completion_tokens} out / {agent.usage.thinking_tokens} thinking")
    print(f"Estimated Cost: ${cost:.4f}")
    print("=" * 80 + "\n")
