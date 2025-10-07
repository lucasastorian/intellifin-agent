import asyncio
import argparse
import logging
from dotenv import load_dotenv
from agent.agent import Agent
from utils.print_messages import print_messages


if __name__ == '__main__':
    load_dotenv()

    logging.getLogger('edgar.core').setLevel(logging.ERROR)

    parser = argparse.ArgumentParser(description='Run the IntelliFin agent')
    parser.add_argument('query', type=str, help='Query to send to the agent')
    parser.add_argument('--model', type=str, default='gpt-5', help='Model to use (default: gpt-5-mini)')
    parser.add_argument('--temperature', type=float, default=1.0, help='Temperature (default: 1.0)')
    parser.add_argument('--max-iter', type=int, default=10, help='Max iterations (default: 10)')

    args = parser.parse_args()

    agent = Agent(
        edgar_user_agent="Lucas Astorian <lucas@intellifin.ai>",
        model=args.model,
        temperature=args.temperature,
        max_iter=args.max_iter
    )

    asyncio.run(agent.run(query=args.query))

    print_messages(agent)
