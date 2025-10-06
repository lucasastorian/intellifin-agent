from edgar.xbrl import XBRL

from pipeline.filings.base_filing import BaseFiling


class FilingSixK(BaseFiling):

    def upsert(self):
        """Upserts the DEF 14A filing"""
        xbrl = self.filing.xbrl()

        filing_id = self._upsert_filing(xbrl=xbrl)
        self._upsert_filing_pages(filing_id=filing_id)

    def _upsert_filing(self, xbrl: XBRL) -> int:
        """Creates a filing record"""
        response = self.database.table("filings").upsert({
            "form": self.filing.form,
            "amendment": self.filing.form == "6-K/A",
            "filing_date": self.filing_date,
            "report_date": self.report_date,
            "accession_number": self.accession_number,
            "company_id": self.company_id
        }, on_conflict="accession_number").execute()

        return response.data[0]['id']
