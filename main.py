import asyncio
import argparse
import logging
import signal
import atexit
from dotenv import load_dotenv
from agent.agent import Agent
from agent.agent_config import AgentMode
from utils.print_messages import print_messages

_agent_instance = None


def cleanup_handler():
    """Cleanup handler called on exit or signal."""
    if _agent_instance and hasattr(_agent_instance, 'database'):
        try:
            _agent_instance.database.close()
        except Exception:
            pass


def signal_handler(signum, frame):
    """Handle SIGINT and SIGTERM gracefully."""
    print(f"\nReceived signal {signum}, shutting down gracefully...")
    cleanup_handler()
    exit(0)


if __name__ == '__main__':
    load_dotenv()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    atexit.register(cleanup_handler)

    logging.getLogger('edgar.core').setLevel(logging.ERROR)

    parser = argparse.ArgumentParser(description='Run the IntelliFin agent')
    parser.add_argument('--query', type=str, help='Query to send to the agent',
                        default='''In 2024, who was Nominated to Serve on BBSI's (NASDAQ: BBSI) Board of Directors?  ''')
    parser.add_argument('--model', type=str, default='gpt-5', help='Model to use (default: gpt-5)')
    parser.add_argument('--max-iter', type=int, default=20, help='Max iterations (default: 15)')
    parser.add_argument('--reasoning-effort', type=str, default='minimal',
                        choices=['minimal', 'low', 'medium', 'high'],
                        help='Reasoning effort level (default: minimal)')
    parser.add_argument('--mode', type=str, default='full_no_web',
                        choices=['basic', 'web_search', 'web_code', 'full', 'full_no_web'],
                        help='Agent mode: basic (no tools), web_search (web only), web_code (web+code), '
                             'full (all tools+web), full_no_web (all tools, no web) (default: full_no_web)')

    args = parser.parse_args()

    agent = Agent(
        edgar_user_agent="Lucas Astorian <lucas@intellifin.ai>",
        model=args.model,
        max_iter=args.max_iter,
        reasoning_effort=args.reasoning_effort,
        mode=AgentMode(args.mode)
    )

    _agent_instance = agent

    try:
        asyncio.run(agent.run(query=args.query))
    finally:
        cleanup_handler()

    print_messages(agent.messages)
