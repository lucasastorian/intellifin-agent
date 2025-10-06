from typing import List, Literal, Optional
from pydantic import BaseModel, Field


class KeywordSearchFilings(BaseModel):
    """BM-25 Keyword search across filings, the notes associated with financial statements and attached press releases

    - Specify a company using its ticker symbol
    - Specify the types of filings you are interested in - Annual, Quarterly, Current or All
    - Specify what to search - the a combination of the filings, the notes to financial statements or press releases

    """
    query: str = Field(description="Keyword query")
    symbol: str = Field(description="The ticker symbol of the company to search")
    reports: Literal['annual', 'quarterly', 'current', 'all'] = Field(
        description="Whether to search annual, quarterly, current reports, or ALL three.")
    sources: List[Literal['filings', 'notes', 'press_releases']] = Field(
        description="Specify the types of sources you're interested in searching"
    )
    start_date: str = Field(description="The YYYY-MM-DD filing date for which to start the query")
    end_date: Optional[str] = Field(default=None, description="The optional end date to filter. "
                                                              "If not specified, will search up until today.")
