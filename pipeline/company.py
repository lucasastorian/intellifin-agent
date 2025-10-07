from typing import List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
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

    def sync(self, forms: Optional[List[str]] = None, start_date: Optional[str] = None,
             end_date: Optional[str] = None) -> bool:
        """Sync filings for this company

        Args:
            forms: List of forms to sync (e.g., ['10-K', '10-Q']). Defaults to all forms.
            start_date: Filter filings by report_date >= this date (ISO format 'YYYY-MM-DD')
            end_date: Filter filings by report_date <= this date (ISO format 'YYYY-MM-DD')
        """
        if self.company.not_found:
            return False

        return self.upsert(forms=forms, start_date=start_date, end_date=end_date)

    def upsert(self, forms: Optional[List[str]] = None, start_date: Optional[str] = None,
               end_date: Optional[str] = None) -> bool:
        company_id = self._get_or_create_company_id()
        self._upsert_filings(company_id=company_id, forms=forms, start_date=start_date, end_date=end_date)
        self.on_sync_complete(company_id=company_id)
        return True

    def on_sync_complete(self, company_id: int):
        update_data = {"synced": True}

        # Update fiscal_year_end if available
        if hasattr(self.company, 'fiscal_year_end') and self.company.fiscal_year_end:
            update_data["fiscal_year_end"] = self.company.fiscal_year_end

        self.database.table("companies").update(update_data).eq("id", company_id).execute()

    def _upsert_filings(self, company_id: int, forms: Optional[List[str]] = None,
                        start_date: Optional[str] = None, end_date: Optional[str] = None):
        filings = self._load_filings(forms=forms)

        # Filter filings by date range if specified
        if start_date or end_date:
            filings = self._filter_filings_by_date(filings, start_date, end_date)

        def upsert_filing(filing: EntityFiling):
            # Check if filing is already synced
            if self._is_filing_synced(filing.accession_number):
                return filing.form

            parser = self._get_filing_parser(filing=filing, company_id=company_id)
            parser.upsert()

            # Mark filing as synced
            self._mark_filing_synced(filing.accession_number)

            return filing.form

        with ThreadPoolExecutor(max_workers=9) as executor:
            futures = {executor.submit(upsert_filing, filing): filing for filing in filings}

            for future in as_completed(futures):
                future.result()

    def _get_or_create_company_id(self) -> int:
        response = self.database.table("companies").select("id").contains("symbols", self.symbol).limit(1).execute()

        if not response.data:
            raise ValueError(f"Company with Symbol {self.symbol} not found. Ensure companies are provisioned.")

        return response.data[0]['id']

    def _load_filings(self, forms: Optional[List[str]] = None) -> EntityFilings:
        """Load filings from EDGAR"""
        forms_to_load = forms if forms else self.forms
        return self.company.get_filings(form=forms_to_load, year=list(range(self.start_year, self.end_year)))

    def _filter_filings_by_date(self, filings: EntityFilings, start_date: Optional[str] = None,
                                end_date: Optional[str] = None) -> List[EntityFiling]:
        """Filter filings by report_date range"""
        filtered = []
        start = date.fromisoformat(start_date) if start_date else None
        end = date.fromisoformat(end_date) if end_date else None

        for filing in filings:
            if not filing.report_date:
                raise ValueError(f"Filing {filing.accession_number} ({filing.form}) is missing report_date - cannot filter by date")

            # Convert to date object if it's a string
            if isinstance(filing.report_date, str):
                filing_report_date = date.fromisoformat(filing.report_date)
            else:
                filing_report_date = filing.report_date

            if start and filing_report_date < start:
                continue
            if end and filing_report_date > end:
                continue

            filtered.append(filing)

        return filtered

    def _is_filing_synced(self, accession_number: str) -> bool:
        """Check if filing is already synced"""
        response = self.database.table("filings").select("synced").eq("accession_number", accession_number).limit(1).execute()
        return len(response.data) > 0 and response.data[0].get('synced', False)

    def _mark_filing_synced(self, accession_number: str):
        """Mark filing as synced"""
        self.database.table("filings").update({"synced": True}).eq("accession_number", accession_number).execute()

    def _get_filing_parser(self, filing: EntityFiling, company_id: int) -> BaseFiling:
        if filing.form in ["10-K", "10-K/A"]:
            return FilingTenK(filing=filing, company_id=company_id, database=self.database)

        elif filing.form in ["10-Q", "10-Q/A"]:
            return FilingTenQ(filing=filing, company_id=company_id, database=self.database)

        elif filing.form in ["8-K", "8-K/A"]:
            return FilingEightK(filing=filing, company_id=company_id, database=self.database)

        elif filing.form in ["DEF 14A", "DEF 14A/A"]:
            return FilingDefFourteenA(filing=filing, company_id=company_id, database=self.database)

        elif filing.form in ["6-K", "6-K/A"]:
            return FilingSixK(filing=filing, company_id=company_id, database=self.database)

        elif filing.form in ["20-F", "20-F/A"]:
            return FilingTwentyF(filing=filing, company_id=company_id, database=self.database)

        else:
            raise ValueError(f"Did not recognize form {filing.form}")
