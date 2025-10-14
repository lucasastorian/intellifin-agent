import pandas as pd
from datetime import date
from typing import List, Literal, Optional
from pydantic import BaseModel, Field, ValidationError, field_validator

from agent.actions.base_action import BaseAction
from agent.message import Action, Message
from agent.action_response import ActionResponse

AllowedForm = Literal["10-K", "10-Q", "8-K", "DEF 14A", "6-K", "20-F"]


class ListFilings(BaseModel):
    """List available SEC filings based on ticker symbol, forms, and date range (limited to 50 filings)

    - Filters by filing_date (SEC submission date), not report_date (fiscal period end)
    - Returns a Markdown table with: id, company, symbols, exchanges, form, title (for 8-K/6-K), items (for 8-K),
      press_release indicator, pages, attachments count, report_date, filing_date, fiscal_period, fiscal_year
    - Results sorted by filing_date in descending order
    - Returns both original submissions and amendments where applicable
    - Use ReadFiling action with the filing ID to explore attachments, notes, and detailed content
    """
    symbols: List[str] = Field(..., description="Ticker symbols to include (e.g., ['AAPL','MSFT']).", min_length=1)
    forms: List[AllowedForm] = Field(..., description="Forms to include in the search results. ", min_length=1)
    start_date: str = Field(..., description="Filter by filing_date (SEC submission date) >= this ISO date 'YYYY-MM-DD'.")
    end_date: Optional[str] = Field(...,
                                    description="Filter by filing_date (SEC submission date) <= this ISO date 'YYYY-MM-DD'. "
                                                "Defaults to today.")

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


class ListFilingsAction(BaseAction):
    name: str = 'ListFilings'
    schema = ListFilings

    async def call(self, action: Action):
        """Calls the search filings actions and returns a MD table of """
        try:
            args = ListFilings(**action.body)
        except ValidationError as e:
            self.log_start("ListFilings")
            self.log_error(f"Validation failed: {e}")
            return ActionResponse(
                message=Message(
                    role="tool",
                    status="completed",
                    content=str(e),
                    error=True,
                    action_id=action.id
                )
            )

        params = f"{', '.join(args.symbols)} ({', '.join(args.forms)}), {args.start_date} → {args.end_date or ''}"

        self.log_start("ListFilings", params=params)

        not_found = await self.sync_symbols(symbols=args.symbols, forms=args.forms,
                                            start_date=args.start_date, end_date=args.end_date)
        if not_found:
            self.log_error(f"Symbols not found: {', '.join(sorted(not_found))}")
            return ActionResponse(
                message=Message(
                    role="tool",
                    status="completed",
                    content=f"Could not find the following symbols on EDGAR: {sorted(not_found)}",
                    error=True,
                    action_id=action.id
                )
            )

        forms = self._expand_forms_with_amendments(args.forms)

        qb = (
            self.database
            .table("company_filings")
            .select(
                "id,company_id,company_name,company_symbols,company_exchanges,company_delisted,form,title,items,press_release,"
                "fiscal_year,fiscal_period,filing_date,report_date,accession_number,num_pages,num_attachments")
            .contains("company_symbols", args.symbols)
            .in_("form", forms)
            .gte("filing_date", args.start_date)
            .lte("filing_date", args.end_date or date.today().isoformat())
        )

        filings_result = await qb.order("filing_date", desc=True).limit(50).execute()

        if not filings_result.data:
            self.log_done("No filings found")
            return ActionResponse(
                message=Message(
                    role="tool",
                    status="completed",
                    content="No filings in the requested range.",
                    action_id=action.id
                )
            )

        company_ids = {f['company_id'] for f in filings_result.data if f.get('company_id')}
        company_info = {}
        if company_ids:
            companies_result = await (
                self.database
                .table("companies")
                .select("id,name,symbols,sector,industry,fiscal_year_end,delisted")
                .in_("id", list(company_ids))
                .execute()
            )
            company_info = {c['id']: c for c in companies_result.data}

        content = self._format_filings_to_md(
            filings=filings_result.data,
            company_info=company_info
        )

        companies = {f['company_name'] for f in filings_result.data if f.get('company_name')}
        forms = {f['form'] for f in filings_result.data}
        summary = f"Found {len(filings_result.data)} filings: {', '.join(sorted(forms))}"
        if len(companies) <= 3:
            summary += f" ({', '.join(sorted(companies))})"

        self.log_done(summary)

        return ActionResponse(
            message=Message(
                role="tool",
                status="completed",
                content=content,
                action_id=action.id
            )
        )

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
                delisted = info.get('delisted', False)

                delisted_tag = " **[DELISTED]**" if delisted else ""
                header += f"**{name}** ({symbols}){delisted_tag}\n"
                header += f"Sector: {sector} | Industry: {industry} | Fiscal Year End: {fiscal_year_end}\n\n"

        # Build table rows
        rows = []
        for f in filings:
            filing_title = f.get('title') if f['form'] in ['8-K', '8-K/A', '6-K', '6-K/A'] else '-'
            rows.append({
                "id": f['id'],
                "company": f.get('company_name', ''),
                "symbols": fmt_items(f.get('company_symbols', [])),
                "exchanges": fmt_items(f.get('company_exchanges', [])),
                "form": f['form'],
                "title": filing_title or '-',
                "items": fmt_items(f.get('items', [])),
                "press_release": "X" if f.get('press_release') else "",
                "pages": f.get('num_pages') or "",
                "attachments": f.get('num_attachments') or "",
                "report_date": f['report_date'],
                "filing_date": f['filing_date'],
                "fiscal_period": f.get("fiscal_period") or "-",
                "fiscal_year": f.get("fiscal_year") or "-",
                "accession_no": f['accession_number']
            })

        if not rows:
            return "No filings in the requested range."

        df = pd.DataFrame(rows, columns=[
            "id", "company", "symbols", "exchanges", "form", "title", "items",
            "press_release", "pages", "attachments", "report_date", "filing_date",
            "fiscal_period", "fiscal_year", "accession_no",
        ])

        df = df.sort_values(by="filing_date", ascending=False, kind="stable")

        return header + df.to_markdown(index=False)
