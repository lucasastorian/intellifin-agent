import asyncio
from bs4 import BeautifulSoup
from abc import ABC, abstractmethod
from typing import Optional, List, Literal, Dict
from edgar.entity.filings import EntityFiling
from edgar.xbrl import XBRL

from database.database import Database
from pipeline.parsers.parser import Parser
from pipeline.chunker.markdown_chunker import MarkdownChunker
from pipeline.parsers.financial_statement import FinancialStatements
from pipeline.enrichment.openai_client import OpenAIClient
from pipeline.enrichment.attachment_summarizer import AttachmentSummarizer
from pipeline.enrichment.note_preview_generator import NotePreviewGenerator


class BaseFiling(ABC):

    def __init__(self, filing: EntityFiling, company: dict, database: Database):
        self.filing = filing
        self.company = company
        self.company_id = company['id']
        self.database = database

        self.accession_number = filing.accession_number
        self.report_date = filing.report_date if filing.report_date else None
        self.filing_date = filing.filing_date.strftime('%Y-%m-%d')

        self.markdown_chunker = MarkdownChunker()

        openai_client = OpenAIClient()
        self.attachment_summarizer = AttachmentSummarizer(openai_client)
        self.note_preview_generator = NotePreviewGenerator(openai_client)

    @abstractmethod
    async def upsert(self):
        """Upserts the filing and associated data to local DB"""
        raise NotImplementedError

    async def _load_xbrl(self) -> XBRL:
        """Async wrapper for blocking edgartools xbrl() call"""
        return await asyncio.to_thread(self.filing.xbrl)

    async def _load_html(self) -> str:
        """Async wrapper for blocking edgartools html() call"""
        return await asyncio.to_thread(self.filing.html)

    @staticmethod
    def infer_attachment_type(exhibit_number: str) -> str:
        """Infer attachment type from exhibit number

        Args:
            exhibit_number: Exhibit number without "EX-" prefix (e.g., "99.1", "10.2", "3.1")

        Returns:
            Type string: press_release, material_contract, corporate_governance, debt_securities,
                        merger_acquisition, subsidiaries, legal_compliance, or other
        """
        # Extract prefix (e.g., "99" from "99.1", "10" from "10.2")
        prefix = exhibit_number.split(".")[0] if "." in exhibit_number else exhibit_number

        # Press releases - EX-99, EX-99.1, EX-99.2, etc.
        if prefix == "99":
            return "press_release"

        # Material contracts - EX-10, EX-10.1, etc.
        elif prefix == "10":
            return "material_contract"

        # Corporate governance - EX-3.x (bylaws, charters)
        elif prefix == "3":
            return "corporate_governance"

        # Debt/securities - EX-4.x (indentures, rights)
        elif prefix == "4":
            return "debt_securities"

        # M&A - EX-2.x (merger/acquisition agreements)
        elif prefix == "2":
            return "merger_acquisition"

        # Subsidiaries - EX-21
        elif prefix == "21":
            return "subsidiaries"

        # Legal/compliance - EX-1 (underwriting), EX-5 (legal opinions), EX-23 (consents)
        elif prefix in ("1", "5", "23"):
            return "legal_compliance"

        # Everything else
        else:
            return "other"

    def exists(self):
        """Returns True if an entry for the filing exists in the DB"""
        return len(
            self.database.table("filings").select("*").eq("accession_number", self.accession_number).execute()) > 0

    @abstractmethod
    def _upsert_filing(self, xbrl: XBRL):
        """Upserts the filing to the local db"""
        raise NotImplementedError

    async def _upsert_filing_pages(self, filing_id: int):
        """Creates a record for the filing pages and returns the pages"""
        html_content = await self._load_html()

        parser = Parser(content=html_content)
        pages = parser.get_pages()

        response = self.database.table("filing_pages").upsert([{
            "page": page['page'],
            "content": page['content'],
            "filing_id": filing_id,
            "company_id": self.company_id
        } for page in pages], on_conflict="filing_id,page").execute()

        return pages

    async def _upsert_filing_notes(self, filing_id: int):
        """Upserts all the notes associated with the filing and their chunks"""
        if not self.filing.reports:
            return None

        notes = self.filing.reports.get_by_category("Notes")
        processed_notes = []

        for note in notes:
            note_content = self._flatten_note(content=note.content)
            parser = Parser(content=note_content)
            note_markdown = parser.markdown()
            processed_notes.append({"title": note.short_name, "content": note_markdown,
                                    "filename": note.html_file_name, "filing_id": filing_id,
                                    "company_id": self.company_id})

        response = self.database.table("filing_notes").upsert(processed_notes,
                                                              on_conflict="filing_id,filename").execute()

        note_ids = [note['id'] for note in response.data]

        self._upsert_filing_note_chunks(note_ids=note_ids, processed_notes=processed_notes, filing_id=filing_id)

        # Pass note IDs for enrichment
        await self._enrich_note_previews(note_ids)

        return note_ids

    def _build_note_embedding_header(self, company_data: dict, filing_data: dict, note_title: str) -> str:
        """Build a rich contextual header for filing note embedding"""
        parts = []

        # Company header
        name = company_data.get('name')
        symbols = company_data.get('symbols', [])
        exchanges = company_data.get('exchanges', [])
        ticker = f"{symbols[0]} - {exchanges[0]}" if symbols and exchanges else symbols[0] if symbols else ""

        if name:
            parts.append(f"# {name}{f' ({ticker})' if ticker else ''}")

        # Sector/Industry
        sector = company_data.get('sector')
        industry = company_data.get('industry')
        if sector or industry:
            sector_str = f"Sector: {sector}" if sector else ""
            industry_str = f"Industry: {industry}" if industry else ""
            parts.append(" | ".join(filter(None, [sector_str, industry_str])))

        # Filing metadata
        form = filing_data.get('form')
        fiscal_year = filing_data.get('fiscal_year')
        fiscal_period = filing_data.get('fiscal_period')
        filing_date = filing_data.get('filing_date')
        report_date = filing_data.get('report_date')

        if form:
            filing_parts = [f"Form {form}"]
            if fiscal_year:
                period_str = f"FY {fiscal_year}"
                if fiscal_period and fiscal_period != 'FY':
                    period_str += f" {fiscal_period}"
                filing_parts.append(period_str)
            if filing_date:
                filing_parts.append(f"Filed: {filing_date}")
            if report_date:
                filing_parts.append(f"Period Ending: {report_date}")
            parts.append(" | ".join(filing_parts))

        # Note title
        if note_title:
            parts.append(f"\n## Note: {note_title}\n")

        return "\n".join(parts)

    def _upsert_filing_note_chunks(self, note_ids: list, processed_notes: list, filing_id: int):
        """Chunks filing notes and upserts them"""
        company_data = self.database.table("companies").select("*").eq("id", self.company_id).execute().data[0]

        filing_data = self.database.table("filings").select("form,fiscal_year,fiscal_period,filing_date,report_date").eq("id", filing_id).execute().data[0]

        all_chunks = []

        for note_id, note_data in zip(note_ids, processed_notes):
            header = self._build_note_embedding_header(company_data, filing_data, note_data['title'])

            chunks = self.markdown_chunker.split(pages=[{"page": 0, "content": note_data['content']}], header=header)

            for i, chunk in enumerate(chunks):
                all_chunks.append({
                    "index": i,
                    "content": chunk.content,
                    "embedding": chunk.embedding_text,  # Uses header + content
                    "has_table": chunk.has_table,
                    "filing_note_id": note_id,
                    "filing_id": filing_id,
                    "company_id": self.company_id
                })

        if all_chunks:
            self.database.table("filing_note_chunks").upsert(
                all_chunks,
                on_conflict="filing_note_id,index"
            ).execute()

    def _upsert_financial_statements(self, xbrl: Optional[XBRL], filing_id: int):
        """Upserts the financial statements for 10-Ks/10-Qs/20-Fs"""
        if xbrl is None:
            return

        fiscal_period = xbrl.entity_info.get('fiscal_period') if xbrl else None
        statements = FinancialStatements(
            xbrl=xbrl,
            report_date=self.report_date,
            filing_id=filing_id,
            company_id=self.company_id,
            database=self.database,
            fiscal_period=fiscal_period
        )
        statements.upsert_statements()

    @staticmethod
    def _flatten_note(content: str) -> Optional[str]:
        """Flattens the note structure by removing the outer table"""
        soup = BeautifulSoup(content, 'lxml')
        elements = []

        body = soup.find("body")
        if not body:
            return None

        table = body.find("table")
        if table is None:
            return None

        for row in table.find_all('tr', recursive=False):
            cells = row.find_all(['th', 'td'], recursive=False)

            for cell in cells:
                elements.append(cell)

        if len(elements) == 0:
            return None

        return ''.join([str(element) for element in elements])

    def _update_filing_counts(self, filing_id: int):
        """Updates the filing with page count and attachment count"""
        num_pages = self.database.table("filing_pages").select("*").eq("filing_id", filing_id).count()

        num_attachments = self.database.table("filing_attachments").select("*").eq("filing_id", filing_id).count()

        self.database.table("filings").update({
            "num_pages": num_pages,
            "num_attachments": num_attachments
        }).eq("id", filing_id).execute()

    def _upsert_attachments(self, filing_id: int, exhibit_filter=None) -> List[Dict]:
        """
        Upserts attachments (exhibits) for filings, returns data for enrichment.

        Args:
            filing_id: The filing ID
            exhibit_filter: Optional function(exhibit_number) -> bool to filter which exhibits to process
        """
        documents = self.filing.attachments.documents
        attachment_data = []

        for document in documents:
            if not document.document_type or not document.document_type.startswith("EX-"):
                continue

            exhibit_number = document.document_type.replace("EX-", "")

            if exhibit_filter and not exhibit_filter(exhibit_number):
                continue

            if not document.is_html():
                continue

            parser = Parser(content=document.content)
            pages = parser.get_pages()

            if not pages:
                continue

            attachment_type = self.infer_attachment_type(exhibit_number)

            attachment_response = self.database.table("filing_attachments").upsert({
                "exhibit_number": exhibit_number,
                "filename": document.document or f"ex-{exhibit_number}",
                "description": document.description,
                "num_pages": len(pages),
                "type": attachment_type,
                "filing_id": filing_id,
                "company_id": self.company_id
            }, on_conflict="filing_id,exhibit_number").execute()

            if not attachment_response.data:
                continue

            attachment_id = attachment_response.data[0]['id']

            self.database.table("filing_attachment_pages").upsert([{
                "page": page['page'],
                "content": page['content'],
                "attachment_id": attachment_id,
                "filing_id": filing_id,
                "company_id": self.company_id
            } for page in pages], on_conflict="attachment_id,page").execute()

            # Only chunk press releases (99 exhibits) for vector search
            if attachment_type == "press_release":
                self._upsert_filing_attachment_chunks(
                    pages=pages,
                    attachment_id=attachment_id,
                    filing_id=filing_id,
                    attachment_type=attachment_type,
                    exhibit_number=exhibit_number,
                    description=document.description
                )

            attachment_data.append({
                "attachment_id": attachment_id,
                "exhibit_number": exhibit_number,
                "pages": pages[:10]
            })

        return attachment_data

    def _build_attachment_embedding_header(self, attachment_type: str, exhibit_number: str, description: str = None) -> str:
        """Build a rich contextual header for attachment embedding"""
        parts = []

        # Company header
        name = self.company.get('name')
        symbols = self.company.get('symbols', [])
        exchanges = self.company.get('exchanges', [])
        ticker = f"{symbols[0]} - {exchanges[0]}" if symbols and exchanges else symbols[0] if symbols else ""

        if name:
            parts.append(f"# {name}{f' ({ticker})' if ticker else ''}")

        sector = self.company.get('sector')
        industry = self.company.get('industry')
        if sector or industry:
            sector_str = f"Sector: {sector}" if sector else ""
            industry_str = f"Industry: {industry}" if industry else ""
            parts.append(" | ".join(filter(None, [sector_str, industry_str])))

        filing_parts = [f"Form {self.filing.form}"]
        filing_parts.append(f"Filed: {self.filing_date}")
        if self.report_date:
            filing_parts.append(f"Report Date: {self.report_date}")
        parts.append(" | ".join(filing_parts))

        attachment_display = attachment_type.replace('_', ' ').title()
        attachment_parts = [f"Exhibit {exhibit_number}", attachment_display]
        if description:
            attachment_parts.append(description)
        parts.append(f"\n## {' - '.join(attachment_parts)}\n")

        return "\n".join(parts)

    def _upsert_filing_attachment_chunks(self, pages: List[dict], attachment_id: int, filing_id: int,
                                         attachment_type: str, exhibit_number: str, description: str = None):
        """Chunks attachment pages and upserts them with embedding context"""
        header = self._build_attachment_embedding_header(attachment_type, exhibit_number, description)
        chunks = self.markdown_chunker.split(pages=pages, header=header)

        data = [
            {
                "index": i,
                "page": chunk.page,
                "pages": chunk.pages,
                "embedding": chunk.embedding_text,
                "has_table": chunk.has_table,
                "attachment_id": attachment_id,
                "filing_id": filing_id,
                "company_id": self.company_id
            } for i, chunk in enumerate(chunks)]

        if data:
            self.database.table("filing_attachment_chunks").upsert(
                data,
                on_conflict="attachment_id,index"
            ).execute()

    async def _enrich_attachments(self, attachment_data: List[Dict], filing: dict) -> List[Dict]:
        """Generate LLM summaries for all attachments in parallel, returns enriched attachments"""
        if not attachment_data:
            return []

        header = self.attachment_summarizer.build_header(self.company, filing)

        tasks = [
            self.attachment_summarizer.summarize(att["pages"], header)
            for att in attachment_data
        ]

        summaries = await asyncio.gather(*tasks, return_exceptions=True)

        updates = []
        enriched = []
        for att, result in zip(attachment_data, summaries):
            if isinstance(result, Exception):
                print(f"ERROR enriching attachment {att['exhibit_number']}: {result}")
                import traceback
                traceback.print_exception(type(result), result, result.__traceback__)
                continue

            if result and (result.get("title") or result.get("summary")):
                # Only pass changed fields - UpsertBuilder will pull missing NOT NULL fields from existing row
                updates.append({
                    "id": att["attachment_id"],
                    "title": result["title"],
                    "summary": result["summary"]
                })
                enriched.append({
                    "exhibit_number": att.get("exhibit_number", "Unknown"),
                    "title": result["title"],
                    "summary": result["summary"]
                })
            else:
                print(f"WARNING: Attachment {att['exhibit_number']} returned empty result: {result}")

        # Batch upsert all attachments at once (SELECT-based INSERT pulls missing fields)
        if updates:
            await asyncio.to_thread(
                lambda: self.database.table("filing_attachments").upsert(
                    updates,
                    on_conflict="id"
                ).execute()
            )

        return enriched

    async def _enrich_note_previews(self, note_ids: List[int]):
        """Generate one-sentence previews for all notes in parallel

        Args:
            note_ids: List of note IDs to enrich
        """
        if not note_ids:
            return

        # Fetch note titles and content for preview generation
        notes = self.database.table("filing_notes").select("id,title,content").in_("id", note_ids).execute()

        tasks = [
            self.note_preview_generator.generate(note['title'], note['content'])
            for note in notes.data
        ]

        previews = await asyncio.gather(*tasks, return_exceptions=True)

        updates = []
        for note, result in zip(notes.data, previews):
            if isinstance(result, Exception):
                continue

            if result:
                # Only pass changed fields - UpsertBuilder will pull missing NOT NULL fields from existing row
                updates.append({
                    "id": note['id'],
                    "preview": result
                })

        # Batch upsert all notes at once (SELECT-based INSERT pulls missing fields)
        if updates:
            await asyncio.to_thread(
                lambda: self.database.table("filing_notes").upsert(
                    updates,
                    on_conflict="id"
                ).execute()
            )
