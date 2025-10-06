from typing import List
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
    forms: List[str] = ["10-K", "10-Q", "8-K", "DEF 14A", "20-F", "6-K"]

    def __init__(self, symbol: str, database: Database, edgar_user_agent: str, start_year: int = 2015,
                 end_year: int = 2026):
        self.symbol = symbol
        self.database = database
        self.start_year = start_year
        self.end_year = end_year

        set_identity(edgar_user_agent)
        self.company = EdgarCompany(cik_or_ticker=self.symbol)

    def sync(self) -> bool:
        """Syncs a company and returns True if synced, and False if not found"""
        if self.exists():
            return True

        if self.company.not_found:
            return False

        return self.upsert()

    def upsert(self) -> bool:
        """Syncs filings for a company (assumes company already provisioned)"""

        company_id = self._get_company_id()
        self._upsert_filings(company_id=company_id)

        self.on_sync_complete(company_id=company_id)

        return True

    def exists(self) -> bool:
        """Returns True if the company exists in the DB and has been synced"""
        return len(
            self.database.table("companies").select("*").contains("symbols", self.symbol).eq("synced", True).limit(
                1).execute()) > 0

    def on_sync_complete(self, company_id: int):
        """Updates the copmany's synced status to true on complete"""
        self.database.table("companies").update({"synced": True}).eq("id", company_id).execute()

    def _upsert_filings(self, company_id: int):
        """Upserts ALL the filings for that company within a given date range"""
        filings = self._load_filings()

        for filing in filings:
            # NOTE: You will eventually want to add a ThreadPoolExecutor here...
            parser = self._get_filing_parser(filing=filing, company_id=company_id)
            parser.upsert()

    def _get_company_id(self) -> int:
        """Gets the company id (assumes company already provisioned)"""
        response = self.database.table("companies").select("id").eq("cik", self.company.cik).limit(1).execute()

        if not response:
            raise ValueError(f"Company with CIK {self.company.cik} not found. Ensure companies are provisioned.")

        return response[0]['id']

    def _load_filings(self) -> EntityFilings:
        """Loads all filings from Edgar"""
        return self.company.get_filings(form=self.forms, year=list(range(self.start_year, self.end_year)))

    def _get_filing_parser(self, filing: EntityFiling, company_id: int) -> BaseFiling:
        """Returns the relevant filing depending on the form"""
        if filing.form in ["10-K", "10-K/A"]:
            # NOTE: Maybe we need an amendment flag here??
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
