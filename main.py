import argparse
import logging
from dotenv import load_dotenv
from agent.agent_config import AgentMode
from cli.intellifin_tui import launch_tui

if __name__ == '__main__':
    load_dotenv()

    # Suppress edgar logging
    logging.getLogger('edgar.core').setLevel(logging.ERROR)

    parser = argparse.ArgumentParser(description='IntelliFin Agent - Interactive Financial Research Assistant')
    parser.add_argument('--model', type=str, default='gpt-5', help='Model to use (default: gpt-5)')
    parser.add_argument('--max-iter', type=int, default=20, help='Max iterations (default: 20)')
    parser.add_argument('--reasoning-effort', type=str, default='high',
                        choices=['minimal', 'low', 'medium', 'high'],
                        help='Reasoning effort level (default: high)')
    parser.add_argument('--mode', type=str, default='full_no_web',
                        choices=['basic', 'web_search', 'web_code', 'full', 'full_no_web'],
                        help='Agent mode: basic (no tools), web_search (web only), web_code (web+code), '
                             'full (all tools+web), full_no_web (all tools, no web) (default: full_no_web)')

    args = parser.parse_args()

    launch_tui(
        edgar_user_agent="Lucas Astorian <lucas@intellifin.ai>",
        model=args.model,
        max_iter=args.max_iter,
        reasoning_effort=args.reasoning_effort,
        mode=AgentMode(args.mode)
    )
