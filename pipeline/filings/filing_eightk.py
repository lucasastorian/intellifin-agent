import asyncio
from typing import List, Dict
from edgar.xbrl import XBRL

from pipeline.filings.base_filing import BaseFiling
from pipeline.parsers.parser import Parser
from pipeline.enrichment.openai_client import OpenAIClient
from pipeline.enrichment.filing_summarizer import FilingSummarizer


class FilingEightK(BaseFiling):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        openai_client = OpenAIClient()
        self.filing_summarizer = FilingSummarizer(openai_client)

    def upsert(self):
        """Upserts the 8-K filing"""
        xbrl = self.filing.xbrl()

        filing = self._upsert_filing(xbrl=xbrl)
        pages = self._upsert_filing_pages(filing_id=filing['id'])
        attachment_data = self._upsert_attachments(filing_id=filing['id'])

        async def enrich():
            enriched_attachments = await self._enrich_attachments(attachment_data, filing)
            await self._enrich_filing(filing, pages, enriched_attachments)

        asyncio.run(enrich())

        self._update_filing_counts(filing_id=filing['id'])

    def _upsert_filing(self, xbrl: XBRL) -> dict:
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

        return response.data[0]

    async def _enrich_filing(self, filing: dict, pages: List[Dict], enriched_attachments: List[Dict]):
        """Generate LLM summary for the filing using attachment summaries + filing content"""
        header = self.filing_summarizer.build_header(self.company, filing)

        first_pages = pages[:10]

        result = await self.filing_summarizer.summarize(
            pages=first_pages,
            attachment_summaries=enriched_attachments,
            header=header
        )

        if result and (result.get("title") or result.get("summary")):
            self.database.table("filings").update({
                "title": result["title"],
                "summary": result["summary"]
            }).eq("id", filing['id']).execute()
