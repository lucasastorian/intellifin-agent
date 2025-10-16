from utils import presync_tickers
from evals.eval_dataset import EvalMode, EvalDataset
from database.database import Database
from pipeline.company_provisioner import CompanyProvisioner


async def provision_eval_data(database: Database, dataset_path: str, edgar_user_agent: str, limit: int = None):
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

    provisioner = CompanyProvisioner(database=database, edgar_user_agent=edgar_user_agent)
    await provisioner.provision()

    if tickers:
        await presync_tickers(
            tickers=list(tickers),
            database=database,
            edgar_user_agent=edgar_user_agent,
            start_year=2018
        )

    print(f"   ✅ Data provisioning complete!\n")
