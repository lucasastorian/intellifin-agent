import uuid
from typing import List, Optional, Literal, Dict, Tuple
from pydantic import BaseModel, Field, create_model

from agent.message import Action, Message
from agent.action_response import ActionResponse, ActionFollowUp
from agent.actions.base_action import BaseAction


class ReadFilingContentAction(BaseAction):
    name: str = 'ReadFilingContent'

    def __init__(self, filing_id: int, valid_attachment_ids: List[int], valid_note_ids: List[int],
                 siblings: Optional[Tuple] = None, database=None, edgar_user_agent: str = None, start_year: int = 2017):
        super().__init__(database=database, edgar_user_agent=edgar_user_agent, start_year=start_year)
        self.filing_id = filing_id
        self.valid_attachment_ids = valid_attachment_ids
        self.valid_note_ids = valid_note_ids
        self.siblings = siblings

    @property
    def schema(self) -> BaseModel:
        """Returns a dynamically generated schema with valid attachment/note IDs as Literal constraints"""

        attachment_ids = tuple(self.valid_attachment_ids) if self.valid_attachment_ids else (None,)
        note_ids = tuple(self.valid_note_ids) if self.valid_note_ids else (None,)

        fields = {
            'thought': (
                str,
                Field(description="Explain what content you're reading and why")
            ),
            'document': (
                Literal['primary_document', 'attachment', 'note'],
                Field(description="The type of document to read: 'primary_document' (main filing), 'attachment' (exhibits), or 'note' (financial statement notes)")
            ),
            'attachment_id': (
                Optional[Literal[attachment_ids]],
                Field(default=None, description=f"Required when document='attachment'. Valid attachment IDs: {list(attachment_ids) if attachment_ids != (None,) else 'none available'}")
            ),
            'note_id': (
                Optional[Literal[note_ids]],
                Field(default=None, description=f"Required when document='note'. Valid note IDs: {list(note_ids) if note_ids != (None,) else 'none available'}")
            ),
            'start_page': (
                int,
                Field(default=0, description="Starting page number (0-indexed). Only applies to 'primary_document' and 'attachment'. Ignored for 'note'.")
            ),
            'end_page': (
                int,
                Field(default=10, description="Ending page number (exclusive). Only applies to 'primary_document' and 'attachment'. Ignored for 'note'.")
            )
        }

        return create_model(
            'ReadFilingContent',
            **fields,
            __doc__="""Read specific content from a filing.

Use this to explore:
- **primary_document**: Read page ranges from the main filing document (e.g., pages 5-15)
- **attachment**: Read page ranges from a specific exhibit (first specify attachment_id)
- **note**: Read complete financial statement note content (specify note_id; start_page/end_page ignored)

Page ranges are 0-indexed and exclusive on the end (e.g., start_page=0, end_page=10 reads pages 0-9)."""
        )

    async def call(self, action: Action) -> ActionResponse:
        """Reads specific filing content based on document type and forces curation"""
        try:
            args = self.schema(**action.body)
        except Exception as e:
            self.log_start("ReadFilingContent")
            self.log_error(f"Validation failed: {e}")
            return ActionResponse(
                message=Message(role="tool", status="completed", content=str(e), error=True, action_id=action.id)
            )

        # Validate attachment_id / note_id requirements
        if args.document == 'attachment' and args.attachment_id is None:
            return ActionResponse(
                message=Message(
                    role="tool",
                    status="completed",
                    content="attachment_id is required when document='attachment'",
                    error=True,
                    action_id=action.id
                )
            )

        if args.document == 'note' and args.note_id is None:
            return ActionResponse(
                message=Message(
                    role="tool",
                    status="completed",
                    content="note_id is required when document='note'",
                    error=True,
                    action_id=action.id
                )
            )

        self.log_start(
            "ReadFilingContent",
            params=f"document={args.document}, pages={args.start_page}-{args.end_page}",
            thought=args.thought
        )

        # Route to appropriate read method (now returns content + pages_read)
        if args.document == 'primary_document':
            result = await self._read_primary_document(args.start_page, args.end_page)
        elif args.document == 'attachment':
            result = await self._read_attachment(args.attachment_id, args.start_page, args.end_page)
        else:  # note
            result = await self._read_note(args.note_id)

        if result is None:
            self.log_error("Content not found")
            return ActionResponse(
                message=Message(
                    role="tool",
                    status="completed",
                    content="Requested content not found.",
                    error=True,
                    action_id=action.id
                )
            )

        content, pages_read = result

        self.log_done(f"Retrieved {args.document} content", content=content)

        read_message_id = str(uuid.uuid4())
        read_message = Message(
            id=read_message_id,
            role="tool",
            status="completed",
            content=content,
            action_id=action.id
        )

        from agent.actions.__read_filing.curate_filing_content import CurateFilingContentAction

        curate_action = CurateFilingContentAction(
            document_type=args.document,
            pages_read=pages_read,
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

    async def _read_primary_document(self, start_page: int, end_page: int) -> Optional[Tuple[str, List[Dict]]]:
        """Read pages from primary filing document - returns (content, pages_read)"""
        result = await (
            self.database
            .table("company_filing_pages")
            .select("page,content,form,company_name,company_symbols,filing_date,accession_number")
            .eq("filing_id", self.filing_id)
            .gte("page", start_page)
            .lt("page", end_page)
            .order("page")
            .execute()
        )

        if not result.data:
            return None

        pages = result.data
        first_page = pages[0]

        header = self._format_header(
            doc_type="Primary Document",
            company_name=first_page['company_name'],
            symbols=first_page['company_symbols'],
            form=first_page['form'],
            filing_date=first_page['filing_date'],
            page_range=(start_page, end_page)
        )

        content_parts = [f"**Page {p['page']}**\n{p['content']}" for p in pages]
        output = header + "\n\n" + "\n\n---\n\n".join(content_parts)

        # Build pages_read for curation
        pages_read = [{'page': p['page'], 'content': p['content']} for p in pages]

        return (output, pages_read)

    async def _read_attachment(self, attachment_id: int, start_page: int, end_page: int) -> Optional[Tuple[str, List[Dict]]]:
        """Read pages from filing attachment - returns (content, pages_read)"""
        # Get attachment metadata
        att_result = await (
            self.database
            .table("company_filing_attachments")
            .select("exhibit_number,title,attachment_type,company_name,company_symbols,form,filing_date")
            .eq("id", attachment_id)
            .limit(1)
            .execute()
        )

        if not att_result.data:
            return None

        att = att_result.data[0]

        # Get pages
        pages_result = await (
            self.database
            .table("filing_attachment_pages")
            .select("page,content")
            .eq("attachment_id", attachment_id)
            .gte("page", start_page)
            .lt("page", end_page)
            .order("page")
            .execute()
        )

        if not pages_result.data:
            return None

        pages = pages_result.data

        att_title = att.get('title') or f"Exhibit {att['exhibit_number']}"
        header = self._format_header(
            doc_type=f"Attachment: {att_title}",
            company_name=att['company_name'],
            symbols=att['company_symbols'],
            form=att['form'],
            filing_date=att['filing_date'],
            page_range=(start_page, end_page)
        )

        content_parts = [f"**Page {p['page']}**\n{p['content']}" for p in pages]
        output = header + "\n\n" + "\n\n---\n\n".join(content_parts)

        # Build pages_read for curation
        pages_read = [{'page': p['page'], 'content': p['content']} for p in pages]

        return (output, pages_read)

    async def _read_note(self, note_id: int) -> Optional[Tuple[str, List[Dict]]]:
        """Read full financial statement note content - returns (content, empty list)"""
        result = await (
            self.database
            .table("company_filing_notes")
            .select("title,content,filename,company_name,company_symbols,form,filing_date,fiscal_year,fiscal_period")
            .eq("id", note_id)
            .limit(1)
            .execute()
        )

        if not result.data:
            return None

        note = result.data[0]

        header = self._format_header(
            doc_type=f"Financial Statement Note: {note['title']}",
            company_name=note['company_name'],
            symbols=note['company_symbols'],
            form=note['form'],
            filing_date=note['filing_date'],
            fiscal_info=f"{note.get('fiscal_period', '—')} {note.get('fiscal_year', '—')}"
        )

        content = note['content'].strip()

        # Truncate if too large
        if len(content) > 200_000:
            content = content[:200_000] + "\n\n[truncated - content exceeds 200k characters]"

        output = header + "\n\n" + content

        # Notes don't have pages, so return empty list
        return (output, [])

    @staticmethod
    def _format_header(doc_type: str, company_name: str, symbols: List[str], form: str,
                      filing_date: str, page_range: Optional[Tuple[int, int]] = None,
                      fiscal_info: Optional[str] = None) -> str:
        """Format content header"""
        from datetime import datetime

        symbols_str = ','.join(symbols)
        filing_date_str = datetime.strptime(filing_date, '%Y-%m-%d').strftime('%B %-d, %Y')

        header = f"### {company_name} ({symbols_str}) - {form}\n"
        header += f"**Filed:** {filing_date_str}"

        if fiscal_info:
            header += f" | **Fiscal:** {fiscal_info}"

        if page_range:
            header += f"\n**Reading:** {doc_type} (pages {page_range[0]}-{page_range[1]-1})"
        else:
            header += f"\n**Reading:** {doc_type}"

        return header
