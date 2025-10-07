import logging
from edgar.xbrl import XBRL
from typing import Optional, List

from pipeline.filings.base_filing import BaseFiling

logger = logging.getLogger(__name__)


class FilingTenK(BaseFiling):

    def upsert(self):
        """Upserts the 10-K filing and associated pages"""
        xbrl = self.filing.xbrl()

        if xbrl is None:
            logger.warning(f"Filing {self.filing.form} ({self.accession_number}) missing an XBRL attachment")

        filing_id = self._upsert_filing(xbrl=xbrl)
        pages = self._upsert_filing_pages(filing_id=filing_id)
        self._upsert_filing_notes(filing_id=filing_id)
        self._upsert_financial_statements(xbrl=xbrl, filing_id=filing_id)
        self._upsert_filing_chunks(pages=pages, filing_id=filing_id)

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

    # def _upsert_filing_chunks(self, pages: List[dict], filing_id: int):
    #     """Chunks the filing pages and upserts them"""
    #     chunks = self.markdown_chunker.split(pages=pages)
    #
    #     data = [
    #         {
    #             "page": chunk.page,
    #             "content": chunk.content,
    #             "filing_id": filing_id,
    #             "company_id": self.company_id
    #         } for chunk in chunks]
    #
    #     response = self.database.table("filing_chunks").upsert(data).execute()
    #
    #     return response.data[0]['id']
