from typing import List, Literal, Optional
from pydantic import BaseModel, Field

AllowedForm = Literal["10-K", "10-Q", "8-K", "DEF 14A", "6-K", "20-F"]


class SearchFilings(BaseModel):
    """List SEC filings that match basic filters (limited to 50 filings)

    - Use this first to identify filings before reading or searching text.
    - Returns a markdown table with: id, company name, ticker symbol(s), exchange(s), form, items (for 8-K), press release (X),
        report_date, filing_date, fiscal_period, fiscal_year.
    - Results sorted by filing_date in descending order
    - For each form - will return both original submissions and amendments where applicable.
    - The fiscal period (Q1/Q2/Q3/FY) and fiscal year returned in the table are from the companies' own fiscal calendar

    Typical uses:
    - "Find 8-Ks with item 9.01 (i.e. press releases) for AMD in 2024."
    - "Identify the 10K for AAPL's fiscal year 2025"
    """
    symbols: List[str] = Field(..., description="Ticker symbols to include (e.g., ['AAPL','MSFT']).")
    forms: List[AllowedForm] = Field(..., description="Forms to include in the search results. ")
    start_date: str = Field(..., description="Filter by report_date >= this ISO date 'YYYY-MM-DD'.")
    end_date: Optional[str] = Field(..., description="Filter by report_date <= this ISO date 'YYYY-MM-DD'. "
                                                     "Defaults to today.")
    include_items: Optional[List[str]] = Field(
        default=None,
        description="For 8-Ks, optionally require specific items (e.g., ['9.01']). Ignored for other forms."
    )
