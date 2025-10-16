import os
import sys
import asyncio
import traceback

import tiktoken
from typing import List, Set, Optional
from pydantic import BaseModel
from abc import ABC, abstractmethod

from database.database import Database
from pipeline.company import Company
from agent.message import Action
from agent.action_response import ActionResponse


class BaseAction(ABC):
    """Defines an Action, i.e. tool call an LLM can take"""

    name: str
    schema: BaseModel

    def __init__(self, database: Database, edgar_user_agent: str, start_year: int = 2017, verbose: bool = True):
        self.database = database
        self.edgar_user_agent = edgar_user_agent
        self.start_year = start_year
        self.verbose = verbose

    @abstractmethod
    async def call(self, action: Action) -> ActionResponse:
        """Calls the action with the LLM provided action"""
        raise NotImplementedError

    async def sync_symbols(self, symbols: List[str], forms: Optional[List[str]] = None,
                           start_date: Optional[str] = None, end_date: Optional[str] = None,
                           include_earnings_transcripts: bool = False):
        """Sync the filings for the symbols with optional filtering

        Args:
            symbols: List of ticker symbols to sync
            forms: List of forms to sync (e.g., ['10-K', '10-Q']). Defaults to all forms.
            start_date: Filter filings by report_date >= this date (ISO format 'YYYY-MM-DD')
            end_date: Filter filings by report_date <= this date (ISO format 'YYYY-MM-DD')
            include_earnings_transcripts: Whether to sync earnings transcripts
        """
        not_found = []

        for symbol in symbols:
            company = Company(symbol=symbol, database=self.database, edgar_user_agent=self.edgar_user_agent,
                              start_year=self.start_year, verbose=self.verbose)

            sync_desc = symbol
            if forms or start_date or end_date:
                parts = []
                if forms:
                    parts.append(f"{', '.join(forms)}")
                if start_date or end_date:
                    date_range = f"{start_date or '...'} to {end_date or '...'}"
                    parts.append(f"({date_range})")
                sync_desc += f" [{' '.join(parts)}]"

            try:
                synced_count = await asyncio.wait_for(
                    company.upsert(
                        forms=forms,
                        start_date=start_date,
                        end_date=end_date,
                        include_earnings_transcripts=include_earnings_transcripts
                    ),
                    timeout=600
                )
            except asyncio.TimeoutError:
                if self.verbose:
                    print(f"  {self._c('✗', 'red')} Sync timeout for {symbol}", flush=True)
                not_found.append(symbol)
                continue

            except Exception as e:
                if self.verbose:
                    traceback.print_exc()
                    print(f"  {self._c('✗', 'red')} Sync error for {symbol}: {e}", flush=True)
                not_found.append(symbol)
                continue

            # Verify company exists in DB (synced_count=0 just means no NEW filings, not "not found")
            company_row = await self.database.table("companies").select("id").contains("symbols", symbol).limit(
                1).execute()

            if company_row.data:
                if self.verbose:
                    if synced_count > 0:
                        print(
                            f"  {self._c('⟳', 'yellow')} Syncing {sync_desc} from EDGAR... {self._c('✓', 'green')} {synced_count} new",
                            flush=True)
                    else:
                        print(
                            f"  {self._c('⟳', 'yellow')} Syncing {sync_desc} from EDGAR... {self._c('✓', 'green')} up to date",
                            flush=True)
            else:
                if self.verbose:
                    print(
                        f"  {self._c('⟳', 'yellow')} Syncing {sync_desc} from EDGAR... {self._c('✗', 'red')} Not found",
                        flush=True)
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
        if self.verbose:
            print(f"\n{self._c(action, 'cyan')}", flush=True)
            if thought:
                print(f"  {self._c('💭', 'magenta')} {thought}", flush=True)
            if params:
                print(f"  {self._c('→', 'dim')} {params}", flush=True)

    def log_done(self, result: str, content: str):
        """Log successful completion with result summary, including token count of content when verbose"""
        if self.verbose:
            try:
                tokens = self.num_tokens(content or "")
            except Exception:
                tokens = 0
            print(f"  {self._c('✓', 'green')} {result} | tokens={tokens}", flush=True)

    def log_error(self, error: str = ""):
        """Log error with message"""
        if self.verbose:
            print(f"  {self._c('✗', 'red')} {error}", flush=True)

    @property
    def openai_legacy_schema(self) -> dict:
        """Converts the schema to an OpenAI Chat Completions tool call format"""
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

    @property
    def openai_schema(self) -> dict:
        """Converts the schema to an OpenAI Responses API tool call format"""
        json_schema = self.schema.model_json_schema(mode="serialization")

        return {
            "type": "function",
            "name": self.schema.__name__,
            "description": json_schema['description'],
            "parameters": {
                "type": "object",
                "properties": json_schema['properties'],
                "required": json_schema.get('required', [])
            }
        }

    @property
    def legacy_openai_schema(self) -> dict:
        """Returns the legacy OpenAI completions API tool call format (still used by XAI)"""
        json_schema = self.schema.model_json_schema(mode="serialization")
        return {
            "type": "function",
            "function": {
                "name": json_schema['title'],
                "description": json_schema['description'],
                "parameters": {
                    "type": "object",
                    "properties": json_schema['properties'],
                    "required": json_schema.get('required', [])
                },
            }
        }

    @property
    def anthropic_schema(self) -> dict:
        """Converts the Action to an Anthropic compatible schema"""
        json_schema = self.schema.model_json_schema(mode="serialization")

        return {
            "name": self.schema.__name__,
            "description": json_schema['description'],
            "input_schema": {
                "type": "object",
                "properties": json_schema['properties'],
                "required": json_schema.get('required', [])
            }
        }

    @staticmethod
    def num_tokens(content: str) -> int:
        encoding = tiktoken.encoding_for_model("gpt-3.5-turbo")
        return len(encoding.encode(content))
