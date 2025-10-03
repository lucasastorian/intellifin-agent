from typing import List, Literal
from pydantic import BaseModel, Field

AllowedNotesForm = Literal["10-K", "10-Q", "20-F"]


class KeywordSearchPressReleases(BaseModel):
    """BM-25 Keyword search across the press releases of one or more 9.01 schedule 8-Ks. Returns top 5 results.

    - Will search ONLY the attached press releases of 8-Ks with item 9.01
    """
    filing_ids: List[int] = Field(description="Exact filing ids to search within.")
    query: str = Field(..., description="Keyword query")
