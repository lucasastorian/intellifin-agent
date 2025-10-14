import uuid
from typing import List, Dict, Optional, Literal
from pydantic import BaseModel, Field, create_model

from database import Database
from agent.actions.base_action import BaseAction
from agent.message import Message, Action
from agent.action_response import ActionResponse, ActionFollowUp
from agent.actions.__search.current_reports.curate_read_pages import CurateReadPagesAction


class ReadAttachmentAction(BaseAction):
    name: str = 'ReadAttachment'
    max_pages: int = 20

    def __init__(self, valid_attachment_ids: List[int], siblings: Optional[tuple], database: Database,
                 edgar_user_agent: str, start_year: int = 2017):
        super().__init__(database=database, edgar_user_agent=edgar_user_agent, start_year=start_year)
        self.valid_attachment_ids = valid_attachment_ids
        self.siblings = siblings  # (read_filing, read_attachment, exit_action)

    @property
    def schema(self) -> BaseModel:
        """Returns a dynamically generated schema with valid attachment IDs as Literal constraint"""

        attachment_ids = tuple(self.valid_attachment_ids)

        fields = {
            'thought': (
                str,
                Field(description="Explain what information you're looking for in this attachment and why these specific pages")
            ),
            'attachment_id': (
                Literal[attachment_ids],
                Field(description=f"Unique attachment ID from search results. Valid IDs: {list(attachment_ids)}")
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
            'ReadAttachment',
            **fields,
            __doc__="""Read a specific page range from an attachment/exhibit (up to 20 pages).

- Page numbering starts at 1 for each attachment
- Start with first few pages to understand structure
- Then read specific sections as needed
- Call ExitReading when done"""
        )

    async def call(self, action: Action) -> ActionResponse:
        """Reads pages from an attachment and returns to reading loop"""
        try:
            args = self.schema(**action.body)
        except Exception as e:
            self.log_start("ReadAttachment")
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

        self.log_start("ReadAttachment", f"Attachment #{args.attachment_id}, pages {args.start_page}–{args.end_page}",
                       thought=args.thought)

        # Get attachment + filing + company metadata in one query using view
        result = await (
            self.database
            .table("company_filing_attachments")
            .select("*")
            .eq("id", args.attachment_id)
            .limit(1)
            .execute()
        )

        if not result.data:
            self.log_error(f"Attachment #{args.attachment_id} not found")
            return ActionResponse(
                message=Message(
                    role="tool",
                    status="completed",
                    content=f"Attachment #{args.attachment_id} not found in database.",
                    error=True,
                    action_id=action.id
                )
            )

        data = result.data[0]

        # Extract attachment, filing, and company info from view
        attachment = {
            "id": data["id"],
            "exhibit_number": data["exhibit_number"],
            "filename": data["filename"],
            "description": data.get("description"),
            "type": data.get("attachment_type"),
            "num_pages": data.get("num_pages"),
            "title": data.get("title")
        }

        filing = {
            "id": data["filing_id"],
            "form": data["form"],
            "filing_date": data["filing_date"],
            "report_date": data.get("report_date"),
            "accession_number": data["accession_number"],
            "fiscal_year": data.get("fiscal_year"),
            "fiscal_period": data.get("fiscal_period")
        }

        company = {
            "id": data["company_id"],
            "name": data["company_name"],
            "symbols": data.get("company_symbols", []),
            "exchanges": data.get("company_exchanges", [])
        }

        max_page = attachment.get("num_pages") or 0
        if max_page == 0:
            self.log_error(f"No pages stored for attachment #{args.attachment_id}")
            return ActionResponse(
                message=Message(
                    role="tool",
                    status="completed",
                    content=f"No pages stored for attachment #{args.attachment_id}.",
                    error=True,
                    action_id=action.id
                )
            )

        # Validate page range
        start = args.start_page
        end = min(args.end_page, max_page)

        if start > max_page:
            self.log_error(f"Page {start} exceeds max page {max_page}")
            return ActionResponse(
                message=Message(
                    role="tool",
                    status="completed",
                    content=f"Start page {start} exceeds last page {max_page} for attachment #{args.attachment_id}.",
                    error=True,
                    action_id=action.id
                )
            )

        # Enforce max pages
        if (end - start + 1) > self.max_pages:
            end = start + self.max_pages - 1
            self.log_error(f"Range exceeds {self.max_pages} pages, truncating to {start}–{end}")

        # Query pages
        pages_result = await (
            self.database
            .table("filing_attachment_pages")
            .select("page,content")
            .eq("attachment_id", args.attachment_id)
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
                    content=f"No pages found for attachment #{args.attachment_id} in range {start}–{end}.",
                    error=True,
                    action_id=action.id
                )
            )

        # Format output
        header = self._format_header(company, filing, attachment, start, end, max_page)
        body = self._join_pages_to_md(pages_result.data)

        output = header + "\n\n" + body
        truncated = False
        if len(output) > 200_000:
            output = header + "\n\n" + body[:200_000] + "\n\n[truncated]"
            truncated = True

        summary = f"Retrieved {len(pages_result.data)} pages: {company.get('name')} Exhibit {attachment.get('exhibit_number')}"
        if truncated:
            summary += " (truncated)"

        self.log_done(summary)

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
            source_type='attachment',
            source_id=args.attachment_id,
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
    def _format_header(company: Dict, filing: Dict, attachment: Dict, start: int, end: int, max_page: int) -> str:
        symbols = ",".join(company.get("symbols") or [])
        exchanges = ",".join(company.get("exchanges") or [])
        fy = filing.get("fiscal_year") or "—"
        fp = filing.get("fiscal_period") or "—"

        exhibit_number = attachment.get("exhibit_number", "?")
        filename = attachment.get("filename", "")
        description = attachment.get("description") or ""
        title = attachment.get("title") or "—"
        att_type = attachment.get("type", "").replace("_", " ").title()

        return (
            f"### {company.get('name','Unknown')} ({symbols})\n"
            f"**Form:** {filing.get('form')} | **Accession:** {filing.get('accession_number')}\n"
            f"**Exhibit:** {exhibit_number} | **Type:** {att_type}\n"
            f"**Title:** {title}\n"
            f"**Filename:** {filename}\n"
            f"**Description:** {description}\n"
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
