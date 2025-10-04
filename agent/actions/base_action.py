from typing import List, Set
from pydantic import BaseModel
from abc import ABC, abstractmethod

from database.database import Database
from pipeline.company import Company
from agent.message import Action


class BaseAction(ABC):

    name: str
    schema: BaseModel

    def __init__(self, database: Database, edgar_user_agent: str, start_year: int = 2015):
        self.database = database
        self.edgar_user_agent = edgar_user_agent
        self.start_year = start_year

    @abstractmethod
    async def call(self, action: Action):
        """Calls the action with the LLM provided action"""
        raise NotImplementedError

    def sync_symbols(self, symbols: List[str]):
        """Sync the filings for the symbols"""
        not_found = []

        for symbol in symbols:
            company = Company(symbol=symbol, database=self.database, edgar_user_agent=self.edgar_user_agent,
                              start_year=self.start_year)
            sync_successful = company.sync()
            if not sync_successful:
                not_found.append(symbol)

        return not_found

    @staticmethod
    def _expand_forms_with_amendments(forms: List[str]) -> List[str]:
        """Adds form amendments to forms"""
        expanded: Set[str] = set()
        for f in forms:
            expanded.add(f)
            if f in {"10-K", "10-Q", "8-K", "DEF 14A", "6-K", "20-F"}:
                expanded.add(f + "/A")
        return sorted(expanded)
