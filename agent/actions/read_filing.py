from typing import Optional
from pydantic import BaseModel, Field, ValidationError

from agent.actions.base_action import BaseAction
from agent.message import Action, Message
from agent.action_response import ActionResponse


class ReadFiling(BaseModel):
    """Read pages from a filing's primary document

    - Returns pages separated by '---' markers
    - Page numbers start at 1
    - If end_page not specified, reads to the last page
    """
    filing_id: int = Field(..., description="The filing ID to read")
    start_page: int = Field(1, description="Starting page number (1-indexed)", ge=1)
    end_page: Optional[int] = Field(None, description="Ending page number (inclusive). Defaults to last page.", ge=1)


class ReadFilingAction(BaseAction):
    name: str = 'ReadFiling'
    schema = ReadFiling

    async def call(self, action: Action):
        """Read pages from a filing's primary document"""
        try:
            args = ReadFiling(**action.body)
        except ValidationError as e:
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

        self.log_start("ReadFiling", params=f"filing_id={args.filing_id}, pages {args.start_page}-{args.end_page or 'end'}")

        # Check if filing exists and get metadata
        filing_result = await (
            self.database
            .table("company_filings")
            .select("id,form,filing_date,company_name,num_pages")
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
        num_pages = filing.get('num_pages') or 0

        # Validate page range
        if args.start_page > num_pages:
            self.log_error(f"start_page {args.start_page} exceeds total pages {num_pages}")
            return ActionResponse(
                message=Message(
                    role="tool",
                    status="completed",
                    content=f"Filing {args.filing_id} has only {num_pages} pages. Cannot start at page {args.start_page}.",
                    error=True,
                    action_id=action.id
                )
            )

        end_page = args.end_page or num_pages
        if end_page > num_pages:
            end_page = num_pages

        # Load pages
        pages_result = await (
            self.database
            .table("company_filing_pages")
            .select("page_number,content")
            .eq("filing_id", args.filing_id)
            .gte("page_number", args.start_page)
            .lte("page_number", end_page)
            .order("page_number")
            .execute()
        )

        if not pages_result.data:
            self.log_error(f"No pages found in range {args.start_page}-{end_page}")
            return ActionResponse(
                message=Message(
                    role="tool",
                    status="completed",
                    content=f"No pages found for filing {args.filing_id} in range {args.start_page}-{end_page}.",
                    error=True,
                    action_id=action.id
                )
            )

        # Build content with page separators
        header = f"**{filing['company_name']}** - {filing['form']} ({filing['filing_date']})\n"
        header += f"Pages: {args.start_page}-{end_page} of {num_pages}\n\n"

        pages = []
        for page_data in pages_result.data:
            pages.append(page_data['content'])

        content = header + "\n\n---\n\n".join(pages)

        self.log_done(f"Read {len(pages)} pages")

        return ActionResponse(
            message=Message(
                role="tool",
                status="completed",
                content=content,
                action_id=action.id
            )
        )
