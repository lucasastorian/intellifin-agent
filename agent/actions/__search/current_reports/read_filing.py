import uuid
from typing import List, Dict, Optional, Literal
from pydantic import BaseModel, Field, create_model

from database import Database
from agent.actions.base_action import BaseAction
from agent.message import Message, Action
from agent.action_response import ActionResponse, ActionFollowUp
from agent.actions.__search.current_reports.curate_read_pages import CurateReadPagesAction


class ReadFilingAction(BaseAction):
    name: str = 'ReadFiling'
    max_pages: int = 20

    def __init__(self, valid_filing_ids: List[int], siblings: Optional[tuple], database: Database,
                 edgar_user_agent: str, start_year: int = 2017):
        super().__init__(database=database, edgar_user_agent=edgar_user_agent, start_year=start_year)
        self.valid_filing_ids = valid_filing_ids
        self.siblings = siblings  # (read_filing, read_attachment, exit_action)

    @property
    def schema(self) -> BaseModel:
        """Returns a dynamically generated schema with valid filing IDs as Literal constraint"""

        filing_ids = tuple(self.valid_filing_ids)

        fields = {
            'thought': (
                str,
                Field(description="Explain what information you're looking for in this filing and why these specific pages")
            ),
            'filing_id': (
                Literal[filing_ids],
                Field(description=f"Unique filing ID from search results. Valid IDs: {list(filing_ids)}")
            ),
            'start_page': (
                int,
                Field(ge=1, description="1-based start page")
            ),
            'end_page': (
                int,
                Field(ge=1, description="1-based end page (inclusive)")
            )
        }

        return create_model(
            'ReadFiling',
            **fields,
            __doc__="""Read a specific page range from a current report filing (up to 20 pages).

- Page numbering starts at 1
- Read strategically: start with first few pages to understand structure
- Then read specific sections as needed
- Call ExitReading when done"""
        )

    async def call(self, action: Action) -> ActionResponse:
        """Reads pages from a filing and returns to reading loop"""
        try:
            args = self.schema(**action.body)
        except Exception as e:
            self.log_start("ReadFiling")
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

        self.log_start("ReadFiling", f"Filing #{args.filing_id}, pages {args.start_page}–{args.end_page}",
                       thought=args.thought)

        # Get max page and metadata using view
        max_page_result = await (
            self.database
            .table("company_filing_pages")
            .select("*")
            .eq("filing_id", args.filing_id)
            .order("page", desc=True)
            .limit(1)
            .execute()
        )

        if not max_page_result.data:
            self.log_error(f"Filing #{args.filing_id} not found or has no pages")
            return ActionResponse(
                message=Message(
                    role="tool",
                    status="completed",
                    content=f"Filing #{args.filing_id} not found or has no pages.",
                    error=True,
                    action_id=action.id
                )
            )

        data = max_page_result.data[0]
        max_page = data["page"]

        # Extract filing and company info from view
        filing = {
            "id": args.filing_id,
            "form": data["form"],
            "filing_date": data["filing_date"],
            "report_date": data.get("report_date"),
            "accession_number": data["accession_number"],
            "fiscal_year": data.get("fiscal_year"),
            "fiscal_period": data.get("fiscal_period"),
            "title": data.get("title")
        }

        company = {
            "name": data["company_name"],
            "symbols": data.get("company_symbols", []),
            "exchanges": data.get("company_exchanges", [])
        }

        start = args.start_page
        end = min(args.end_page, max_page)

        if start > max_page:
            self.log_error(f"Page {start} exceeds max page {max_page}")
            return ActionResponse(
                message=Message(
                    role="tool",
                    status="completed",
                    content=f"Start page {start} exceeds last page {max_page} for filing #{args.filing_id}.",
                    error=True,
                    action_id=action.id
                )
            )

        if (end - start + 1) > self.max_pages:
            end = start + self.max_pages - 1
            self.log_error(f"Range exceeds {self.max_pages} pages, truncating to {start}–{end}")

        # Query pages using view
        pages_result = await (
            self.database
            .table("company_filing_pages")
            .select("page,content")
            .eq("filing_id", args.filing_id)
            .gte("page", start)
            .lte("page", end)
            .order("page", desc=False)
            .execute()
        )

        if not pages_result.data:
            self.log_error(f"No pages in range {start}–{end}")
            return ActionResponse(
                message=Message(
                    role="tool",
                    status="completed",
                    content=f"No pages found for filing #{args.filing_id} in range {start}–{end}.",
                    error=True,
                    action_id=action.id
                )
            )

        # Format output
        header = self._format_header(company, filing, start, end, max_page)
        body = self._join_pages_to_md(pages_result.data)

        output = header + "\n\n" + body
        truncated = False
        if len(output) > 200_000:
            output = header + "\n\n" + body[:200_000] + "\n\n[truncated]"
            truncated = True

        summary = f"Retrieved {len(pages_result.data)} pages: {company.get('name')} {filing.get('form')}"
        if truncated:
            summary += " (truncated)"

        self.log_done(summary, content=content)

        # Create message with ID for curation to reference
        read_message_id = str(uuid.uuid4())
        read_message = Message(
            id=read_message_id,
            role="tool",
            status="completed",
            content=output,
            action_id=action.id
        )

        # Create curation action with pages and siblings
        curate_action = CurateReadPagesAction(
            pages_read=[{'page': p['page'], 'content': p['content']} for p in pages_result.data],
            source_type='filing',
            source_id=args.filing_id,
            read_message_id=read_message_id,
            siblings=self.siblings,
            database=self.database,
            edgar_user_agent=self.edgar_user_agent,
            start_year=self.start_year
        )

        return ActionResponse(
            message=read_message,
            follow_up=ActionFollowUp(
                actions=[curate_action],
                force=True
            )
        )

    @staticmethod
    def _format_header(company: Dict, filing: Dict, start: int, end: int, max_page: int) -> str:
        symbols = ",".join(company.get("symbols") or [])
        exchanges = ",".join(company.get("exchanges") or [])
        fy = filing.get("fiscal_year") or "—"
        fp = filing.get("fiscal_period") or "—"
        title = filing.get("title") or "—"

        return (
            f"### {company.get('name','Unknown')} ({symbols})\n"
            f"**Form:** {filing.get('form')} | **Accession:** {filing.get('accession_number')}\n"
            f"**Title:** {title}\n"
            f"**Report Date:** {filing.get('report_date') or '—'} | **Filing Date:** {filing.get('filing_date')}\n"
            f"**Fiscal:** {fp} {fy} | **Exchanges:** {exchanges}\n"
            f"**Pages:** {start}–{end} of {max_page}"
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
