import asyncio
import argparse
import logging
from typing import Literal
from dotenv import load_dotenv
from edgar import set_identity

from agent.agent import Agent
from database import Database
from schema import schema
from utils.print_messages import print_messages
from utils import print_run_summary, presync_tickers, get_client
from pipeline.company_provisioner import CompanyProvisioner
from utils.supress_warnings import suppress_edgar_no_xbrl_warnings, suppress_pyrate_limiter_warnings

suppress_pyrate_limiter_warnings()
suppress_edgar_no_xbrl_warnings()


def run_agent(query: str, user_agent: str, model: str, max_iter: int,
              reasoning_effort: Literal['minimal', 'low', 'medium', 'high'],
              run_presync: bool = False, tickers: str = None):
    """Runs the agent"""
    set_identity(user_agent)

    client = get_client(model=model, temperature=1, reasoning_effort=reasoning_effort)

    database = Database(schema=schema, base_path="./data/intellifin.db")

    provisioner = CompanyProvisioner(database=database, edgar_user_agent=user_agent)
    asyncio.run(provisioner.provision())

    if tickers and run_presync:
        asyncio.run(
            presync_tickers(
                tickers=tickers.split(','),
                database=database,
                edgar_user_agent=user_agent,
                start_year=2019
            ))

    agent = Agent(
        database=database,
        edgar_user_agent=user_agent,
        client=client,
        max_iter=max_iter,
        skip_sync=False
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
                        choices=['gpt-5', 'gpt-5-mini',
                                 'claude-haiku-4-5', 'claude-sonnet-4-5', 'claude-opus-4-1',
                                 "gemini-2.5-flash", "gemini-2.5-pro",
                                 'grok-4',
                                 "openai/gpt-oss-120b", "openai/gpt-oss-20b", "moonshotai/kimi-k2-instruct-0905"],
                        help='Model to use (default: gpt-5)')
    parser.add_argument('--max-iter', type=int, default=30, help='Max iterations (default: 30)')
    parser.add_argument('--reasoning-effort', type=str, default='high',
                        choices=['minimal', 'low', 'medium', 'high'],
                        help='Reasoning effort level (default: medium)')
    # parser.add_argument('--presync', action='store_true', default=False,
    #                     help='Presync filings from EDGAR (for benchmarking pre-synced databases)')
    # parser.add_argument('--tickers', type=str,
    #                     help='Syncs the ticker symbols for fast access / more accurate benchmarks')

    args = parser.parse_args()

    run_agent(query=args.query, user_agent=args.user_agent, model=args.model, max_iter=args.max_iter,
              reasoning_effort=args.reasoning_effort, run_presync=False, tickers=None)
