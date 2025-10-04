from typing import List
from pydantic import BaseModel, Field


class ReadFilingNotes(BaseModel):
    """Read one or more filing notes (across filings if applicable).

    - Get unique Ids from ListFilingNotes.
    - Limited to 5 notes total.
    """
    filing_note_ids: List[int] = Field(..., description="Unique id returned by ListFilingFilings.")
