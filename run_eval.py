import asyncio
import argparse
from utils.supress_warnings import suppress_edgar_no_xbrl_warnings
from edgar import set_identity
from dotenv import load_dotenv

from database import Database
from evals.eval_runner import EvalRunner
from utils import get_client
from evals.eval_dataset import EvalMode
from evals.answer_evaluator import Evaluator
from schema import schema
from evals.provision_eval_data import provision_eval_data


async def main(args):
    # Suppress noisy EDGAR warnings about missing XBRL attachments
    suppress_edgar_no_xbrl_warnings()

    set_identity(args.edgar_user_agent)

    database = Database(schema=schema, base_path="./data/intellifin.db")

    client = get_client(model=args.model, temperature=1, reasoning_effort=args.reasoning_effort,
                        verbose=args.serial)

    await provision_eval_data(
        database=database,
        dataset_path=args.dataset,
        edgar_user_agent=args.edgar_user_agent,
        limit=args.limit,
    )

    evaluator = Evaluator()
    runner = EvalRunner(
        edgar_user_agent=args.edgar_user_agent,
        client=client,
        evaluator=evaluator,
        max_iter=20
    )

    mode_map = {
        'original': EvalMode.ORIGINAL,
        'updated': EvalMode.UPDATED,
        'standard': EvalMode.STANDARD,
        'strict': EvalMode.STRICT
    }

    eval_mode = mode_map[args.mode]

    await runner.run_eval(
        dataset_path=args.dataset,
        output_dir="evals/results",
        mode=eval_mode,
        limit=args.limit,
        reasoning_effort=args.reasoning_effort,
        serial=args.serial,
        verbose=args.serial,
    )


if __name__ == '__main__':
    load_dotenv()

    parser = argparse.ArgumentParser(
        description='Run agent evaluations',
        formatter_class=argparse.RawDescriptionHelpFormatter)

    parser.add_argument(
        '--dataset',
        type=str,
        default="datasets/finance_agent_public_validation.yaml"
    )

    parser.add_argument('--model', type=str, default='gpt-5',
                        choices=['gpt-5', 'gpt-5-mini',
                                 'claude-haiku-4-5', 'claude-sonnet-4-5', 'claude-opus-4-1',
                                 "gemini-2.5-flash", "gemini-2.5-pro",
                                 'grok-4',
                                 "openai/gpt-oss-120b", "openai/gpt-oss-20b", "moonshotai/kimi-k2-instruct-0905"],
                        help='Model to use (default: gpt-5)')

    parser.add_argument(
        '--reasoning-effort',
        type=str,
        choices=['minimal', 'low', 'medium', 'high'],
        default="medium",
    )

    parser.add_argument(
        '--mode',
        type=str,
        default='standard',
        choices=['original', 'updated', 'standard', 'strict'],
    )

    parser.add_argument(
        '--limit',
        type=int,
        help='Limit evaluation to first N questions (useful for testing)',
        default=50
    )

    parser.add_argument(
        '--edgar-user-agent',
        type=str,
    )

    parser.add_argument(
        '--serial',
        action='store_true',
        help='Run evaluations serially instead of in parallel (for debugging)'
    )

    args = parser.parse_args()

    asyncio.run(main(args=args))
