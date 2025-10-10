from typing import List, Optional, get_args, Literal
from pydantic import BaseModel, Field, create_model
from agent.actions.base_action import BaseAction
from agent.message import Action, Message
from database.database import Database


class ReadFilingTenKAction(BaseAction):
    """Dynamically generated action for reading specific content from a 10-K filing"""

    def __init__(self, database: Database, edgar_user_agent: str, filing: dict, company: dict,
                 sections: List[str], attachments: List[dict], notes: List[dict], start_year: int = 2017):
        super().__init__(database, edgar_user_agent, start_year)
        self.filing = filing
        self.filing_id = filing['id']
        self.company = company
        self.sections = sections
        self.attachments = attachments
        self.notes = notes
        self.name = f'Read10K_{filing["id"]}'

    @property
    def schema(self) -> BaseModel:
        """Returns a dynamically generated schema based on available sections, attachments, and notes"""

        # Build field definitions
        fields = {}

        # Add thought field
        fields['thought'] = (str, Field(description="Explain what specific content you're looking for"))

        # Add sections if available - use Literal for validation
        if self.sections:
            # Create Literal type from available sections
            SectionLiteral = Literal[tuple(sorted(self.sections))]
            fields['sections'] = (
                Optional[List[SectionLiteral]],
                Field(
                    default=None,
                    description="Select specific sections to read (business, risk_factors, md&a, etc.)"
                )
            )

        # Add attachments if available - use Literal for validation
        if self.attachments:
            # Create keys like "10.1", "99.1" for exhibit numbers
            attachment_keys = tuple(att['exhibit_number'] for att in self.attachments)
            AttachmentLiteral = Literal[attachment_keys]
            fields['attachments'] = (
                Optional[List[AttachmentLiteral]],
                Field(
                    default=None,
                    description="Select specific attachments by exhibit number (e.g., '10.1', '99.1')"
                )
            )

        # Add notes if available - use Literal for validation
        if self.notes:
            note_titles = tuple(note['title'] for note in self.notes)
            NoteLiteral = Literal[note_titles]
            fields['notes'] = (
                Optional[List[NoteLiteral]],
                Field(
                    default=None,
                    description="Select specific notes by title"
                )
            )

        # Add page range as fallback
        fields['start_page'] = (
            Optional[int],
            Field(
                default=None,
                description="Alternative to sections: specify starting page number (1-indexed)"
            )
        )
        fields['num_pages'] = (
            Optional[int],
            Field(
                default=None,
                le=15,
                description="Number of pages to read (max 15, only used with start_page)"
            )
        )

        # Create dynamic model
        model = create_model(
            f'Read10KContent_{self.filing_id}',
            **fields,
            __doc__=f"""Read specific content from 10-K filing {self.filing_id}.

You can either:
1. Select sections (business, risk_factors, md&a, etc.)
2. Select attachments by exhibit number
3. Select notes by title
4. Specify a page range (start_page + num_pages)

At least one option must be specified."""
        )

        return model

    async def call(self, action: Action) -> Message:
        """Read and return the selected content"""
        try:
            args = self.schema(**action.body)
        except Exception as e:
            self.log_start("Read10K")
            self.log_error(f"Validation failed: {e}")
            return Message(role="tool", status="completed", content=str(e), error=True, action_id=action.id)

        # Extract selections
        sections = getattr(args, 'sections', None)
        attachments = getattr(args, 'attachments', None)
        notes = getattr(args, 'notes', None)
        start_page = getattr(args, 'start_page', None)
        num_pages = getattr(args, 'num_pages', None)

        # Validate at least one selection
        if not any([sections, attachments, notes, start_page]):
            return Message(
                role="tool",
                status="completed",
                content="You must specify at least one of: sections, attachments, notes, or start_page",
                error=True,
                action_id=action.id
            )

        self.log_start("Read10K", params=f"Filing {self.filing_id}")

        content_parts = []

        # Read sections
        if sections:
            for section in sections:
                section_content = await self._read_section(section)
                if section_content:
                    content_parts.append(section_content)

        # Read attachments
        if attachments:
            for exhibit_num in attachments:
                # exhibit_num is already just the number (e.g., "10.1")
                att_content = await self._read_attachment(exhibit_num)
                if att_content:
                    content_parts.append(att_content)

        # Read notes
        if notes:
            for note_title in notes:
                note_content = await self._read_note(note_title)
                if note_content:
                    content_parts.append(note_content)

        # Read page range
        if start_page:
            page_content = await self._read_pages(start_page, num_pages or 5)
            if page_content:
                content_parts.append(page_content)

        if not content_parts:
            self.log_error("No content found")
            return Message(
                role="tool",
                status="completed",
                content="No content found for the specified selections",
                error=True,
                action_id=action.id
            )

        final_content = "\n\n---\n\n".join(content_parts)
        self.log_done(f"Read {len(content_parts)} content sections")

        return Message(
            role="tool",
            status="completed",
            content=final_content,
            action_id=action.id
        )

    async def _read_section(self, section: str) -> Optional[str]:
        """Read a section from filing_section_pages"""
        # section is already the key (business, risk_factors, etc.)
        section_key = section

        result = (
            self.database
            .table("filing_section_pages")
            .select("page,content")
            .eq("filing_id", self.filing_id)
            .eq("section", section_key)
            .order("page")
await             .execute()
        )

        if not result.data:
            return None

        lines = [f"## Section: {section}\n"]
        for page_data in result.data:
            lines.append(page_data['content'])

        return "\n".join(lines)

    async def _read_attachment(self, exhibit_number: str) -> Optional[str]:
        """Read an attachment from filing_attachment_pages"""
        # Find attachment
        att_result = (
            self.database
            .table("filing_attachments")
            .select("id,title,exhibit_number")
            .eq("filing_id", self.filing_id)
            .eq("exhibit_number", exhibit_number)
            .limit(1)
await             .execute()
        )

        if not att_result.data:
            return None

        attachment = att_result.data[0]
        attachment_id = attachment['id']

        # Get pages
        pages_result = (
            self.database
            .table("filing_attachment_pages")
            .select("page,content")
            .eq("attachment_id", attachment_id)
            .order("page")
            .limit(15)  # Limit to 15 pages
await             .execute()
        )

        if not pages_result.data:
            return None

        lines = [f"## Attachment: EX-{exhibit_number} - {attachment.get('title', 'Untitled')}\n"]
        for page_data in pages_result.data:
            lines.append(f"### Page {page_data['page']}\n")
            lines.append(page_data['content'])

        return "\n".join(lines)

    async def _read_note(self, note_title: str) -> Optional[str]:
        """Read a note"""
        note_result = (
            self.database
            .table("filing_notes")
            .select("id,title,content,preview")
            .eq("filing_id", self.filing_id)
            .eq("title", note_title)
            .limit(1)
await             .execute()
        )

        if not note_result.data:
            return None

        note = note_result.data[0]
        lines = [f"## Note: {note['title']}\n"]
        if note.get('preview'):
            lines.append(f"**Preview**: {note['preview']}\n")
        lines.append(note['content'])

        return "\n".join(lines)

    async def _read_pages(self, start_page: int, num_pages: int) -> Optional[str]:
        """Read a page range from filing_pages"""
        end_page = start_page + num_pages - 1

        result = (
            self.database
            .table("filing_pages")
            .select("page,content")
            .eq("filing_id", self.filing_id)
            .gte("page", start_page)
            .lte("page", end_page)
            .order("page")
await             .execute()
        )

        if not result.data:
            return None

        lines = [f"## Pages {start_page}-{end_page}\n"]
        for page_data in result.data:
            lines.append(f"### Page {page_data['page']}\n")
            lines.append(page_data['content'])

        return "\n".join(lines)
