from typing import Literal, List
from pydantic import BaseModel, Field


class ViewFinancialStatements(BaseModel):
    """Merges & formats the financial statements for a single company - potentially across filings - as a single table

    - Can visualize up to 6 periods (quarters or years depending on frequency) in a single table
    - Filter by report date. Use SearchFilings to figure out the fiscal period/year of a given report date range ahead of time
    - Use include segments ONLY if you want full product/services/geographic segment breakdowns included in statement (relevant for the income statement in particular)

    """
    symbol: str = Field(description="The symbol of the company")
    statement: Literal['income_statement', 'balance_sheet', 'cash_flow_statement'] = Field(
        description="The statement which to show")
    frequency: Literal['annual', 'quarterly'] = Field(description="Whether to show annual or quarterly financials")
    include_segments: bool = Field(default=False, description="Whether to include segment breakdowns in the financial "
                                                              "statement")
    start_date: str = Field(description="The starting report date")
    end_date: str = Field(description="The ending report date")
