from typing import List, Literal
from pydantic import BaseModel, Field

AllowedNotesForm = Literal["10-K", "10-Q", "20-F"]


class KeywordSearchFilingPages(BaseModel):
    """BM-25 Keyword search across the primary document pages of one or more filings. Returns the top 5 matching pages.

    - Will search any filing: 10-Ks/10-Qs/20-Fs, etc. etc.
    - For 8-Ks with attached press releases, this does NOT actually search the press releases.
    """
    filing_ids: List[int] = Field(description="Exact filing ids to search within.")
    query: str = Field(..., description="Keyword query")
