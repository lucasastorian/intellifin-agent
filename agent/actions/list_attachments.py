import pandas as pd
from typing import Dict
from pydantic import BaseModel, Field
from agent.actions.base_action import BaseAction
from agent.message import Action, Message


class ListAttachments(BaseModel):
    """List all attachments (exhibits) for a specific filing

    - Returns exhibit number, filename, description, and number of pages
    - Use this to discover what exhibits are available before reading with ReadAttachment
    - Primarily applicable to 8-K filings (material contracts, certificates, underwriting agreements)
    """
    thought: str = Field(
        description="Explain why you're listing attachments for this filing and what you expect to find"
    )
    filing_id: int = Field(..., description="Unique filing id to list attachments for")


class ListAttachmentsAction(BaseAction):
    name: str = 'ListAttachments'
    schema = ListAttachments

    async def call(self, action: Action):
        try:
            args = self.validate(action)
        except RuntimeError as e:
            self.log_start("ListAttachments")
            self.log_error(f"Validation failed: {e}")
            return Message(role="tool", status="completed", content=str(e), error=True, action_id=action.id)

        self.log_start("ListAttachments", f"Filing #{args.filing_id}", thought=args.thought)

        # Check filing exists
        filing_result = (
            self.database
            .table("filings")
            .select("id,company_id,form,filing_date,report_date,accession_number")
            .eq("id", args.filing_id)
            .limit(1)
            .execute()
        )

        if not filing_result.data:
            self.log_error(f"Filing #{args.filing_id} not found")
            return Message(
                role="tool",
                status="completed",
                content=f"Filing id {args.filing_id} not found.",
                error=True,
                action_id=action.id
            )

        filing = filing_result.data[0]

        # Get company info
        company_result = (
            self.database
            .table("companies")
            .select("id,name,symbols")
            .eq("id", filing["company_id"])
            .limit(1)
            .execute()
        )
        company = company_result.data[0] if company_result.data else {"name": "Unknown", "symbols": []}

        # Query attachments
        attachments_result = (
            self.database
            .table("filing_attachments")
            .select("id,exhibit_number,filename,description,num_pages")
            .eq("filing_id", args.filing_id)
            .order("exhibit_number", desc=False)
            .execute()
        )

        if not attachments_result.data:
            self.log_done("No attachments found")
            return Message(
                role="tool",
                status="completed",
                content=f"No attachments found for filing {args.filing_id}.",
                action_id=action.id
            )

        content = self._format_attachments_to_md(
            attachments=attachments_result.data,
            company=company,
            filing=filing
        )

        self.log_done(f"Found {len(attachments_result.data)} attachments")

        return Message(
            role="tool",
            status="completed",
            content=content,
            action_id=action.id
        )

    def validate(self, action: Action) -> ListAttachments:
        try:
            return ListAttachments(**action.body)
        except Exception as e:
            raise RuntimeError(f"Validation failed for ListAttachments: {e}") from e

    @staticmethod
    def _format_attachments_to_md(attachments: list, company: Dict, filing: Dict) -> str:
        """Formats the attachments as a Markdown table with filing header"""

        symbols = ",".join(company.get("symbols") or [])

        # Build header
        header = (
            f"### {company.get('name','Unknown')} ({symbols})\n"
            f"**Form:** {filing.get('form')} | **Accession:** {filing.get('accession_number')}\n"
            f"**Report Date:** {filing.get('report_date') or '—'} | **Filing Date:** {filing.get('filing_date')}\n\n"
        )

        rows = []
        for att in attachments:
            rows.append({
                "id": att['id'],
                "exhibit": att['exhibit_number'],
                "filename": att['filename'],
                "description": att.get('description') or "",
                "pages": att.get('num_pages') or ""
            })

        df = pd.DataFrame(rows, columns=["id", "exhibit", "filename", "description", "pages"])

        return header + df.to_markdown(index=False)
