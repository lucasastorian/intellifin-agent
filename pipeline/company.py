import os
import aiohttp
import asyncio
from datetime import date
from tqdm.asyncio import tqdm
from typing import List, Optional

from edgar import Company as EdgarCompany, set_identity
from edgar.entity.filings import EntityFilings, EntityFiling

from database.database import Database
from pipeline.filings.base_filing import BaseFiling
from pipeline.filings.filing_tenk import FilingTenK
from pipeline.filings.filing_tenq import FilingTenQ
from pipeline.filings.filing_eightk import FilingEightK
from pipeline.filings.filing_deffourteena import FilingDefFourteenA
from pipeline.filings.filing_sixk import FilingSixK
from pipeline.filings.filing_twentyf import FilingTwentyF
from pipeline.transcripts.transcript import Transcript


class Company:
    forms: List[str] = ["10-K", "10-Q", "8-K",
                        "DEF 14A",
                        "20-F", "6-K"]

    def __init__(self, symbol: str, database: Database, edgar_user_agent: str, start_year: int = 2015,
                 end_year: int = 2027):
        self.symbol = symbol
        self.database = database
        self.start_year = start_year
        self.end_year = end_year

        set_identity(edgar_user_agent)
        self.company = EdgarCompany(cik_or_ticker=self.symbol)

    async def upsert(self, forms: Optional[List[str]] = None, start_date: Optional[str] = None,
                     end_date: Optional[str] = None, include_earnings_transcripts: bool = False) -> int:
        if self.company.not_found:
            return 0

        company = await self._get_company()
        if company is None:
            return 0

        synced_count = await self._upsert_filings(company=company, forms=forms, start_date=start_date,
                                                  end_date=end_date)

        if include_earnings_transcripts:
            await self._sync_transcripts(company=company, start_date=start_date, end_date=end_date)

        await self.on_sync_complete(company_id=company['id'])

        return synced_count

    async def on_sync_complete(self, company_id: int):
        update_data = {"synced": True}

        if self.company.fiscal_year_end:
            update_data["fiscal_year_end"] = self.company.fiscal_year_end

        await self.database.table("companies").update(update_data).eq("id", company_id).execute()

    async def _upsert_filings(self, company: dict, forms: Optional[List[str]] = None,
                              start_date: Optional[str] = None, end_date: Optional[str] = None) -> int:
        filings = self._load_filings(forms=forms)

        if start_date or end_date:
            filings = self._filter_filings_by_date(filings, start_date, end_date)

        async def upsert_filing_async(filing: EntityFiling):
            if await self._is_filing_synced(filing.accession_number):
                return False

            parser = self._get_filing_parser(filing=filing, company=company)
            await parser.upsert()
            return True

        tasks = [upsert_filing_async(filing) for filing in filings]
        synced = 0

        with tqdm(total=len(filings), desc=f"Loading Edgar Filings for {self.symbol}") as pbar:
            for coro in asyncio.as_completed(tasks):
                if await coro:
                    synced += 1
                pbar.update(1)

        return synced

    async def _get_company(self) -> Optional[dict]:
        response = await self.database.table("companies").select("id").contains("symbols", self.symbol).limit(
            1).execute()

        if not response.data:
            return None

        return response.data[0]

    def _load_filings(self, forms: Optional[List[str]] = None) -> EntityFilings:
        """Load filings from EDGAR"""
        forms_to_load = forms if forms else self.forms
        return self.company.get_filings(form=forms_to_load, year=list(range(self.start_year, self.end_year)))

    def _filter_filings_by_date(self, filings: EntityFilings, start_date: Optional[str] = None,
                                end_date: Optional[str] = None) -> List[EntityFiling]:
        """Filter filings by report_date range (fallback to filing_date if report_date missing)"""
        filtered = []
        start = date.fromisoformat(start_date) if start_date else None
        end = date.fromisoformat(end_date) if end_date else None

        for filing in filings:
            # NOTE: Some filings (e.g., DEF 14A proxy statements) don't have report_date.
            # In those cases, fallback to filing_date for date filtering.
            filter_date = filing.report_date or filing.filing_date

            if not filter_date:
                continue

            if isinstance(filter_date, str):
                filter_date = date.fromisoformat(filter_date)

            if start and filter_date < start:
                continue
            if end and filter_date > end:
                continue

            filtered.append(filing)

        return filtered

    async def _is_filing_synced(self, accession_number: str) -> bool:
        """Check if filing is already synced"""
        response = await self.database.table("filings").select("synced").eq("accession_number", accession_number).limit(
            1).execute()
        return len(response.data) > 0 and response.data[0].get('synced', False)

    def _get_filing_parser(self, filing: EntityFiling, company: dict) -> BaseFiling:
        if filing.form in ["10-K", "10-K/A"]:
            return FilingTenK(filing=filing, company=company, database=self.database)

        elif filing.form in ["10-Q", "10-Q/A"]:
            return FilingTenQ(filing=filing, company=company, database=self.database)

        elif filing.form in ["8-K", "8-K/A"]:
            return FilingEightK(filing=filing, company=company, database=self.database)

        elif filing.form in ["DEF 14A", "DEF 14A/A"]:
            return FilingDefFourteenA(filing=filing, company=company, database=self.database)

        elif filing.form in ["6-K", "6-K/A"]:
            return FilingSixK(filing=filing, company=company, database=self.database)

        elif filing.form in ["20-F", "20-F/A"]:
            return FilingTwentyF(filing=filing, company=company, database=self.database)

        else:
            raise ValueError(f"Did not recognize form {filing.form}")

    async def _sync_transcripts(self, company: dict, start_date: Optional[str] = None,
                               end_date: Optional[str] = None):
        """Syncs earnings transcripts for the given date range (uses batch endpoint per fiscal year)"""
        async with aiohttp.ClientSession() as session:
            all_transcript_dates = await self._load_transcript_dates(session)
            filtered_dates = self._filter_transcript_dates(all_transcript_dates, start_date, end_date)
            fiscal_years_needed = set(meta['fiscalYear'] for meta in filtered_dates)

            years_to_load = []
            for fiscal_year in fiscal_years_needed:
                if not await self._are_all_transcripts_synced_for_year(company['id'], fiscal_year, all_transcript_dates):
                    years_to_load.append(fiscal_year)

            if not years_to_load:
                return 0

            tasks = [self._load_transcript_batch(fiscal_year, session) for fiscal_year in years_to_load]
            batch_results = await asyncio.gather(*tasks)

            upsert_tasks = []
            for batch_data in batch_results:
                for transcript_data in batch_data:
                    if self._is_transcript_in_range(transcript_data, filtered_dates):
                        transcript = Transcript(
                            content=transcript_data['content'],
                            fiscal_year=transcript_data['year'],
                            fiscal_quarter=transcript_data['quarter'],
                            date=transcript_data['date'],
                            company=company,
                            database=self.database
                        )
                        upsert_tasks.append(transcript.upsert())

            results = await asyncio.gather(*upsert_tasks)
            return sum(1 for r in results if r)

    async def _upsert_transcript(self, data: dict, company: dict):
        """Upserts a single transcript"""
        transcript = Transcript(content=data['content'], fiscal_year=data['year'], fiscal_quarter=data['quarter'],
                                date=date['date'], company=company, database=self.database)
        return await transcript.upsert()

    async def _load_transcripts(self):
        """Loads transcripts via the FMP API"""
        if not os.environ.get("FMP_API_KEY"):
            return

        tasks = []

        async with aiohttp.ClientSession() as session:
            for fiscal_year in range(self.start_year, self.end_year):
                tasks.append(self._load_transcript_batch(fiscal_year=fiscal_year, session=session))

        transcript_data = await asyncio.gather(*tasks)

        return transcript_data

    async def _load_transcript_batch(self, fiscal_year: int, session: aiohttp.ClientSession) -> List[dict]:
        """Loads a batch of transcripts for a given fiscal year"""
        params = {"apikey": os.environ['FMP_API_KEY'], "year": f"{fiscal_year}"}
        async with session.get(f"https://financialmodelingprep.com/api/v4/batch_earning_call_transcript/{self.symbol}",
                               params=params) as response:

            data = await response.json()

            return data

    async def _load_transcript_dates(self, session: aiohttp.ClientSession):
        """Loads all the dates for a given transcript"""
        params = {"apikey": os.environ['FMP_API_KEY'], "symbol": self.symbol}

        async with session.get(f"https://financialmodelingprep.com/stable/earning-call-transcript-dates", params=params) as response:
            data = await response.json()

            return data

    @staticmethod
    def _filter_transcript_dates(transcript_dates: List[dict], start_date: Optional[str],
                                 end_date: Optional[str]) -> List[dict]:
        """Filter transcript metadata by date range"""
        if not start_date and not end_date:
            return transcript_dates

        start = date.fromisoformat(start_date) if start_date else None
        end = date.fromisoformat(end_date) if end_date else None

        filtered = []
        for meta in transcript_dates:
            transcript_date = date.fromisoformat(meta['date'])
            if start and transcript_date < start:
                continue

            if end and transcript_date > end:
                continue

            filtered.append(meta)

        return filtered

    async def _are_all_transcripts_synced_for_year(self, company_id: int, fiscal_year: int,
                                                   all_transcript_dates: List[dict]) -> bool:
        """Check if ALL available transcripts for a fiscal year are synced (cross-reference FMP vs SQLite)"""
        available_quarters = set(
            meta['quarter'] for meta in all_transcript_dates
            if meta['fiscalYear'] == fiscal_year
        )

        if not available_quarters:
            return True

        response = await self.database.table("earnings_transcripts").select("fiscal_period").eq(
            "company_id", company_id
        ).eq("fiscal_year", fiscal_year).execute()

        synced_periods = set(r['fiscal_period'] for r in response.data)

        return available_quarters <= synced_periods

    @staticmethod
    def _is_transcript_in_range(transcript_data: dict, filtered_dates: List[dict]) -> bool:
        """Check if transcript matches any of the filtered date range metadata"""
        return any(
            meta['fiscalYear'] == transcript_data['year'] and
            meta['quarter'] == transcript_data['quarter']
            for meta in filtered_dates
        )
