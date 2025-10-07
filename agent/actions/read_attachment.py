from typing import List, Dict
from pydantic import BaseModel, Field
from agent.actions.base_action import BaseAction
from agent.message import Action, Message


class ReadAttachment(BaseModel):
    """Read a specific page range from an attachment/exhibit (up to 20 pages).

    - Use ListAttachments first to discover available exhibits and get attachment_id
    - Page numbering starts at 1 for each attachment
    - Common exhibits: 1.x (underwriting), 2.x (M&A), 3.x (certificates), 4.x (instruments), 10.x (contracts)

    Strategy:
    - Start with first few pages to understand structure
    - Then read specific sections as needed
    """
    thought: str = Field(
        description="Explain what information you're looking for in this attachment and why these specific pages"
    )
    attachment_id: int = Field(..., description="Unique attachment id from ListAttachments")
    start_page: int = Field(..., ge=1, description="1-based start page")
    end_page: int = Field(..., ge=1, description="1-based end page (inclusive)")


class ReadAttachmentAction(BaseAction):
    name: str = "ReadAttachment"
    schema = ReadAttachment
    max_pages: int = 20

    async def call(self, action: Action):
        try:
            args = self.validate(action)
        except RuntimeError as e:
            self.log_start("ReadAttachment")
            self.log_error(f"Validation failed: {e}")
            return Message(role="tool", status="completed", content=str(e), error=True, action_id=action.id)

        self.log_start("ReadAttachment", f"Attachment #{args.attachment_id}, pages {args.start_page}–{args.end_page}", thought=args.thought)

        # Get attachment metadata
        attachment_result = (
            self.database
            .table("filing_attachments")
            .select("id,exhibit_number,filename,description,num_pages,filing_id,company_id")
            .eq("id", args.attachment_id)
            .limit(1)
            .execute()
        )

        if not attachment_result.data:
            self.log_error(f"Attachment #{args.attachment_id} not found")
            return Message(
                role="tool",
                status="completed",
                content=f"Attachment id {args.attachment_id} not found.",
                error=True,
                action_id=action.id
            )

        attachment = attachment_result.data[0]

        # Get filing info
        filing_result = (
            self.database
            .table("filings")
            .select("id,form,filing_date,report_date,accession_number,fiscal_year,fiscal_period")
            .eq("id", attachment["filing_id"])
            .limit(1)
            .execute()
        )
        filing = filing_result.data[0] if filing_result.data else {}

        # Get company info
        company_result = (
            self.database
            .table("companies")
            .select("id,name,symbols,exchanges")
            .eq("id", attachment["company_id"])
            .limit(1)
            .execute()
        )
        company = company_result.data[0] if company_result.data else {"name": "Unknown", "symbols": [], "exchanges": []}

        max_page = attachment.get("num_pages") or 0
        if max_page == 0:
            self.log_error(f"No pages stored for attachment #{args.attachment_id}")
            return Message(
                role="tool",
                status="completed",
                content=f"No pages stored for attachment {args.attachment_id}.",
                error=True,
                action_id=action.id
            )

        start = args.start_page
        end = min(args.end_page, max_page)

        if start > max_page:
            self.log_error(f"Page {start} exceeds max page {max_page}")
            return Message(
                role="tool",
                status="completed",
                content=f"Start page {start} exceeds last page {max_page} for attachment {args.attachment_id}.",
                error=True,
                action_id=action.id
            )

        # Enforce max pages
        if (end - start + 1) > self.max_pages:
            end = start + self.max_pages - 1
            self.log_error(f"Page range exceeds {self.max_pages} pages, truncating to {start}–{end}")

        # Query pages
        pages_result = (
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
            return Message(
                role="tool",
                status="completed",
                content=f"No pages found for attachment {args.attachment_id} in range {start}-{end}.",
                error=True,
                action_id=action.id
            )

        header = self._format_header(company, filing, attachment, start, end, max_page)
        body = self._join_pages_to_md(pages_result.data)

        output = header + "\n\n" + body
        truncated = False
        if len(output) > 200_000:
            output = header + "\n\n" + body[:200_000] + "\n\n[truncated]"
            truncated = True

        # Build result summary
        company_name = company.get('name', 'Unknown')
        exhibit = attachment.get('exhibit_number', '?')
        summary = f"Retrieved {len(pages_result.data)} pages: {company_name} Exhibit {exhibit}"
        if truncated:
            summary += " (truncated)"

        self.log_done(summary)

        return Message(role="tool", status="completed", content=output, action_id=action.id)

    def validate(self, action: Action) -> ReadAttachment:
        try:
            return ReadAttachment(**action.body)
        except Exception as e:
            raise RuntimeError(f"Validation failed for ReadAttachment: {e}") from e

    @staticmethod
    def _format_header(company: Dict, filing: Dict, attachment: Dict, start: int, end: int, max_page: int) -> str:
        symbols = ",".join(company.get("symbols") or [])
        exchanges = ",".join(company.get("exchanges") or [])
        fy = filing.get("fiscal_year") or "—"
        fp = filing.get("fiscal_period") or "—"

        exhibit_number = attachment.get("exhibit_number", "?")
        filename = attachment.get("filename", "")
        description = attachment.get("description") or ""

        return (
            f"### {company.get('name','Unknown')} ({symbols})\n"
            f"**Form:** {filing.get('form')} | **Accession:** {filing.get('accession_number')}\n"
            f"**Exhibit:** {exhibit_number} | **Filename:** {filename}\n"
            f"**Description:** {description}\n"
            f"**Report Date:** {filing.get('report_date') or '—'} | **Filing Date:** {filing.get('filing_date')}\n"
            f"**Fiscal:** {fp} {fy} | **Exchanges:** {exchanges}\n"
            f"**Pages:** {start}–{end} of {max_page} | **Source:** attachment_pages"
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
