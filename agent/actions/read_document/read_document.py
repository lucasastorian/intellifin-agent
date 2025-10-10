from pydantic import BaseModel, Field
from typing import Literal, List, Optional

from agent.actions.base_action import BaseAction
from agent.message import Action, Message
from agent.action_response import ActionResponse
from agent.actions.read_document.read_filing_tenk import ReadFilingTenKAction
from agent.actions.read_document.read_filing_tenq import ReadFilingTenQAction
from agent.actions.read_document.read_filing_content import ReadFilingContentAction
from agent.actions.read_document.read_attachment_content import ReadAttachmentContentAction
from agent.actions.read_document.read_note_content import ReadNoteContentAction


class ReadDocument(BaseModel):
    """Preview a filing, attachment, or note before reading specific sections or pages.

    Returns metadata, summary (if available), and first 3 pages as a preview.
    After calling this, you MUST use the returned follow-up action to read specific content.
    """
    thought: str = Field(
        description="Explain what you're looking for and why you want to read this document"
    )
    document_id: int = Field(description="The filing_id, attachment_id, or note_id from list_filings")
    document_type: Literal['filing', 'attachment', 'note'] = Field(
        description="The type of document to preview"
    )


class ReadDocumentAction(BaseAction):
    name: str = 'ReadDocument'
    schema = ReadDocument

    async def call(self, action: Action) -> ActionResponse:
        """Preview a document and return a forced follow-up action for reading specific content"""
        try:
            args = self.validate(action)
        except RuntimeError as e:
            self.log_start("ReadDocument")
            self.log_error(f"Validation failed: {e}")
            return ActionResponse(
                message=Message(role="tool", status="completed", content=str(e), error=True, action_id=action.id),
                follow_up_actions=None,
                force_action=False
            )

        self.log_start("ReadDocument", params=f"{args.document_type} ID {args.document_id}", thought=args.thought)

        # Fetch document based on type
        if args.document_type == 'filing':
            return await self._preview_filing(args.document_id, action.id)
        elif args.document_type == 'attachment':
            return await self._preview_attachment(args.document_id, action.id)
        elif args.document_type == 'note':
            return await self._preview_note(args.document_id, action.id)

    async def _preview_filing(self, filing_id: int, action_id: str) -> ActionResponse:
        """Preview a filing and return filing-specific follow-up action"""
        # Fetch filing details
        filing_result = (
            self.database
            .table("company_filings")
            .select("*")
            .eq("id", filing_id)
            .limit(1)
await             .execute()
        )

        if not filing_result.data:
            self.log_error(f"Filing {filing_id} not found")
            return ActionResponse(
                message=Message(
                    role="tool",
                    status="completed",
                    content=f"Filing {filing_id} not found",
                    error=True,
                    action_id=action_id
                ),
                follow_up_actions=None,
                force_action=False
            )

        filing = filing_result.data[0]
        company_id = filing['company_id']

        # Get first 3 pages as preview
        pages_result = (
            self.database
            .table("filing_pages")
            .select("page,content")
            .eq("filing_id", filing_id)
            .order("page")
            .limit(3)
await             .execute()
        )

        # Build preview content (passing sections/attachments/notes for 10-K/10-Q)
        if form in ['10-K', '10-K/A']:
            sections = self._get_available_sections(filing_id, '10-K')
            attachments = self._get_attachments(filing_id)
            notes = self._get_notes(filing_id)
            preview = self._format_filing_preview(filing, pages_result.data, sections, attachments, notes)
        elif form in ['10-Q', '10-Q/A']:
            sections = self._get_available_sections(filing_id, '10-Q')
            attachments = self._get_attachments(filing_id)
            notes = self._get_notes(filing_id)
            preview = self._format_filing_preview(filing, pages_result.data, sections, attachments, notes)
        else:
            preview = self._format_filing_preview(filing, pages_result.data)

        # Determine filing type and create appropriate follow-up action
        form = filing['form']

        if form in ['10-K', '10-K/A']:
            # Fetch sections, attachments, notes for 10-K
            sections = self._get_available_sections(filing_id, '10-K')
            attachments = self._get_attachments(filing_id)
            notes = self._get_notes(filing_id)
            company = self._get_company(company_id)

            follow_up_action = ReadFilingTenKAction(
                database=self.database,
                edgar_user_agent=self.edgar_user_agent,
                filing=filing,
                company=company,
                sections=sections,
                attachments=attachments,
                notes=notes,
                start_year=self.start_year
            )

        elif form in ['10-Q', '10-Q/A']:
            sections = self._get_available_sections(filing_id, '10-Q')
            attachments = self._get_attachments(filing_id)
            notes = self._get_notes(filing_id)
            company = self._get_company(company_id)

            follow_up_action = ReadFilingTenQAction(
                filing=filing,
                company=company,
                sections=sections,
                attachments=attachments,
                notes=notes
            )

        else:
            # For other filings (8-K, 6-K, 20-F, DEF 14A), just offer page ranges
            company = self._get_company(company_id)
            follow_up_action = ReadFilingContentAction(
                filing=filing,
                company=company,
                total_pages=filing.get('num_pages', 0)
            )

        self.log_done(f"Preview {form} filing {filing_id}")

        return ActionResponse(
            message=Message(
                role="tool",
                status="completed",
                content=preview,
                action_id=action_id
            ),
            follow_up_actions=[follow_up_action],
            force_action=True
        )

    async def _preview_attachment(self, attachment_id: int, action_id: str) -> ActionResponse:
        """Preview an attachment and return attachment-specific follow-up action"""
        # Fetch attachment details
        attachment_result = (
            self.database
            .table("filing_attachments")
            .select("*")
            .eq("id", attachment_id)
            .limit(1)
await             .execute()
        )

        if not attachment_result.data:
            self.log_error(f"Attachment {attachment_id} not found")
            return ActionResponse(
                message=Message(
                    role="tool",
                    status="completed",
                    content=f"Attachment {attachment_id} not found",
                    error=True,
                    action_id=action_id
                ),
                follow_up_actions=None,
                force_action=False
            )

        attachment = attachment_result.data[0]

        # Get first 3 pages as preview
        pages_result = (
            self.database
            .table("filing_attachment_pages")
            .select("page,content")
            .eq("attachment_id", attachment_id)
            .order("page")
            .limit(3)
await             .execute()
        )

        preview = self._format_attachment_preview(attachment, pages_result.data)

        follow_up_action = ReadAttachmentContentAction(
            attachment=attachment,
            total_pages=attachment.get('num_pages', 0)
        )

        self.log_done(f"Preview attachment {attachment_id}")

        return ActionResponse(
            message=Message(
                role="tool",
                status="completed",
                content=preview,
                action_id=action_id
            ),
            follow_up_actions=[follow_up_action],
            force_action=True
        )

    async def _preview_note(self, note_id: int, action_id: str) -> ActionResponse:
        """Preview a note and return note-specific follow-up action"""
        # Fetch note details
        note_result = (
            self.database
            .table("filing_notes")
            .select("*")
            .eq("id", note_id)
            .limit(1)
await             .execute()
        )

        if not note_result.data:
            self.log_error(f"Note {note_id} not found")
            return ActionResponse(
                message=Message(
                    role="tool",
                    status="completed",
                    content=f"Note {note_id} not found",
                    error=True,
                    action_id=action_id
                ),
                follow_up_actions=None,
                force_action=False
            )

        note = note_result.data[0]

        # Notes don't have pages - just truncate content for preview
        content = note['content']
        preview_content = content[:2000] if len(content) > 2000 else content

        preview = self._format_note_preview(note, preview_content)

        follow_up_action = ReadNoteContentAction(note=note)

        self.log_done(f"Preview note {note_id}")

        return ActionResponse(
            message=Message(
                role="tool",
                status="completed",
                content=preview,
                action_id=action_id
            ),
            follow_up_actions=[follow_up_action],
            force_action=True
        )

    def _get_available_sections(self, filing_id: int, filing_type: str) -> List[str]:
        """Get list of available sections for this filing"""
        result = (
            self.database
            .table("filing_section_pages")
            .select("section")
            .eq("filing_id", filing_id)
await             .execute()
        )

        # Return unique sections
        return list(set(row['section'] for row in result.data))

    def _get_attachments(self, filing_id: int) -> List[dict]:
        """Get attachments for this filing"""
        result = (
            self.database
            .table("filing_attachments")
            .select("id,exhibit_number,title,type,num_pages")
            .eq("filing_id", filing_id)
            .order("exhibit_number")
await             .execute()
        )
        return result.data

    def _get_notes(self, filing_id: int) -> List[dict]:
        """Get notes for this filing"""
        result = (
            self.database
            .table("filing_notes")
            .select("id,title,preview")
            .eq("filing_id", filing_id)
            .order("title")
await             .execute()
        )
        return result.data

    def _get_company(self, company_id: int) -> dict:
        """Get company details"""
        result = (
            self.database
            .table("companies")
            .select("*")
            .eq("id", company_id)
            .limit(1)
await             .execute()
        )
        return result.data[0] if result.data else {}

    @staticmethod
    def _format_filing_preview(filing: dict, pages: List[dict], sections: List[str] = None,
                               attachments: List[dict] = None, notes: List[dict] = None) -> str:
        """Format filing preview as markdown"""
        lines = []

        # Header
        lines.append(f"# {filing['form']} Filing Preview")
        lines.append(f"**Company**: {filing.get('company_name', 'N/A')}")
        lines.append(f"**Filing Date**: {filing.get('filing_date', 'N/A')}")
        lines.append(f"**Report Date**: {filing.get('report_date', 'N/A')}")
        lines.append(f"**Total Pages**: {filing.get('num_pages', 'N/A')}")

        # Title/Summary if available (8-K/6-K)
        if filing.get('title'):
            lines.append(f"\n**Title**: {filing['title']}")
        if filing.get('summary'):
            lines.append(f"\n**Summary**: {filing['summary']}")

        # Available content summary
        if sections or attachments or notes:
            lines.append("\n## Available Content")

            if sections:
                lines.append(f"\n**Sections** ({len(sections)}): {', '.join(sorted(sections))}")

            if attachments:
                lines.append(f"\n**Attachments** ({len(attachments)}):")
                for att in attachments[:10]:  # Show first 10
                    title = att.get('title') or att.get('type', 'Unknown')
                    lines.append(f"  - EX-{att['exhibit_number']}: {title}")
                if len(attachments) > 10:
                    lines.append(f"  - ... and {len(attachments) - 10} more")

            if notes:
                lines.append(f"\n**Notes** ({len(notes)}): {', '.join([n['title'] for n in notes[:15]])}")
                if len(notes) > 15:
                    lines.append(f" ... and {len(notes) - 15} more")

        # Preview pages
        lines.append("\n## Preview (First 3 Pages)\n")
        for page in pages:
            lines.append(f"### Page {page['page']}\n")
            lines.append(page['content'])
            lines.append("\n---\n")

        return "\n".join(lines)

    @staticmethod
    def _format_attachment_preview(attachment: dict, pages: List[dict]) -> str:
        """Format attachment preview as markdown"""
        lines = []

        lines.append(f"# Attachment Preview: EX-{attachment.get('exhibit_number', '?')}")
        if attachment.get('title'):
            lines.append(f"**Title**: {attachment['title']}")
        if attachment.get('type'):
            lines.append(f"**Type**: {attachment['type']}")
        lines.append(f"**Total Pages**: {attachment.get('num_pages', 'N/A')}")

        if attachment.get('summary'):
            lines.append(f"\n**Summary**: {attachment['summary']}")

        lines.append("\n## Preview (First 3 Pages)\n")
        for page in pages:
            lines.append(f"### Page {page['page']}\n")
            lines.append(page['content'])
            lines.append("\n---\n")

        return "\n".join(lines)

    @staticmethod
    def _format_note_preview(note: dict, preview_content: str) -> str:
        """Format note preview as markdown"""
        lines = []

        lines.append(f"# Note Preview: {note.get('title', 'Untitled')}")
        if note.get('preview'):
            lines.append(f"\n**Preview**: {note['preview']}")

        lines.append("\n## Content Preview\n")
        lines.append(preview_content)
        lines.append("\n\n*(Content truncated for preview)*")

        return "\n".join(lines)

    def validate(self, action: Action) -> ReadDocument:
        """Validates the action against the Pydantic schema"""
        return ReadDocument(**action.body)
