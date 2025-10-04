from typing import List
from edgar import Company as EdgarCompany
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

    start_year: int = 2015
    end_year: int = 2026
    forms: List[str] = ["10-K", "10-Q", "8-K", "DEF 14A", "20-F", "6-K"]

    def __init__(self, symbol: str, database: Database):
        self.symbol = symbol
        self.database = database

        self.company = EdgarCompany(cik_or_ticker=self.symbol)

    def sync(self) -> bool:
        """Syncs a company and returns True if synced, and False if not found"""
        if self.exists():
            return True

        if self.company.not_found:
            return False

        return self.upsert()

    def upsert(self) -> bool:
        """Upserts a company into the companies table"""

        company_id = self._upsert_company()
        self._upsert_filings(company_id=company_id)

        return True

    def exists(self) -> bool:
        """Returns True if the company exists in the DB"""
        return len(self.database.table("companies").contains("symbols", self.symbol).limit(1).execute()) > 0

    def _upsert_filings(self, company_id: int):
        """Upserts ALL the filings for that company within a given date range"""
        filings = self._load_filings()

        for filing in filings:
            # NOTE: You will eventually want to add a ThreadPoolExecutor here...
            parser = self._get_filing_parser(filing=filing, company_id=company_id)
            parser.upsert()

    def _upsert_company(self) -> int:
        """Upserts the company object and returns the company serial id"""
        response = self.database.table("companies").upsert({
            "name": self.company.name,
            "symbols": self.company.tickers,
            "exchanges": [exchange.upper() for exchange in self.company.get_exchanges() if
                          self.company.get_exchanges()],
            "cik": self.company.cik,
            "sic": self.company.sic,
            "industry": self.company.industry,
            "fiscal_year_end": self.company.fiscal_year_end
        }, on_conflict="cik").execute()

        return response['data'][0]['id']

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
