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
        # Convert empty report_date to None (DEF 14A and some other filings don't have report dates)
        self.report_date = filing.report_date if filing.report_date else None
        self.filing_date = filing.filing_date.strftime('%Y-%m-%d')

        self.markdown_chunker = MarkdownChunker()

    @abstractmethod
    def upsert(self):
        """Upserts the filing and associated data to local DB"""
        raise NotImplementedError

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
        """Upserts all the notes associated with the filing"""
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

        self.database.table("filing_notes").upsert(processed_notes, on_conflict="filing_id,filename").execute()

    def _upsert_financial_statements(self, xbrl: Optional[XBRL], filing_id: int):
        """Upserts the financial statements for 10-Ks/10-Qs/20-Fs"""
        if xbrl is None:
            return

        statements = FinancialStatements(xbrl=xbrl, report_date=self.report_date, filing_id=filing_id,
                                         company_id=self.company_id, database=self.database)
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
                "filing_id": filing_id,
                "company_id": self.company_id
            } for i, chunk in enumerate(chunks)]

        response = self.database.table("filing_chunks").upsert(data, on_conflict="filing_id,index").execute()

        return response.data
