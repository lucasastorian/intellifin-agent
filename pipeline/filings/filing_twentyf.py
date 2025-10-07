import logging
from typing import Optional
from edgar.xbrl import XBRL

from pipeline.filings.base_filing import BaseFiling

logger = logging.getLogger(__name__)


class FilingTwentyF(BaseFiling):

    def upsert(self):
        """Upserts the DEF 14A filing"""
        xbrl = self.filing.xbrl()

        if xbrl is None:
            logger.warning(f"Filing {self.filing.form} ({self.accession_number}) missing an XBRL attachment")

        filing_id = self._upsert_filing(xbrl=xbrl)
        pages = self._upsert_filing_pages(filing_id=filing_id)
        self._upsert_filing_notes(filing_id=filing_id)
        self._upsert_financial_statements(xbrl=xbrl, filing_id=filing_id)

        self._upsert_filing_chunks(pages=pages, filing_id=filing_id)

        # Update filing counts after all processing is complete
        self._update_filing_counts(filing_id=filing_id)

    def _upsert_filing(self, xbrl: Optional[XBRL]) -> int:
        """Creates a filing record"""
        fiscal_year = xbrl.entity_info['fiscal_year'] if xbrl else None
        fiscal_period = xbrl.entity_info['fiscal_period'] if xbrl else None

        response = self.database.table("filings").upsert({
            "form": self.filing.form,
            "amendment": self.filing.form == "20-F/A",
            "fiscal_year": fiscal_year,
            "fiscal_period": fiscal_period,
            "filing_date": self.filing_date,
            "report_date": self.report_date,
            "accession_number": self.accession_number,
            "company_id": self.company_id
        }, on_conflict="accession_number").execute()

        return response.data[0]['id']
