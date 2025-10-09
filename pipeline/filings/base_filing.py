from bs4 import BeautifulSoup
from abc import ABC, abstractmethod
from typing import Optional, List
from edgar.entity.filings import EntityFiling
from edgar.xbrl import XBRL

from database.database import Database
from pipeline.parsers.parser import Parser
from pipeline.chunker.markdown_chunker import MarkdownChunker
from pipeline.parsers.financial_statement import FinancialStatements


class BaseFiling(ABC):

    def __init__(self, filing: EntityFiling, company_id: int, database: Database):
        self.filing = filing
        self.company_id = company_id
        self.database = database

        self.accession_number = filing.accession_number
        self.report_date = filing.report_date if filing.report_date else None
        self.filing_date = filing.filing_date.strftime('%Y-%m-%d')

        self.markdown_chunker = MarkdownChunker()

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

        return note_ids

    def _upsert_filing_note_chunks(self, note_ids: list, processed_notes: list, filing_id: int):
        """Chunks filing notes and upserts them"""
        all_chunks = []

        for note_id, note_data in zip(note_ids, processed_notes):
            # Chunk the note content
            chunks = self.markdown_chunker.split(pages=[{"page": 0, "content": note_data['content']}])

            for i, chunk in enumerate(chunks):
                all_chunks.append({
                    "index": i,
                    "content": chunk.content,
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

    def _upsert_filing_chunks(self, pages: List[dict], filing_id: int):
        """Chunks the filing pages and upserts them"""
        chunks = self.markdown_chunker.split(pages=pages)

        data = [
            {
                "index": i,
                "page": chunk.page,
                "content": chunk.content,
                "has_table": chunk.has_table,
                "filing_id": filing_id,
                "company_id": self.company_id
            } for i, chunk in enumerate(chunks)]

        response = self.database.table("filing_chunks").upsert(data, on_conflict="filing_id,index").execute()

        return response.data

    def _update_filing_counts(self, filing_id: int):
        """Updates the filing with page count and attachment count"""
        # Count filing pages
        num_pages = self.database.table("filing_pages").select("*").eq("filing_id", filing_id).count()

        # Count attachments
        num_attachments = self.database.table("filing_attachments").select("*").eq("filing_id", filing_id).count()

        # Update filing record
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
