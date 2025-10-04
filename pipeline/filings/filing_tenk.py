from edgar.xbrl import XBRL

from pipeline.filings.base_filing import BaseFiling


class FilingTenK(BaseFiling):

    def upsert(self):
        """Upserts the 10-K filing and associated pages"""
        xbrl = self.filing.xbrl()

        filing_id = self._upsert_filing(xbrl=xbrl)
        self._upsert_filing_pages(filing_id=filing_id)
        self._upsert_filing_notes(filing_id=filing_id)
        self._upsert_financial_statements(xbrl=xbrl, filing_id=filing_id)

    def _upsert_filing(self, xbrl: XBRL) -> int:
        """Creates a filing record"""
        fiscal_year = xbrl.entity_info['fiscal_year']
        fiscal_period = xbrl.entity_info['fiscal_period']

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

        return response['data'][0]['id']
