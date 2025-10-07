import logging
from edgar.xbrl import XBRL
from typing import Optional, List

from pipeline.filings.base_filing import BaseFiling
from pipeline.parsers.parser import Parser

logger = logging.getLogger(__name__)


class FilingTenK(BaseFiling):

    # Exhibit types to extract (material contracts and subsidiaries)
    included_exhibits: List[str] = ["10", "21"]

    def upsert(self):
        """Upserts the 10-K filing and associated pages"""
        xbrl = self.filing.xbrl()

        if xbrl is None:
            logger.warning(f"Filing {self.filing.form} ({self.accession_number}) missing an XBRL attachment")

        filing_id = self._upsert_filing(xbrl=xbrl)
        pages = self._upsert_filing_pages(filing_id=filing_id)
        self._upsert_filing_notes(filing_id=filing_id)
        self._upsert_financial_statements(xbrl=xbrl, filing_id=filing_id)
        self._upsert_attachments(filing_id=filing_id)
        self._upsert_filing_chunks(pages=pages, filing_id=filing_id)

        # Update filing counts after all processing is complete
        self._update_filing_counts(filing_id=filing_id)

    def _upsert_filing(self, xbrl: Optional[XBRL]) -> int:
        """Creates a filing record"""
        fiscal_year = xbrl.entity_info['fiscal_year'] if xbrl else None
        fiscal_period = xbrl.entity_info['fiscal_period'] if xbrl else None

        response = self.database.table("filings").upsert({
            "form": self.filing.form,
            "amendment": self.filing.form == "10-K/A",
            "fiscal_year": fiscal_year,
            "fiscal_period": fiscal_period,
            "filing_date": self.filing_date,
            "report_date": self.report_date,
            "accession_number": self.accession_number,
            "company_id": self.company_id
        }, on_conflict="accession_number").execute()

        return response.data[0]['id']

    def _upsert_attachments(self, filing_id: int):
        """Upserts attachments (exhibits) for 10-K filings"""
        documents = self.filing.attachments.documents

        for document in documents:
            # Normalize exhibit number: "EX-10.1" -> "10.1", "EX-21" -> "21"
            if not document.document_type or not document.document_type.startswith("EX-"):
                continue

            exhibit_number = document.document_type.replace("EX-", "")

            # Check if exhibit type is in included list (e.g., "10", "21")
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
