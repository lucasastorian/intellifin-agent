import asyncio
import argparse
import logging
from typing import Literal
from dotenv import load_dotenv

from agent.agent import Agent
from agent.clients.openai_client import OpenAIClient
from agent.clients.anthropic_client import AnthropicClient
from utils.print_messages import print_messages
from utils.run_summary import print_run_summary


def run_agent(query: str, user_agent: str, model: str, max_iter: int,
              reasoning_effort: Literal['minimal', 'low', 'medium', 'high']):
    """Runs the agent"""
    if model in ['gpt-5', 'gpt-5-mini']:
        client = OpenAIClient(
            model=model,
            temperature=1.0,
            reasoning_effort=reasoning_effort
        )

    elif args.model in ['claude-sonnet-4.5', 'claude-opus-4-1']:
        client = AnthropicClient(
            model=model,
            temperature=1.0,
            reasoning_effort="medium"
        )
    else:
        raise ValueError(f"Did not recognize model {args.model}")

    agent = Agent(
        edgar_user_agent=user_agent,
        client=client,
        max_iter=max_iter
    )

    asyncio.run(agent.run(query=query))
    print_messages(messages=agent.messages)
    print_run_summary(agent=agent)


if __name__ == '__main__':
    load_dotenv()

    logging.getLogger('edgar.core').setLevel(logging.ERROR)

    parser = argparse.ArgumentParser(description='Run the IntelliFin agent')
    parser.add_argument('--query', type=str, help='Query to send to the agent',
                        default='''What's Boeing's effective tax rate in 2024?''')
    parser.add_argument('--user-agent', type=str)

    parser.add_argument('--model', type=str, default='gpt-5',
                        choices=['gpt-5', 'gpt-5-mini', 'claude-sonnet-4.5', 'claude-opus-4-1'],
                        help='Model to use (default: gpt-5)')
    parser.add_argument('--max-iter', type=int, default=20, help='Max iterations (default: 15)')
    parser.add_argument('--reasoning-effort', type=str, default='minimal',
                        choices=['minimal', 'low', 'medium', 'high'],
                        help='Reasoning effort level (default: minimal)')

    args = parser.parse_args()

    run_agent(query=args.query, user_agent=args.user_agent, model=args.model, max_iter=args.max_iter,
              reasoning_effort=args.reasoning_effort)
