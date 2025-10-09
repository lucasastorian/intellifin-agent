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

        # Initialize LLM enrichment clients
        openai_client = OpenAIClient()
        self.attachment_summarizer = AttachmentSummarizer(openai_client)
        self.note_preview_generator = NotePreviewGenerator(openai_client)

    @abstractmethod
    def upsert(self):
        """Upserts the filing and associated data to local DB"""
        raise NotImplementedError

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

    def _upsert_filing_pages(self, filing_id: int):
        """Creates a record for the filing pages and returns the pages"""
        html_content = self.filing.html()

        parser = Parser(content=html_content)
        pages = parser.get_pages()

        response = self.database.table("filing_pages").upsert([{
            "page": page['page'],
            "content": page['content'],
            "filing_id": filing_id,
            "company_id": self.company_id
        } for page in pages], on_conflict="filing_id,page").execute()

        return pages

    def _upsert_filing_notes(self, filing_id: int):
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

        # Chunk each note
        self._upsert_filing_note_chunks(note_ids=note_ids, processed_notes=processed_notes, filing_id=filing_id)

        # Generate previews for notes
        note_data = [(note_id, processed_note['title'], processed_note['content'])
                     for note_id, processed_note in zip(note_ids, processed_notes)]
        asyncio.run(self._enrich_note_previews(note_data))

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
        # Get company data for header
        company_data = self.database.table("companies").select("*").eq("id", self.company_id).execute().data[0]

        # Get filing data for header
        filing_data = self.database.table("filings").select("form,fiscal_year,fiscal_period,filing_date,report_date").eq("id", filing_id).execute().data[0]

        all_chunks = []

        for note_id, note_data in zip(note_ids, processed_notes):
            # Build embedding header
            header = self._build_note_embedding_header(company_data, filing_data, note_data['title'])

            # Chunk the note content with header
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

    def _upsert_filing_attachment_chunks(self, pages: List[dict], attachment_id: int, filing_id: int):
        """Chunks attachment pages and upserts them"""
        chunks = self.markdown_chunker.split(pages=pages)

        data = [
            {
                "index": i,
                "page": chunk.page,
                "content": chunk.content,
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
                continue

            if result and (result.get("title") or result.get("summary")):
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

        if updates:
            self.database.table("filing_attachments").upsert(updates, on_conflict="id").execute()

        return enriched

    async def _enrich_note_previews(self, note_data: List[tuple]):
        """Generate one-sentence previews for all notes in parallel"""
        if not note_data:
            return

        tasks = [
            self.note_preview_generator.generate(title, content)
            for note_id, title, content in note_data
        ]

        previews = await asyncio.gather(*tasks, return_exceptions=True)

        updates = []
        for (note_id, title, content), result in zip(note_data, previews):
            if isinstance(result, Exception):
                continue

            if result:
                updates.append({
                    "id": note_id,
                    "preview": result
                })

        if updates:
            self.database.table("filing_notes").upsert(updates, on_conflict="id").execute()
