#!/usr/bin/env python
"""
Run evaluations on the agent.

Usage:
    # Standard mode with default model (gpt-5)
    python run_eval.py --dataset evals/datasets/finance_agent_public_validation.yaml

    # Test with first 10 questions only
    python run_eval.py --dataset evals/datasets/finance_agent_public_validation.yaml --limit 10

    # Use Anthropic Claude
    python run_eval.py --dataset evals/datasets/finance_agent_public_validation.yaml --client anthropic

    # Use OpenAI o1 with reasoning
    python run_eval.py --dataset evals/datasets/finance_agent_public_validation.yaml --model o1 --reasoning-effort high

    # Different eval modes
    python run_eval.py --dataset datasets/finance_agent_public_validation.yaml --mode original
    python run_eval.py --dataset datasets/finance_agent_public_validation.yaml --mode updated
    python run_eval.py --dataset datasets/finance_agent_public_validation.yaml --mode standard  # default
    python run_eval.py --dataset datasets/finance_agent_public_validation.yaml --mode strict
"""
import asyncio
import argparse
import logging
from dotenv import load_dotenv

from agent.clients.openai_client import OpenAIClient
from agent.clients.anthropic_client import AnthropicClient
from agent.clients.xai_client import XAIClient
from evals.eval_runner import EvalRunner
from evals.eval_dataset import EvalMode, EvalDataset
from evals.answer_evaluator import Evaluator
from database.database import Database
from pipeline.company_provisioner import CompanyProvisioner
from pipeline.company import Company
from datetime import datetime, timedelta
from schema import schema


# Model defaults and capabilities
MODEL_CONFIGS = {
    "gpt-5-2025-08-07": {"client": "openai", "supports_reasoning": False, "default_temp": 1.0},
    "gpt-5-mini-2025-08-07": {"client": "openai", "supports_reasoning": False, "default_temp": 1.0},

    "claude-sonnet-4-5-20250929": {"client": "anthropic", "supports_reasoning": False, "default_temp": 1.0},

    "grok-4-0709": {"client": "xai", "supports_reasoning": False, "default_temp": 1.0},
}

DEFAULT_MODELS = {
    "openai": "gpt-5-2025-08-07",
    "anthropic": "claude-sonnet-4-5-20250929",
    "xai": "grok-4-0709"
}


def create_client(client_type: str, model: str, reasoning_effort: str, temperature: float,
                  verbose: bool = True, tier: str = "tier-3"):
    """
    Create the appropriate client with intelligent defaults.

    Args:
        client_type: "openai" or "anthropic"
        model: Specific model name or None for default
        reasoning_effort: The reasoning effort
        temperature: Temperature setting
        verbose: Whether to print streaming output (default: True)
        tier: API tier for rate limiting (default: tier-3)

    Returns:
        Configured client instance
    """
    if model:
        selected_model = model
        if model in MODEL_CONFIGS:
            inferred_client = MODEL_CONFIGS[model]["client"]
            if inferred_client != client_type:
                print(f"NOTE: Model '{model}' is an {inferred_client} model, using {inferred_client} client")
                client_type = inferred_client
    else:
        selected_model = DEFAULT_MODELS[client_type]

    supports_reasoning = MODEL_CONFIGS.get(selected_model, {}).get("supports_reasoning", False)

    final_reasoning_effort = reasoning_effort
    if supports_reasoning and not reasoning_effort:
        final_reasoning_effort = "low"
        print(f"NOTE: Using default reasoning_effort='low' for reasoning model '{selected_model}'")
    elif reasoning_effort and not supports_reasoning:
        print(f"NOTE: Model '{selected_model}' does not support reasoning_effort, ignoring --reasoning-effort flag")
        final_reasoning_effort = None

    kwargs = {
        "model": selected_model,
        "temperature": temperature,
        "verbose": verbose,
        "tier": tier
    }

    if final_reasoning_effort:
        kwargs["reasoning_effort"] = final_reasoning_effort

    if client_type == "openai":
        return OpenAIClient(**kwargs)
    elif client_type == "anthropic":
        return AnthropicClient(**kwargs)
    elif client_type == "xai":
        return XAIClient(**kwargs)
    else:
        raise ValueError(f"Unknown client type: {client_type}")


async def provision_eval_data(dataset_path: str, edgar_user_agent: str, limit: int = None, years_back: int = 10):
    """
    Provision data for eval dataset questions.

    Args:
        dataset_path: Path to eval dataset YAML
        edgar_user_agent: EDGAR user agent string
        limit: If set, only provision data for first N questions
        years_back: How many years of historical data to sync
    """
    print(f"\n📦 Provisioning data for eval...")

    dataset = EvalDataset.from_yaml(dataset_path, mode=EvalMode.STANDARD)
    questions = dataset.questions[:limit] if limit else dataset.questions

    tickers = set()
    for question in questions:
        if not question.ticker:
            continue
        for ticker in question.ticker.split(','):
            ticker = ticker.strip().upper()
            if ticker and ticker != 'TICKER':
                tickers.add(ticker)

    print(f"   Found {len(tickers)} unique tickers: {sorted(tickers)}")

    if not tickers:
        print("   No tickers to provision")
        return

    database = Database(schema=schema, base_path="./data/intellifin.db")

    provisioner = CompanyProvisioner(database=database, edgar_user_agent=edgar_user_agent)
    await provisioner.provision()

    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=years_back * 365)).strftime("%Y-%m-%d")
    current_year = datetime.now().year
    start_year = current_year - years_back

    print(f"   📅 Syncing filings from {start_date} to {end_date}")

    print(f"   🔄 Syncing {len(tickers)} companies...")
    for i, ticker in enumerate(sorted(tickers), 1):
        try:
            print(f"      [{i}/{len(tickers)}] {ticker} - Syncing...")

            company = Company(
                symbol=ticker,
                database=database,
                edgar_user_agent=edgar_user_agent,
                start_year=start_year,
                end_year=current_year + 1,
                verbose=False
            )

            synced_count = await company.upsert(
                forms=None,
                start_date=start_date,
                end_date=end_date,
                include_earnings_transcripts=True
            )

            print(f"      [{i}/{len(tickers)}] {ticker} - Synced {synced_count} filings ✓")

        except Exception as e:
            print(f"      [{i}/{len(tickers)}] {ticker} - Error: {str(e)} ✗")
            continue

    print(f"   ✅ Data provisioning complete!\n")


