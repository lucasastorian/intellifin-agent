from datetime import datetime
from typing import List, Optional

from database import Database
from edgar.async_api import preload_ticker_lookup_async
from edgar.httpclient import async_http_client
from pipeline.company import Company


async def presync_tickers(
    tickers: List[str],
    database: Database,
    edgar_user_agent: str,
    start_year: int = 2017,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None
):
    """Presync all filings for a list of tickers.

    Args:
        tickers: List of ticker symbols to sync
        database: Database instance
        edgar_user_agent: EDGAR user agent string
        start_year: Start year for syncing (default: 2017)
        start_date: Optional start date filter (ISO format)
        end_date: Optional end date filter (ISO format)
    """
    current_year = datetime.now().year

    print(f"\n  → Presyncing {len(tickers)} tickers...", flush=True)

    # Warm up EDGAR async ticker lookup to avoid repeated network calls per ticker
    try:
        await preload_ticker_lookup_async()
    except Exception:
        # Non-fatal if warmup fails; proceed with per-ticker lookups
        pass

    # Open a shared async HTTP client context for all EDGAR calls in this batch.
    # This reduces client churn and satisfies libraries expecting an async bucket.
    async with async_http_client():
        for i, ticker in enumerate(sorted(tickers), 1):
            try:
                print(f"      [{i}/{len(tickers)}] {ticker} - Syncing...", flush=True)

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

                print(f"      [{i}/{len(tickers)}] {ticker} - Synced {synced_count} filings ✓", flush=True)

            except Exception as e:
                print(f"      [{i}/{len(tickers)}] {ticker} - Error: {e} ✗", flush=True)
                continue

    print(f"  ✓ Presync complete\n", flush=True)
