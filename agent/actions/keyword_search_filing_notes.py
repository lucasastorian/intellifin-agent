from typing import List, Literal
from pydantic import BaseModel, Field

AllowedNotesForm = Literal["10-K", "10-Q", "20-F"]


class KeywordSearchFilingNotes(BaseModel):
    """Executes BM-25 Keyword search across the filing notes of one or more filings. Returns top 5 notes.

    - Must be a filing with notes, i.e. annual/quarterly forms: 10-K, 10-Q, 20-F.
    - Filter by filing_ids, which can be identified using the 'SearchFilings' tool
    """
    filing_ids: List[int] = Field(description="Exact filing ids to search within")
    query: str = Field(..., description="Keyword query")
