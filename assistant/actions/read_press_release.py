from typing import List
from pydantic import BaseModel, Field


class ReadPressRelease(BaseModel):
    """Read a page range of a press release using the unique filing_id.

    - Limited to up to 10 pages at a time
    """
    filing_id: int = Field(..., description="Unique id for an 8-K filing with a item 9.01 (press release)")
    start_page: int = Field(description="The start page ", ge=1)
    end_page: int = Field(description="The end page", ge=1)
