import pandas as pd
from datetime import date
from typing import List, Literal, Optional
from pydantic import BaseModel, Field, ValidationError, field_validator

from agent.actions.base_action import BaseAction
from agent.message import Action, Message

AllowedForm = Literal["10-K", "10-Q", "8-K", "DEF 14A", "6-K", "20-F"]


class SearchFilings(BaseModel):
    """List SEC filings that match basic filters (limited to 50 filings)

    - Use this first to identify filings before reading or searching text.
    - Returns a Markdown table with: id, company name, ticker symbol(s), exchange(s), form, items (for 8-K), press release (X),
        report_date, filing_date, fiscal_period, fiscal_year.
    - Results sorted by filing_date in descending order
    - For each form - will return both original submissions and amendments where applicable.
    - The fiscal period (Q1/Q2/Q3/FY) and fiscal year returned in the table are from the companies' own fiscal calendar

    Typical uses:
    - "Find 8-Ks with item 9.01 (i.e. press releases) for AMD in 2024."
    - "Identify the 10K for AAPL's fiscal year 2025"
    """
    symbols: List[str] = Field(..., description="Ticker symbols to include (e.g., ['AAPL','MSFT']).", min_length=1)
    forms: List[AllowedForm] = Field(..., description="Forms to include in the search results. ", min_length=1)
    start_date: str = Field(..., description="Filter by report_date >= this ISO date 'YYYY-MM-DD'.")
    end_date: Optional[str] = Field(..., description="Filter by report_date <= this ISO date 'YYYY-MM-DD'. "
                                                     "Defaults to today.")
    include_items: Optional[List[str]] = Field(
        default=None,
        description="For 8-Ks, optionally require specific items (e.g., ['9.01']). Ignored for other forms."
    )

    @classmethod
    @field_validator("symbols")
    def norm_symbols(cls, v: List[str]) -> List[str]:
        out = [s.strip().upper() for s in v if str(s).strip()]
        return list(set(out))

    @classmethod
    @field_validator("start_date")
    def check_start_date(cls, v: str) -> str:
        _ = date.fromisoformat(v)  # raises if invalid
        return v

    @classmethod
    @field_validator("end_date")
    def check_end_date(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return date.today().isoformat()
        _ = date.fromisoformat(v)
        return v

    @classmethod
    @field_validator("start_date")
    def check_start_date(cls, v: str) -> str:
        _ = date.fromisoformat(v)  # raises if invalid
        return v

    @classmethod
    @field_validator("end_date")
    def check_end_date(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return date.today().isoformat()
        _ = date.fromisoformat(v)
        return v


class SearchFilingsAction(BaseAction):

    name: str = 'SearchFilings'
    schema: SearchFilings

    async def call(self, action: Action):
        """Calls the search filings actions and returns a MD table of """
        try:
            args = self.validate(action)
        except RuntimeError as e:
            return Message(role="tool", status="completed", content=str(e), error=True)

        not_found = self.sync_symbols(symbols=args.symbols)
        if not_found:
            return Message(
                role="tool",
                status="completed",
                content=f"Could not find the following symbols on EDGAR: {sorted(not_found)}",
                error=True,
            )

        companies_rows = (
            self.database
            .table("companies")
            .select("id,name,symbols,exchanges")
            .contains("symbols", args.symbols)
            .execute()
        )

        company_ids = [r["id"] for r in companies_rows]
        company_by_id = {r["id"]: r for r in companies_rows}

        forms = self._expand_forms_with_amendments(args.forms)

        qb = (
            self.database
            .table("filings")
            .select(
                "id,company_id,form,items,press_release,fiscal_year,fiscal_period,filing_date,report_date,accession_number")
            .in_("company_id", company_ids)
            .in_("form", forms)
            .gte("report_date", args.start_date)
            .lte("report_date", args.end_date or date.today().isoformat())
        )

        if args.include_items:
            for item_code in args.include_items:
                qb = qb.contains("items", item_code)

        filings = qb.order("filing_date", desc=True).limit(50).execute()
        if not filings:
            return Message(role="tool", status="completed", content="No filings in the requested range.")

        content = self._format_filings_to_md(filings=filings, company_by_id=company_by_id)

        return Message(
            role="user",
            status="completed",
            content=content
        )

    def validate(self, action: Action) -> SearchFilings:
        """Validates the action against the Pydantic schema"""
        try:
            return SearchFilings(**action.body)

        except ValidationError:
            pass

    @staticmethod
    def _format_filings_to_md(filings: List[dict], company_by_id: dict) -> str:
        """Formats the filings as a Markdown table"""

        def fmt_items(v):
            return ",".join(v) if isinstance(v, list) else ""

        rows = []
        for f in filings:
            c = company_by_id.get(f.get("company_id") or -1, {})
            rows.append({
                "id": f['id'],
                "company": c['name'],
                "symbols": fmt_items(c['symbols']),
                "exchanges": fmt_items(c['exchanges']),
                "form": c['form'],
                "items": c['items'],
                "press_release": "X" if f['press_release'] else "",
                "report_date": f['report_date'],
                "filing_date": f['filing_date'],
                "fiscal_period": f.get("fiscal_period") or " - ",
                "fiscal_year": f.get("fiscal_year") or " - ",
                "accession_no": f['accession_number']
            })

        if not rows:
            return "No filings in the requested range."

        df = pd.DataFrame(rows, columns=[
            "id", "company", "symbols", "exchanges", "form", "items",
            "press_release", "report_date", "filing_date",
            "fiscal_period", "fiscal_year", "accession_no",
        ])

        df = df.sort_values(by="filing_date", ascending=False, kind="stable")

        return df.to_markdown()
