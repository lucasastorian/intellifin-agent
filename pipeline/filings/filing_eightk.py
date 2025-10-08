from typing import List
from edgar.xbrl import XBRL

from pipeline.filings.base_filing import BaseFiling
from pipeline.parsers.parser import Parser


class FilingEightK(BaseFiling):

    def upsert(self):
        """Upserts the 8-K filing"""
        xbrl = self.filing.xbrl()

        filing_id = self._upsert_filing(xbrl=xbrl)
        pages = self._upsert_filing_pages(filing_id=filing_id)
        self._upsert_attachments(filing_id=filing_id)

        self._upsert_filing_chunks(pages=pages, filing_id=filing_id)

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

    def _upsert_attachments(self, filing_id: int):
        """Upserts ALL attachments (exhibits) for 8-K filings, including press releases (EX-99)"""
        documents = self.filing.attachments.documents

        for document in documents:
            if not document.document_type or not document.document_type.startswith("EX-"):
                continue

            exhibit_number = document.document_type.replace("EX-", "")

            if not document.is_html():
                continue

            parser = Parser(content=document.content)
            pages = parser.get_pages()

            if not pages:
                continue

            # Mark EX-99* exhibits as press releases
            is_press_release = exhibit_number.startswith("99")

            # Upsert attachment metadata
            attachment_response = self.database.table("filing_attachments").upsert({
                "exhibit_number": exhibit_number,
                "filename": document.document or f"ex-{exhibit_number}",
                "description": document.description,
                "num_pages": len(pages),
                "is_press_release": is_press_release,
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

            # Upsert attachment chunks
            self._upsert_filing_attachment_chunks(pages=pages, attachment_id=attachment_id, filing_id=filing_id)
