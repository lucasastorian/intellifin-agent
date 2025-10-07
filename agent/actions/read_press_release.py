from typing import Dict
from pydantic import BaseModel, Field, ValidationError

from agent.actions.base_action import BaseAction
from agent.message import Action, Message


class ReadPressRelease(BaseModel):
    """Read a page range of a press release using the unique filing_id.

    - Limited to up to 10 pages at a time
    """
    thought: str = Field(
        description="Explain what information you're looking for in this press release and why these specific pages"
    )
    filing_id: int = Field(..., description="Unique id for an 8-K filing with a item 9.01 (press release)")
    start_page: int = Field(description="The start page ", ge=1)
    end_page: int = Field(description="The end page", ge=1)


class ReadPressReleaseAction(BaseAction):
    name: str = "ReadPressRelease"
    schema = ReadPressRelease
    max_pages: int = 10

    async def call(self, action: Action):
        """Reads a press release from an 8-K filing"""
        try:
            args = self.validate(action)
        except RuntimeError as e:
            self.log_start("ReadPressRelease")
            self.log_error(f"Validation failed: {e}")
            return Message(role="tool", status="completed", content=str(e), error=True, action_id=action.id)

        self.log_start("ReadPressRelease", f"Filing #{args.filing_id}, pages {args.start_page}–{args.end_page}", thought=args.thought)

        # Get filing metadata
        filing_result = (
            self.database
            .table("filings")
            .select("id,company_id,form,filing_date,report_date,press_release")
            .eq("id", args.filing_id)
            .limit(1)
            .execute()
        )

        if not filing_result.data:
            self.log_error(f"Filing #{args.filing_id} not found")
            return Message(role="tool", status="completed",
                         content=f"Filing id {args.filing_id} not found.", error=True, action_id=action.id)

        filing = filing_result.data[0]

        if not filing.get('press_release'):
            self.log_error("Filing does not have a press release")
            return Message(role="tool", status="completed",
                         content=f"Filing {args.filing_id} does not contain a press release (Item 9.01).",
                         error=True, action_id=action.id)

        # Get company info
        company_result = (
            self.database
            .table("companies")
            .select("id,name,symbols,exchanges")
            .eq("id", filing["company_id"])
            .limit(1)
            .execute()
        )
        company = company_result.data[0] if company_result.data else {"name": "Unknown", "symbols": [], "exchanges": []}

        # Get press release pages
        max_row_result = (
            self.database
            .table("press_release_pages")
            .select("page")
            .eq("filing_id", args.filing_id)
            .order("page", desc=True)
            .limit(1)
            .execute()
        )

        if not max_row_result.data:
            self.log_error(f"No press release pages for filing #{args.filing_id}")
            return Message(role="tool", status="completed",
                         content=f"No press release pages stored for filing {args.filing_id}.",
                         error=True, action_id=action.id)

        max_page = max_row_result.data[0]["page"]

        start = args.start_page
        end = min(args.end_page, max_page, args.start_page + self.max_pages - 1)

        if start > max_page:
            self.log_error(f"Page {start} exceeds max page {max_page}")
            return Message(role="tool", status="completed",
                         content=f"Start page {start} exceeds last page {max_page}.",
                         error=True, action_id=action.id)

        pages_result = (
            self.database
            .table("press_release_pages")
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
                         content=f"No pages found in range {start}-{end}.",
                         error=True, action_id=action.id)

        header = self._format_header(company, filing, start, end, max_page)
        body = self._join_pages_to_md(pages_result.data)

        output = header + "\n\n" + body
        truncated = False
        if len(output) > 200_000:
            output = header + "\n\n" + body[:200_000] + "\n\n[truncated]"
            truncated = True

        company_name = company.get('name', 'Unknown')
        form = filing.get('form', '?')
        summary = f"Retrieved {len(pages_result.data)} pages: {company_name} {form} press release"
        if truncated:
            summary += " (truncated)"

        self.log_done(summary)

        return Message(role="tool", status="completed", content=output, action_id=action.id)

    @staticmethod
    def validate(action: Action) -> ReadPressRelease:
        """Validates the action against the Pydantic schema"""
        try:
            return ReadPressRelease(**action.body)
        except ValidationError as e:
            raise RuntimeError(f"Validation failed: {e}") from e

    @staticmethod
    def _format_header(company: Dict, filing: Dict, start: int, end: int, max_page: int) -> str:
        """Formats the press release header"""
        symbols = ",".join(company.get("symbols") or [])
        exchanges = ",".join(company.get("exchanges") or [])
        return (
            f"### {company.get('name','Unknown')} ({symbols}) - Press Release\n"
            f"**Form:** {filing.get('form')} | **Filing Date:** {filing.get('filing_date')}\n"
            f"**Report Date:** {filing.get('report_date') or '—'} | **Exchanges:** {exchanges}\n"
            f"**Pages:** {start}–{end} of {max_page}"
        )

    @staticmethod
    def _join_pages_to_md(pages: list) -> str:
        """Joins press release pages into markdown"""
        parts = []
        for row in pages:
            pno = row["page"]
            content = (row.get("content") or "").strip()
            if not content:
                continue
            parts.append(f"\n---\n**Page {pno}**\n\n{content}")
        return "".join(parts) if parts else "_No content in the selected range._"
