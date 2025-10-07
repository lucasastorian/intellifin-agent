from typing import List, Dict
from pydantic import BaseModel, Field
from agent.actions.base_action import BaseAction
from agent.message import Action, Message


class ReadFiling(BaseModel):
    """Read a specific page range from a filing (up to 20 pages).

    - Cover page of filing is considered page #1
    - EDGAR's internal page references may offset by a few pages
    - Adjust ranges as needed (e.g., read ToC first, then jump to the target section).

    Strategy:
    - Don't try to read the entire filings in one call. Generally read ToC first (e.g., first 2–3 pages),
      then fetch the specific section you need.
    """
    thought: str = Field(
        description="Explain what section or information you're looking for in this filing and why these specific pages"
    )
    filing_id: int = Field(..., description="Unique filing id returned by SearchFilings.")
    start_page: int = Field(..., ge=1, description="1-based start page.")
    end_page: int = Field(..., ge=1, description="1-based end page (inclusive).")


class ReadFilingAction(BaseAction):

    name: str = "ReadFiling"
    schema = ReadFiling
    max_pages: int = 20

    async def call(self, action: Action):
        try:
            args = self.validate(action)

        except RuntimeError as e:
            self.log_start("ReadFiling")
            self.log_error(f"Validation failed: {e}")
            return Message(role="tool", status="completed", content=str(e), error=True, action_id=action.id)

        self.log_start("ReadFiling", f"Filing #{args.filing_id}, pages {args.start_page}–{args.end_page}", thought=args.thought)

        filing_result = (
            self.database
            .table("filings")
            .select("id,company_id,form,filing_date,report_date,accession_number,fiscal_year,fiscal_period")
            .eq("id", args.filing_id)
            .limit(1)
            .execute()
        )

        if not filing_result.data:
            self.log_error(f"Filing #{args.filing_id} not found")
            return Message(role="tool", status="completed",
                           content=f"Filing id {args.filing_id} not found.", error=True, action_id=action.id)
        filing = filing_result.data[0]

        company_result = (
            self.database
            .table("companies")
            .select("id,name,symbols,exchanges")
            .eq("id", filing["company_id"])
            .limit(1)
            .execute()
        )
        company = company_result.data[0] if company_result.data else {"name": "Unknown", "symbols": [], "exchanges": []}

        max_row_result = (
            self.database
            .table("filing_pages")
            .select("page")
            .eq("filing_id", args.filing_id)
            .order("page", desc=True)
            .limit(1)
            .execute()
        )
        if not max_row_result.data:
            self.log_error(f"No pages stored for filing #{args.filing_id}")
            return Message(role="tool", status="completed",
                           content=f"No pages stored for filing {args.filing_id}.", error=True, action_id=action.id)
        max_page = max_row_result.data[0]["page"]

        start = args.start_page
        end = min(args.end_page, max_page)

        if start > max_page:
            self.log_error(f"Page {start} exceeds max page {max_page}")
            return Message(role="tool", status="completed",
                           content=f"Start page {start} exceeds last page {max_page} for filing {args.filing_id}.",
                           error=True, action_id=action.id)

        pages_result = (
            self.database
            .table("filing_pages")
            .select("page,content")
            .eq("filing_id", args.filing_id)
            .gte("page", start)
            .lte("page", end)
            .order("page", desc=False)
            .execute()
        )
        if not pages_result.data:
            self.log_error(f"No pages in range {start}–{end}")
            return Message(role="tool", status="completed",
                           content=f"No pages found for filing {args.filing_id} in range {start}-{end}.",
                           error=True, action_id=action.id)

        header = self._format_header(company, filing, start, end, max_page)
        body = self._join_pages_to_md(pages_result.data)

        output = header + "\n\n" + body
        truncated = False
        if len(output) > 200_000:
            output = header + "\n\n" + body[:200_000] + "\n\n[truncated]"
            truncated = True

        # Build result summary
        company_name = company.get('name', 'Unknown')
        form = filing.get('form', '?')
        summary = f"Retrieved {len(pages_result.data)} pages: {company_name} {form}"
        if truncated:
            summary += " (truncated)"

        self.log_done(summary)

        return Message(role="tool", status="completed", content=output, action_id=action.id)

    def validate(self, action: Action) -> ReadFiling:
        try:
            return ReadFiling(**action.body)
        except Exception as e:
            raise RuntimeError(f"Validation failed for ReadFiling: {e}") from e

    @staticmethod
    def _format_header(company: Dict, filing: Dict, start: int, end: int, max_page: int) -> str:
        symbols = ",".join(company.get("symbols") or [])
        exchanges = ",".join(company.get("exchanges") or [])
        fy = filing.get("fiscal_year") or "—"
        fp = filing.get("fiscal_period") or "—"
        return (
            f"### {company.get('name','Unknown')} ({symbols})\n"
            f"**Form:** {filing.get('form')} | **Accession:** {filing.get('accession_number')}\n"
            f"**Report Date:** {filing.get('report_date') or '—'} | **Filing Date:** {filing.get('filing_date')}\n"
            f"**Fiscal:** {fp} {fy} | **Exchanges:** {exchanges}\n"
            f"**Pages:** {start}–{end} of {max_page} | **Source:** filing_pages"
        )

    @staticmethod
    def _join_pages_to_md(pages: List[Dict]) -> str:
        parts: List[str] = []
        for row in pages:
            pno = row["page"]
            content = (row.get("content") or "").strip()
            if not content:
                continue
            parts.append(f"\n---\n**Page {pno}**\n\n{content}")
        return "".join(parts) if parts else "_No content in the selected range._"

