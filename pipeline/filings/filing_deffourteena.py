from edgar.xbrl import XBRL

from pipeline.filings.base_filing import BaseFiling


class FilingDefFourteenA(BaseFiling):

    async def upsert(self):
        """Upserts the DEF 14A filing"""
        xbrl = await self._load_xbrl()

        filing_id = await self._upsert_filing(xbrl=xbrl)
        pages = await self._upsert_filing_pages(filing_id=filing_id)

        # DEF 14A filings are not chunked
        await self._update_filing_counts(filing_id=filing_id)

    async def _upsert_filing(self, xbrl: XBRL) -> int:
        """Creates a filing record"""
        response = await self.database.table("filings").upsert({
            "form": self.filing.form,
            "amendment": self.filing.form == "DEF 14A/A",
            "filing_date": self.filing_date,
            "report_date": self.report_date,
            "accession_number": self.accession_number,
            "company_id": self.company_id
        }, on_conflict="accession_number").execute()

        return response.data[0]['id']