async def main():
    # Targeted logger suppression (keep root at WARNING)
    for name in (
        "edgar.core",
        "pipeline.filings.filing_tenk",
        "pyrate_limiter",
        "httpx",
        "httpxthrottlecache",
        "httpxthrottlecache.controller",
        "edgar",
        "pipeline",
    ):
        logging.getLogger(name).setLevel(logging.ERROR)
    load_dotenv()

    parser = argparse.ArgumentParser(
        description='Run agent evaluations',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Default (gpt-5, standard mode)
  python run_eval.py --dataset evals/datasets/finance_agent_public_validation.yaml

  # Test with first 10 questions only
  python run_eval.py --dataset evals/datasets/finance_agent_public_validation.yaml --limit 10

  # Use Claude Sonnet 4.5
  python run_eval.py --dataset evals/datasets/finance_agent_public_validation.yaml --client anthropic

  # Use OpenAI o1 with high reasoning
  python run_eval.py --dataset evals/datasets/finance_agent_public_validation.yaml --model o1 --reasoning-effort high

  # Compare modes
  python run_eval.py --dataset evals/datasets/finance_agent_public_validation.yaml --mode original
  python run_eval.py --dataset evals/datasets/finance_agent_public_validation.yaml --mode strict
        """
    )
    parser.add_argument(
        '--dataset',
        type=str,
        required=True,
        help='Path to eval dataset YAML file'
    )
    parser.add_argument(
        '--client',
        type=str,
        default='openai',
        choices=['anthropic', 'openai', 'xai'],
        help='LLM provider (default: anthropic). Auto-detected if --model is specified.'
    )

    parser.add_argument(
        '--model',
        type=str,
        help=f'Model name (default: OpenAI={DEFAULT_MODELS["openai"]}, '
             f'Anthropic={DEFAULT_MODELS["anthropic"]}, '
             f'xAI={DEFAULT_MODELS["xai"]})'
    )

    parser.add_argument(
        '--temperature',
        type=float,
        default=1.0,
        help='Temperature for sampling (default: 1.0)'
    )

    parser.add_argument(
        '--reasoning-effort',
        type=str,
        choices=['minimal', 'low', 'medium', 'high'],
        default="medium",
        help='Reasoning effort for thinking models'
    )

    parser.add_argument(
        '--max-iter',
        type=int,
        default=20,
        help='Max iterations per question (default: 20)'
    )
    parser.add_argument(
        '--mode',
        type=str,
        default='standard',
        choices=['original', 'updated', 'standard', 'strict'],
        help='Eval mode (default: standard). See docstring for details.'
    )

    parser.add_argument(
        '--limit',
        type=int,
        help='Limit evaluation to first N questions (useful for testing)'
    )

    parser.add_argument(
        '--tier',
        type=str,
        default='tier-3',
        choices=['tier-1', 'tier-2', 'tier-3', 'tier-4', 'tier-5'],
        help='API tier for rate limiting (default: tier-3). Anthropic: 1=30k, 2=450k, 3=800k, 4=2M tokens/min'
    )
    parser.add_argument(
        '--edgar-user-agent',
        type=str,
        default='Lucas Astorian <lucas@intellifin.ai>',
        help='EDGAR user agent string (default: Lucas Astorian <lucas@intellifin.ai>)'
    )

    args = parser.parse_args()

    client = create_client(
        client_type=args.client,
        model=args.model,
        reasoning_effort=args.reasoning_effort,
        temperature=args.temperature,
        verbose=False,
        tier=args.tier
    )

    evaluator = Evaluator()

    await provision_eval_data(
        dataset_path=args.dataset,
        edgar_user_agent=args.edgar_user_agent,
        limit=args.limit,
        years_back=10
    )

    print("=" * 60)
    print("EVAL CONFIGURATION")
    print("=" * 60)
    print(f"Dataset:          {args.dataset}")
    print(f"Agent Model:      {client.model}")
    print(f"Temperature:      {args.temperature}")
    print(f"API Tier:         {args.tier}")
    if args.reasoning_effort:
        print(f"Reasoning Effort: {args.reasoning_effort}")
    print(f"Max Iterations:   {args.max_iter}")
    print(f"Eval Mode:        {args.mode.upper()}")
    if args.limit:
        print(f"Question Limit:   {args.limit}")
    print("=" * 60)
    print()

    runner = EvalRunner(
        edgar_user_agent=args.edgar_user_agent,
        client=client,
        evaluator=evaluator,
        max_iter=args.max_iter
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
        temperature=args.temperature,
        tier=args.tier,
        reasoning_effort=args.reasoning_effort
    )


if __name__ == '__main__':
    asyncio.run(main())
