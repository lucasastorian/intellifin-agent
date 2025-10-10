from typing import List, Dict
from pydantic import BaseModel, Field

from agent.message import Action, Message
from agent.action_response import ActionResponse, ActionFollowUp
from agent.actions.base_action import BaseAction


class ReadFiling(BaseModel):
    """Read a filing's overview and available content.
    
    Returns a summary of the filing including:
    - Filing metadata (form, dates, company info)
    - Available attachments (with IDs, titles, summaries)
    - Available notes (with IDs, titles, previews)
    - Primary document page count
    
    After reading the overview, you can explore specific content using ReadFilingContent.
    """
    thought: str = Field(description="Explain why you're reading this filing")
    filing_id: int = Field(description="The filing ID to read")


class ReadFilingAction(BaseAction):
    name: str = 'ReadFiling'
    schema = ReadFiling

    async def call(self, action: Action) -> ActionResponse:
        """Returns filing overview and enables content exploration loop"""
        try:
            args = ReadFiling(**action.body)
        except Exception as e:
            self.log_start("ReadFiling")
            self.log_error(f"Validation failed: {e}")
            return ActionResponse(
                message=Message(role="tool", status="completed", content=str(e), error=True, action_id=action.id)
            )

        self.log_start("ReadFiling", params=f"filing_id={args.filing_id}", thought=args.thought)

        # Get filing metadata
        filing_result = await (
            self.database
            .table("company_filings")
            .select("*")
            .eq("id", args.filing_id)
            .limit(1)
            .execute()
        )

        if not filing_result.data:
            self.log_error(f"Filing #{args.filing_id} not found")
            return ActionResponse(
                message=Message(
                    role="tool",
                    status="completed",
                    content=f"Filing #{args.filing_id} not found.",
                    error=True,
                    action_id=action.id
                )
            )

        filing = filing_result.data[0]

        # Get attachments
        attachments_result = await (
            self.database
            .table("company_filing_attachments")
            .select("id,exhibit_number,title,attachment_type,num_pages,description")
            .eq("filing_id", args.filing_id)
            .order("exhibit_number")
            .execute()
        )

        attachments = attachments_result.data

        # Get notes
        notes_result = await (
            self.database
            .table("company_filing_notes")
            .select("id,title,filename")
            .eq("filing_id", args.filing_id)
            .order("filename")
            .execute()
        )

        notes = notes_result.data

        # Format output
        content = self._format_filing_overview(filing, attachments, notes)

        summary = f"Retrieved overview for {filing['form']} filing"
        self.log_done(summary)

        # Create follow-up actions with siblings pattern
        attachment_ids = [att['id'] for att in attachments]
        note_ids = [note['id'] for note in notes]

        # Import here to avoid circular dependency
        from agent.actions.read_filing.read_filing_content import ReadFilingContentAction
        from agent.actions.read_filing.exit_filing_reading import ExitFilingReadingAction

        read_content = ReadFilingContentAction(
            filing_id=args.filing_id,
            valid_attachment_ids=attachment_ids,
            valid_note_ids=note_ids,
            siblings=None,
            database=self.database,
            edgar_user_agent=self.edgar_user_agent,
            start_year=self.start_year
        )

        exit_action = ExitFilingReadingAction(
            database=self.database,
            edgar_user_agent=self.edgar_user_agent,
            start_year=self.start_year
        )

        siblings = (read_content, exit_action)
        read_content.siblings = siblings

        return ActionResponse(
            message=Message(
                role="tool",
                status="completed",
                content=content,
                action_id=action.id
            ),
            follow_up=ActionFollowUp(
                actions=[read_content],  # No Exit initially - only after first read+curate cycle
                force=True
            )
        )

    @staticmethod
    def _format_filing_overview(filing: Dict, attachments: List[Dict], notes: List[Dict]) -> str:
        """Format filing overview with metadata, attachments, and notes"""
        from datetime import datetime

        symbols = ','.join(filing['company_symbols'])
        filing_date = datetime.strptime(filing['filing_date'], '%Y-%m-%d').strftime('%B %-d, %Y')
        report_date = filing.get('report_date')
        if report_date:
            report_date = datetime.strptime(report_date, '%Y-%m-%d').strftime('%B %-d, %Y')
        else:
            report_date = '—'

        fy = filing.get('fiscal_year') or '—'
        fp = filing.get('fiscal_period') or '—'
        title = filing.get('title') or '—'
        summary = filing.get('summary') or '—'
        num_pages = filing.get('num_pages', '?')

        output = [
            f"# Filing Overview: {filing['company_name']} ({symbols})",
            f"**Form:** {filing['form']} | **Filed:** {filing_date} | **Report Date:** {report_date}",
            f"**Fiscal Period:** {fp} {fy}",
            f"**Title:** {title}",
            f"**Summary:** {summary}",
            f"**Primary Document:** {num_pages} pages",
            ""
        ]

        if attachments:
            output.append(f"## Attachments ({len(attachments)})")
            for att in attachments:
                att_type = att.get('attachment_type', '').replace('_', ' ').title()
                att_title = att.get('title') or att.get('description') or '—'
                att_pages = att.get('num_pages', '?')
                output.append(
                    f"- **[ID: {att['id']}]** Exhibit {att['exhibit_number']} ({att_type}) | {att_pages} pages\n"
                    f"  {att_title}"
                )
            output.append("")

        if notes:
            output.append(f"## Financial Statement Notes ({len(notes)})")
            for note in notes:
                preview = note.get('preview') or '—'
                output.append(
                    f"- **[ID: {note['id']}]** {note['title']}\n"
                    f"  Preview: {preview[:200]}{'...' if len(preview) > 200 else ''}"
                )
            output.append("")

        output.append(
            "**NEXT STEPS:** Use ReadFilingContent to read specific content (primary document pages, attachments, or notes). "
            "When finished, call ExitFilingReading."
        )

        return "\n".join(output)
