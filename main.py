import asyncio
import argparse
import logging
import signal
import atexit
from dotenv import load_dotenv
from agent.agent import Agent
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
    logging.getLogger('pipeline.filings.filing_tenk').setLevel(logging.ERROR)
    logging.getLogger('pipeline.filings.filing_tenq').setLevel(logging.ERROR)
    logging.getLogger('pipeline.filings.filing_twentyf').setLevel(logging.ERROR)

    parser = argparse.ArgumentParser(description='Run the IntelliFin agent')
    parser.add_argument('--query', type=str, help='Query to send to the agent',
                        default='''How has US Steel addressed its planned merger with Nippon Steel and its effect on its business operations?''')
    parser.add_argument('--model', type=str, default='gpt-5', help='Model to use (default: gpt-5)')
    parser.add_argument('--temperature', type=float, default=1.0, help='Temperature (default: 1.0)')
    parser.add_argument('--max-iter', type=int, default=20, help='Max iterations (default: 15)')

    args = parser.parse_args()

    agent = Agent(
        edgar_user_agent="Lucas Astorian <lucas@intellifin.ai>",
        model=args.model,
        temperature=args.temperature,
        max_iter=args.max_iter
    )

    _agent_instance = agent

    try:
        asyncio.run(agent.run(query=args.query))
    finally:
        cleanup_handler()

    print_messages(agent)
