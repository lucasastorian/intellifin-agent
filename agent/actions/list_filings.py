import pandas as pd
from datetime import date
from typing import List, Literal, Optional
from pydantic import BaseModel, Field, ValidationError, field_validator

from agent.actions.base_action import BaseAction
from agent.message import Action, Message

AllowedForm = Literal["10-K", "10-Q", "8-K", "DEF 14A", "6-K", "20-F"]


class ListFilings(BaseModel):
    """List available SEC filings based on the ticker symbol, forms, and start date (limited to 50 filings)

    - Use this first to identify filings before reading or searching text via SearchContent
    - Returns a Markdown table with: id, company name, ticker symbol(s), exchange(s), form, items (for 8-K), press release (X),
        pages, attachments, report_date, filing_date, fiscal_period, fiscal_year.
    - Results sorted by filing_date in descending order
    - For each form - will return both original submissions and amendments where applicable.
    - The fiscal period (Q1/Q2/Q3/FY) and fiscal year returned in the table are from the companies' own fiscal calendar
    """
    thought: str = Field(
        description="Describe what you're searching for and how it will help you achieve your objective"
    )
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


class ListFilingsAction(BaseAction):
    name: str = 'ListFilings'
    schema = ListFilings

    async def call(self, action: Action):
        """Calls the search filings actions and returns a MD table of """
        try:
            args = self.validate(action)
        except RuntimeError as e:
            self.log_start("ListFilings")
            self.log_error(f"Validation failed: {e}")
            return Message(role="tool", status="completed", content=str(e), error=True, action_id=action.id)

        params = f"{', '.join(args.symbols)} ({', '.join(args.forms)}), {args.start_date} → {args.end_date or ''}"
        if args.include_items:
            params += f", items={','.join(args.include_items)}"

        self.log_start("ListFilings", params=params, thought=args.thought)

        not_found = self.sync_symbols(symbols=args.symbols, forms=args.forms,
                                       start_date=args.start_date, end_date=args.end_date)
        if not_found:
            self.log_error(f"Symbols not found: {', '.join(sorted(not_found))}")
            return Message(
                role="tool",
                status="completed",
                content=f"Could not find the following symbols on EDGAR: {sorted(not_found)}",
                error=True,
                action_id=action.id
            )

        forms = self._expand_forms_with_amendments(args.forms)

        # Query the company_filings view directly
        qb = (
            self.database
            .table("company_filings")
            .select(
                "id,company_id,company_name,company_symbols,company_exchanges,form,items,press_release,"
                "fiscal_year,fiscal_period,filing_date,report_date,accession_number,num_pages,num_attachments")
            .contains("company_symbols", args.symbols)
            .in_("form", forms)
            .gte("report_date", args.start_date)
            .lte("report_date", args.end_date or date.today().isoformat())
        )

        if args.include_items:
            for item_code in args.include_items:
                qb = qb.contains("items", item_code)

        filings_result = qb.order("report_date", desc=True).limit(50).execute()

        if not filings_result.data:
            self.log_done("No filings found")
            return Message(role="tool", status="completed", content="No filings in the requested range.",
                           action_id=action.id)

        # Get company information for the header
        company_ids = {f['company_id'] for f in filings_result.data if f.get('company_id')}
        company_info = {}
        if company_ids:
            companies_result = (
                self.database
                .table("companies")
                .select("id,name,symbols,sector,industry,fiscal_year_end")
                .in_("id", list(company_ids))
                .execute()
            )
            company_info = {c['id']: c for c in companies_result.data}

        content = self._format_filings_to_md(filings=filings_result.data, company_info=company_info)

        # Build result summary
        companies = {f['company_name'] for f in filings_result.data if f.get('company_name')}
        forms = {f['form'] for f in filings_result.data}
        summary = f"Found {len(filings_result.data)} filings: {', '.join(sorted(forms))}"
        if len(companies) <= 3:
            summary += f" ({', '.join(sorted(companies))})"

        self.log_done(summary)

        return Message(
            role="tool",
            status="completed",
            content=content,
            action_id=action.id
        )

    def validate(self, action: Action) -> ListFilings:
        """Validates the action against the Pydantic schema"""
        try:
            return ListFilings(**action.body)

        except ValidationError:
            pass

    @staticmethod
    def _format_filings_to_md(filings: List[dict], company_info: dict = None) -> str:
        """Formats the filings as a Markdown table with optional company header"""

        def fmt_items(v):
            return ",".join(v) if isinstance(v, list) else ""

        def fmt_fiscal_year_end(fye):
            """Format fiscal year end (MMDD format) to human-readable"""
            if not fye or fye == 'N/A':
                return 'N/A'
            try:
                # Handle MMDD format (e.g., "1231" -> "December 31")
                if len(fye) == 4 and fye.isdigit():
                    month = int(fye[:2])
                    day = int(fye[2:])
                    month_names = ['', 'January', 'February', 'March', 'April', 'May', 'June',
                                   'July', 'August', 'September', 'October', 'November', 'December']
                    return f"{month_names[month]} {day}"
                return fye
            except (ValueError, IndexError):
                return fye

        # Build company header if we have company info
        header = ""
        if company_info:
            for company_id, info in company_info.items():
                symbols = fmt_items(info.get('symbols', []))
                name = info.get('name', 'N/A')
                sector = info.get('sector') or 'N/A'
                industry = info.get('industry') or 'N/A'
                fiscal_year_end = fmt_fiscal_year_end(info.get('fiscal_year_end'))

                header += f"**{name}** ({symbols})\n"
                header += f"Sector: {sector} | Industry: {industry} | Fiscal Year End: {fiscal_year_end}\n\n"

        rows = []
        for f in filings:
            rows.append({
                "id": f['id'],
                "company": f.get('company_name', ''),
                "symbols": fmt_items(f.get('company_symbols', [])),
                "exchanges": fmt_items(f.get('company_exchanges', [])),
                "form": f['form'],
                "items": f['items'],
                "press_release": "X" if f['press_release'] else "",
                "pages": f.get('num_pages') or "",
                "attachments": f.get('num_attachments') or "",
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
            "press_release", "pages", "attachments", "report_date", "filing_date",
            "fiscal_period", "fiscal_year", "accession_no",
        ])

        df = df.sort_values(by="filing_date", ascending=False, kind="stable")

        return header + df.to_markdown()
