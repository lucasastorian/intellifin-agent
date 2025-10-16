from typing import Literal

from agent.clients import OpenAIClient, AnthropicClient, XAIClient, GroqClient, GeminiClient, BaseClient


def get_client(model: str, temperature: float, reasoning_effort: Literal['minimal', 'low', 'medium', 'high'],
               verbose: bool = True) -> BaseClient:
    """Returns the appropriate client given a model"""
    if model in ['gpt-5', 'gpt-5-mini']:
        client = OpenAIClient(
            model=model,
            temperature=temperature,
            reasoning_effort=reasoning_effort,
            verbose=verbose
        )

    elif model in ['claude-haiku-4-5', 'claude-sonnet-4-5', 'claude-opus-4-1']:
        client = AnthropicClient(
            model=model,
            temperature=temperature,
            reasoning_effort=reasoning_effort,
            verbose=verbose
        )

    elif model == 'grok-4':
        client = XAIClient(
            model=model,
            temperature=temperature,
            reasoning_effort=reasoning_effort,
            verbose=verbose
        )

    elif model in ["openai/gpt-oss-120b", "openai/gpt-oss-20b", "moonshotai/kimi-k2-instruct-0905"]:
        client = GroqClient(
            model=model,
            temperature=1,
            reasoning_effort=reasoning_effort,
            verbose=verbose
        )

    elif model in ['gemini-2.5-flash', 'gemini-2.5-pro']:
        client = GeminiClient(
            model=model,
            temperature=temperature,
            reasoning_effort=reasoning_effort,
            verbose=verbose
        )

    else:
        raise ValueError(f"Did not recognize model {model}")

    return client
