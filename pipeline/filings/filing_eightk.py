from edgar.xbrl import XBRL

from pipeline.filings.base_filing import BaseFiling
from pipeline.parsers.parser import Parser


class FilingEightK(BaseFiling):

    def upsert(self):
        """Upserts the 8-K filing"""
        xbrl = self.filing.xbrl()

        filing_id = self._upsert_filing(xbrl=xbrl)
        self._upsert_filing_pages(filing_id=filing_id)
        self._upsert_press_release_pages(filing_id=filing_id)

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
            return

        parser = Parser(content=press_release.content)
        pages = parser.get_pages()

        self.database.table("press_release_pages").upsert([{
            "page": page['page'],
            "content": page['content'],
            "filing_id": filing_id,
            "company_id": self.company_id
        } for page in pages], on_conflict="filing_id,page").execute()
