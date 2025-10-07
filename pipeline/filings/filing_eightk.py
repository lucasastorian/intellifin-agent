from typing import List
from edgar.xbrl import XBRL

from pipeline.filings.base_filing import BaseFiling
from pipeline.parsers.parser import Parser


class FilingEightK(BaseFiling):

    # Exhibit types to extract (excludes legal opinions, consents, XBRL)
    included_exhibits: List[str] = ["1", "2", "3", "4", "10", "99"]

    def upsert(self):
        """Upserts the 8-K filing"""
        xbrl = self.filing.xbrl()

        filing_id = self._upsert_filing(xbrl=xbrl)
        pages = self._upsert_filing_pages(filing_id=filing_id)
        press_release_pages = self._upsert_press_release_pages(filing_id=filing_id)
        self._upsert_attachments(filing_id=filing_id)

        self._upsert_filing_chunks(pages=pages, filing_id=filing_id)
        if press_release_pages:
            self._upsert_press_release_chunks(pages=press_release_pages, filing_id=filing_id)

        # Update filing counts after all processing is complete
        self._update_filing_counts(filing_id=filing_id)

    def _upsert_filing(self, xbrl: XBRL) -> int:
        """Creates a filing record"""
        response = self.database.table("filings").upsert({
            "form": self.filing.form,
            "items": self.filing.items.split(','),
            "press_release": '9.01' in self.filing.items,
            "amendment": self.filing.form == "8-K/A",
            "filing_date": self.filing_date,
            "report_date": self.report_date,
            "accession_number": self.accession_number,
            "company_id": self.company_id
        }, on_conflict="accession_number").execute()

        return response.data[0]['id']

    def _upsert_press_release_pages(self, filing_id: int):
        """Upserts the press release for press releases"""
        documents = self.filing.attachments.documents
        press_release = next((document for document in documents if document.document_type == "EX-99.1"), None)
        if not press_release:
            return None

        parser = Parser(content=press_release.content)
        pages = parser.get_pages()

        self.database.table("press_release_pages").upsert([{
            "page": page['page'],
            "content": page['content'],
            "filing_id": filing_id,
            "company_id": self.company_id
        } for page in pages], on_conflict="filing_id,page").execute()

        return pages

    def _upsert_press_release_chunks(self, pages: list, filing_id: int):
        """Chunks the press release pages and upserts them"""
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

        self.database.table("press_release_chunks").upsert(data, on_conflict="filing_id,index").execute()

    def _upsert_attachments(self, filing_id: int):
        """Upserts attachments (exhibits) for 8-K filings"""
        documents = self.filing.attachments.documents

        for document in documents:
            if not document.document_type or not document.document_type.startswith("EX-"):
                continue

            exhibit_number = document.document_type.replace("EX-", "")

            # Skip 99.1 (press releases are handled separately)
            if exhibit_number == "99.1":
                continue

            # Check if exhibit type is in included list (e.g., "1", "2", "3")
            exhibit_prefix = exhibit_number.split(".")[0] if "." in exhibit_number else exhibit_number
            if exhibit_prefix not in self.included_exhibits:
                continue

            # Only process HTML documents
            if not document.is_html():
                continue

            # Parse HTML content to pages
            try:
                parser = Parser(content=document.content)
                pages = parser.get_pages()
            except Exception:
                # Skip attachments that fail to parse
                continue

            if not pages:
                continue

            # Upsert attachment metadata
            attachment_response = self.database.table("filing_attachments").upsert({
                "exhibit_number": exhibit_number,
                "filename": document.document or f"ex-{exhibit_number}",
                "description": document.description,
                "num_pages": len(pages),
                "filing_id": filing_id,
                "company_id": self.company_id
            }, on_conflict="filing_id,exhibit_number").execute()

            if not attachment_response.data:
                continue

            attachment_id = attachment_response.data[0]['id']

            # Upsert attachment pages
            self.database.table("filing_attachment_pages").upsert([{
                "page": page['page'],
                "content": page['content'],
                "attachment_id": attachment_id,
                "filing_id": filing_id,
                "company_id": self.company_id
            } for page in pages], on_conflict="attachment_id,page").execute()

            # Chunk attachment
            self._upsert_filing_attachment_chunks(pages=pages, attachment_id=attachment_id, filing_id=filing_id)
