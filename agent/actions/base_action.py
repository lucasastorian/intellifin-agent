import os
import sys
from typing import List, Set, Optional
from pydantic import BaseModel
from abc import ABC, abstractmethod

from database.database import Database
from pipeline.company import Company
from agent.message import Action


class BaseAction(ABC):
    name: str
    schema: BaseModel

    def __init__(self, database: Database, edgar_user_agent: str, start_year: int = 2017):
        self.database = database
        self.edgar_user_agent = edgar_user_agent
        self.start_year = start_year

    @abstractmethod
    async def call(self, action: Action):
        """Calls the action with the LLM provided action"""
        raise NotImplementedError

    def sync_symbols(self, symbols: List[str], forms: Optional[List[str]] = None,
                     start_date: Optional[str] = None, end_date: Optional[str] = None):
        """Sync the filings for the symbols with optional filtering

        Args:
            symbols: List of ticker symbols to sync
            forms: List of forms to sync (e.g., ['10-K', '10-Q']). Defaults to all forms.
            start_date: Filter filings by report_date >= this date (ISO format 'YYYY-MM-DD')
            end_date: Filter filings by report_date <= this date (ISO format 'YYYY-MM-DD')
        """
        not_found = []

        for symbol in symbols:
            company = Company(symbol=symbol, database=self.database, edgar_user_agent=self.edgar_user_agent,
                              start_year=self.start_year)

            # Build sync description
            sync_desc = symbol
            if forms or start_date or end_date:
                parts = []
                if forms:
                    parts.append(f"{', '.join(forms)}")
                if start_date or end_date:
                    date_range = f"{start_date or '...'} to {end_date or '...'}"
                    parts.append(f"({date_range})")
                sync_desc += f" [{' '.join(parts)}]"

            synced_count = company.sync(forms=forms, start_date=start_date, end_date=end_date)

            # Only show sync message if filings were actually synced
            if synced_count > 0:
                print(f"  {self._c('⟳', 'yellow')} Syncing {sync_desc} from EDGAR... {self._c('✓', 'green')}", flush=True)
            elif synced_count == 0 and not company.company.not_found:
                # Company found but nothing to sync (all already synced)
                pass  # No message
            else:
                # Company not found
                print(f"  {self._c('⟳', 'yellow')} Syncing {sync_desc} from EDGAR... {self._c('✗', 'red')} Not found", flush=True)
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

    @property
    def _tty(self) -> bool:
        try:
            return sys.stdout.isatty() and (os.getenv("TERM") not in (None, "dumb"))
        except Exception:
            return False

    def _c(self, text: str, color: str) -> str:
        if not self._tty:
            return text
        colors = {
            "cyan": "\033[36m", "green": "\033[32m",
            "yellow": "\033[33m", "magenta": "\033[35m",
            "red": "\033[31m", "dim": "\033[2m",
            "reset": "\033[0m"
        }
        return f"{colors.get(color, '')}{text}{colors['reset']}"

    def log_start(self, action: str, params: str = "", thought: str = ""):
        """Log action start with name and parameters"""
        print(f"\n{self._c(action, 'cyan')}", flush=True)
        if thought:
            print(f"  {self._c('💭', 'magenta')} {thought}", flush=True)
        if params:
            print(f"  {self._c('→', 'dim')} {params}", flush=True)

    def log_done(self, result: str = ""):
        """Log successful completion with result summary"""
        print(f"  {self._c('✓', 'green')} {result}", flush=True)

    def log_error(self, error: str = ""):
        """Log error with message"""
        print(f"  {self._c('✗', 'red')} {error}", flush=True)

    @property
    def openai_schema(self) -> dict:
        """Converts the schema to an OpenAI compatible tool call format"""
        json_schema = self.schema.model_json_schema(mode="serialization")

        return {
            "type": "function",
            "function": {
                "name": self.schema.__name__,
                "description": json_schema['description'],
                "parameters": {
                    "type": "object",
                    "properties": json_schema['properties'],
                    "required": json_schema.get('required', [])
                }
            }
        }
