import asyncio
from typing import List, Optional
from datetime import date
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


class Company:
    forms: List[str] = ["10-K", "10-Q", "8-K",
                        "DEF 14A",
                        "20-F", "6-K"]

    def __init__(self, symbol: str, database: Database, edgar_user_agent: str, start_year: int = 2015,
                 end_year: int = 2026):
        self.symbol = symbol
        self.database = database
        self.start_year = start_year
        self.end_year = end_year

        set_identity(edgar_user_agent)
        self.company = EdgarCompany(cik_or_ticker=self.symbol)

    async def sync(self, forms: Optional[List[str]] = None, start_date: Optional[str] = None,
                   end_date: Optional[str] = None) -> int:
        if self.company.not_found:
            return 0

        return await self.upsert(forms=forms, start_date=start_date, end_date=end_date)

    async def upsert(self, forms: Optional[List[str]] = None, start_date: Optional[str] = None,
                     end_date: Optional[str] = None) -> int:
        company = self._get_company()

        if company is None:
            return 0

        synced_count = await self._upsert_filings(company=company, forms=forms, start_date=start_date,
                                                   end_date=end_date)
        self.on_sync_complete(company_id=company['id'])

        return synced_count

    def on_sync_complete(self, company_id: int):
        update_data = {"synced": True}

        if self.company.fiscal_year_end:
            update_data["fiscal_year_end"] = self.company.fiscal_year_end

        self.database.table("companies").update(update_data).eq("id", company_id).execute()

    async def _upsert_filings(self, company: dict, forms: Optional[List[str]] = None,
                              start_date: Optional[str] = None, end_date: Optional[str] = None) -> int:
        filings = await asyncio.to_thread(self._load_filings, forms=forms)

        if start_date or end_date:
            filings = self._filter_filings_by_date(filings, start_date, end_date)

        def upsert_filing_sync(filing: EntityFiling):
            """Synchronous wrapper for filing upsert - runs in thread"""
            if self._is_filing_synced(filing.accession_number):
                return None

            parser = self._get_filing_parser(filing=filing, company=company)
            parser.upsert()

            self._mark_filing_synced(filing.accession_number)

            return filing.form

        # Process all filings concurrently in separate threads
        results = await asyncio.gather(
            *[asyncio.to_thread(upsert_filing_sync, filing) for filing in filings]
        )

        synced_count = sum(1 for result in results if result is not None and not isinstance(result, Exception))

        return synced_count

    def _get_company(self) -> Optional[dict]:
        response = self.database.table("companies").select("id").contains("symbols", self.symbol).limit(1).execute()

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

    def _is_filing_synced(self, accession_number: str) -> bool:
        """Check if filing is already synced"""
        response = self.database.table("filings").select("synced").eq("accession_number", accession_number).limit(
            1).execute()
        return len(response.data) > 0 and response.data[0].get('synced', False)

    def _mark_filing_synced(self, accession_number: str):
        """Mark filing as synced"""
        self.database.table("filings").update({"synced": True}).eq("accession_number", accession_number).execute()

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
