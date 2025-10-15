import pandas as pd
from typing import Optional
from pydantic import BaseModel, Field, ValidationError

from agent.actions.base_action import BaseAction
from agent.message import Action, Message
from agent.action_response import ActionResponse


class ListAttachments(BaseModel):
    """List all attachments (exhibits) for a given filing

    - Returns a Markdown table with: id, exhibit_number, type, num_pages
    - Results sorted by exhibit_number
    - Use ReadAttachment action with the attachment ID to read the attachment content
    """
    filing_id: int = Field(..., description="The filing ID to list attachments for")


class ListAttachmentsAction(BaseAction):
    name: str = 'ListAttachments'
    schema = ListAttachments

    async def call(self, action: Action):
        """List all attachments for a given filing"""
        try:
            args = ListAttachments(**action.body)
        except ValidationError as e:
            self.log_start("ListAttachments")
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

        self.log_start("ListAttachments", params=f"filing_id={args.filing_id}")

        # Check if filing exists
        filing_result = await (
            self.database
            .table("company_filings")
            .select("id,form,filing_date,company_name,num_attachments")
            .eq("id", args.filing_id)
            .execute()
        )

        if not filing_result.data:
            self.log_error(f"Filing {args.filing_id} not found")
            return ActionResponse(
                message=Message(
                    role="tool",
                    status="completed",
                    content=f"Filing {args.filing_id} not found.",
                    error=True,
                    action_id=action.id
                )
            )

        filing = filing_result.data[0]

        # Load attachments
        attachments_result = await (
            self.database
            .table("filing_attachments")
            .select("id,exhibit_number,type,num_pages")
            .eq("filing_id", args.filing_id)
            .order("exhibit_number")
            .execute()
        )

        if not attachments_result.data:
            self.log_done("No attachments found", content=f"No attachments found for filing {args.filing_id} ({filing['form']}, {filing['filing_date']}).")
            return ActionResponse(
                message=Message(
                    role="tool",
                    status="completed",
                    content=f"No attachments found for filing {args.filing_id} ({filing['form']}, {filing['filing_date']}).",
                    action_id=action.id
                )
            )

        # Build header
        header = f"**{filing['company_name']}** - {filing['form']} ({filing['filing_date']})\n\n"

        # Build table
        rows = []
        for att in attachments_result.data:
            rows.append({
                "id": att['id'],
                "exhibit_number": att.get('exhibit_number', ''),
                "type": att.get('type', '-'),
                "pages": att.get('num_pages', '')
            })

        df = pd.DataFrame(rows, columns=["id", "exhibit_number", "type", "pages"])
        content = header + df.to_markdown(index=False)

        self.log_done(f"Found {len(rows)} attachments", content=content)

        return ActionResponse(
            message=Message(
                role="tool",
                status="completed",
                content=content,
                action_id=action.id
            )
        )
