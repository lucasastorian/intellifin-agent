from pydantic import BaseModel, Field


class ReadFiling(BaseModel):
    """Read a specific page range from a filing (up to 20 pages).

    - Cover page of filing is considered page #1
    - EDGAR’s internal page references may offset by a few pages
    - Adjust ranges as needed (e.g., read ToC first, then jump to the target section).

    Strategy:
    - Don’t try to read the entire filings in one call. Generally read ToC first (e.g., first 2–3 pages),
      then fetch the specific section you need.
    """
    filing_id: int = Field(..., description="Unique filing id returned by SearchFilings.")
    start_page: int = Field(..., ge=1, description="1-based start page.")
    end_page: int = Field(..., ge=1, description="1-based end page (inclusive).")
