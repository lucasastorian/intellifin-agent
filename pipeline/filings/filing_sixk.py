import asyncio
from typing import List, Dict
from edgar.xbrl import XBRL

from pipeline.filings.base_filing import BaseFiling
from pipeline.enrichment.openai_client import OpenAIClient
from pipeline.enrichment.filing_summarizer import FilingSummarizer


class FilingSixK(BaseFiling):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        openai_client = OpenAIClient()
        self.filing_summarizer = FilingSummarizer(openai_client)

    def upsert(self):
        """Upserts the 6-K filing"""
        xbrl = self.filing.xbrl()

        filing = self._upsert_filing(xbrl=xbrl)
        pages = self._upsert_filing_pages(filing_id=filing['id'])

        self._upsert_filing_chunks(pages=pages, filing_id=filing['id'])

        # Run async enrichment in this thread's event loop
        asyncio.run(self._enrich_filing(filing, pages))

        # Update filing counts after all processing is complete
        self._update_filing_counts(filing_id=filing['id'])

    def _upsert_filing(self, xbrl: XBRL) -> dict:
        """Creates a filing record"""
        response = self.database.table("filings").upsert({
            "form": self.filing.form,
            "amendment": self.filing.form == "6-K/A",
            "filing_date": self.filing_date,
            "report_date": self.report_date,
            "accession_number": self.accession_number,
            "company_id": self.company_id
        }, on_conflict="accession_number").execute()

        return response.data[0]

    async def _enrich_filing(self, filing: dict, pages: List[Dict]):
        """Generate LLM summary for the 6-K filing (typically no attachments)"""
        # Build header
        header = self.filing_summarizer.build_header(self.company, filing)

        # Get first 10 pages
        first_pages = pages[:10]

        # Generate filing summary (no attachments for 6-K)
        result = await self.filing_summarizer.summarize(
            pages=first_pages,
            attachment_summaries=[],
            header=header
        )

        # Update filing with title + summary
        if result and (result.get("title") or result.get("summary")):
            self.database.table("filings").update({
                "title": result["title"],
                "summary": result["summary"]
            }).eq("id", filing['id']).execute()
