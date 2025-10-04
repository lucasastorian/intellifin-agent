from typing import List, Set
from abc import ABC, abstractmethod

from database.database import Database
from pipeline.company import Company
from agent.message import Action


class BaseAction(ABC):

    def __init__(self, database: Database):
        self.database = database

    @abstractmethod
    async def call(self, action: Action):
        """Calls the action with the LLM provided action"""
        raise NotImplementedError

    def sync_symbols(self, symbols: List[str]):
        """Sync the filings for the symbols"""
        not_found = []

        for symbol in symbols:
            company = Company(symbol=symbol, database=self.database)
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
