from typing import List, Literal
from pydantic import BaseModel, Field, field_validator

from agent.actions.base_action import BaseAction


class Search(BaseModel):
    """Search the SEC filings & earnings transcripts of a company


    """
    symbol: str = Field(description="The symbol who's filings / transcripts to search. Ex. 'AAPL'")
    include: List[Literal['AnnualReports', 'QuarterlyReports', 'CurrentReports', 'EarningsTranscripts']] = Field(
        description="The types of reports to search. ")
    query: str = Field(description="Describe what you're looking for")


class SearchAction(BaseAction):
    """Performs a comprehensive search across the filings and earnings transcripts of a company"""
    pass
