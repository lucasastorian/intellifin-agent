from typing import Optional
from pydantic import BaseModel, Field, ValidationError

from agent.actions.base_action import BaseAction
from agent.message import Action, Message
from agent.action_response import ActionResponse


class ReadAttachment(BaseModel):
    """Read pages from a specific attachment (exhibit), such as a Press release or Merger Agreement

    - Returns pages separated by '---' markers
    - Page numbers start at 1
    - If end_page not specified, reads to the last page
    - Hard limit: returns at most 20 pages (truncates if range is larger)
    """
    attachment_id: int = Field(..., description="The attachment ID to read")
    start_page: int = Field(1, description="Starting page number (1-indexed)", ge=1)
    end_page: Optional[int] = Field(None, description="Ending page number (inclusive). Defaults to last page.", ge=1)


class ReadAttachmentAction(BaseAction):
    name: str = 'ReadAttachment'
    schema = ReadAttachment

    async def call(self, action: Action):
        """Read pages from a specific attachment"""
        try:
            args = ReadAttachment(**action.body)
        except ValidationError as e:
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

        self.log_start("ReadAttachment", params=f"attachment_id={args.attachment_id}, pages {args.start_page}-{args.end_page or 'end'}")

        # Check if attachment exists and get metadata
        attachment_result = await (
            self.database
            .table("filing_attachments")
            .select("id,filing_id,exhibit_number,type,num_pages")
            .eq("id", args.attachment_id)
            .execute()
        )

        if not attachment_result.data:
            self.log_error(f"Attachment {args.attachment_id} not found")
            return ActionResponse(
                message=Message(
                    role="tool",
                    status="completed",
                    content=f"Attachment {args.attachment_id} not found.",
                    error=True,
                    action_id=action.id
                )
            )

        attachment = attachment_result.data[0]
        num_pages = attachment.get('num_pages') or 0

        if args.start_page > num_pages:
            self.log_error(f"start_page {args.start_page} exceeds total pages {num_pages}")
            return ActionResponse(
                message=Message(
                    role="tool",
                    status="completed",
                    content=f"Attachment {args.attachment_id} has only {num_pages} pages. Cannot start at page {args.start_page}.",
                    error=True,
                    action_id=action.id
                )
            )

        end_page = args.end_page or num_pages
        if end_page > num_pages:
            end_page = num_pages

        # Enforce a hard cap of 20 pages per call
        if end_page - args.start_page + 1 > 20:
            end_page = args.start_page + 20 - 1

        # Load pages
        pages_result = await (
            self.database
            .table("filing_attachment_pages")
            .select("page,content")
            .eq("attachment_id", args.attachment_id)
            .gte("page", args.start_page)
            .lte("page", end_page)
            .order("page")
            .execute()
        )

        if not pages_result.data:
            self.log_error(f"No pages found in range {args.start_page}-{end_page}")
            return ActionResponse(
                message=Message(
                    role="tool",
                    status="completed",
                    content=f"No pages found for attachment {args.attachment_id} in range {args.start_page}-{end_page}.",
                    error=True,
                    action_id=action.id
                )
            )

        # Build content with page separators
        header = f"**Attachment {args.attachment_id}** - EX-{attachment.get('exhibit_number', '?')}\n"
        header += f"Type: {attachment.get('type', 'Unknown')} | Pages: {args.start_page}-{end_page} of {num_pages} (max 20 per call)\n\n"

        pages = []
        for page_data in pages_result.data:
            pages.append(page_data['content'])

        content = header + "\n\n---\n\n".join(pages)

        self.log_done(f"Read {len(pages)} pages", content=content)

        return ActionResponse(
            message=Message(
                role="tool",
                status="completed",
                content=content,
                action_id=action.id
            )
        )
