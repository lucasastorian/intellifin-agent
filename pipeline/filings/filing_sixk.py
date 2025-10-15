import asyncio
from typing import List, Dict
from edgar.xbrl import XBRL

from pipeline.filings.base_filing import BaseFiling
from pipeline.enrichment.openai_client import OpenAIClient
from pipeline.enrichment.filing_summarizer import FilingSummarizer
from pipeline.enrichment.section_embedding_generator import SectionEmbeddingGenerator


class FilingSixK(BaseFiling):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.openai_client = OpenAIClient()
        self.filing_summarizer = FilingSummarizer(self.openai_client)

    async def upsert(self):
        """Upserts the 6-K filing"""
        xbrl = await self._load_xbrl()

        # Upsert raw filing + pages
        filing = await self._upsert_filing(xbrl=xbrl)
        pages = await self._upsert_filing_pages(filing_id=filing['id'])

        # Use transaction to batch all embeddings from chunks/attachments
        async with self.database.transaction():
            await self._upsert_filing_chunks(pages=pages, filing=filing)
            attachment_data = await self._upsert_attachments_and_pages(filing_id=filing['id'])

        # Metadata update + complete
        await self._update_filing_counts(num_pages=len(pages), num_attachments=len(attachment_data),
                                         filing_id=filing['id'])
        await self._mark_synced(filing_id=filing['id'])

    async def _upsert_filing(self, xbrl: XBRL) -> dict:
        """Creates a filing record"""
        response = await self.database.table("filings").upsert({
            "form": self.filing.form,
            "amendment": self.filing.form == "6-K/A",
            "filing_date": self.filing_date,
            "report_date": self.report_date,
            "accession_number": self.accession_number,
            "company_id": self.company_id
        }, on_conflict="accession_number").execute()

        return response.data[0]

    async def _upsert_filing_chunks(self, pages: List[dict], filing: dict):
        """For a current report, we embed the pages (excluding the cover page) and save in filing_section_chunks"""
        generator = SectionEmbeddingGenerator(self.company, filing=filing, chunk_size=1024, chunk_overlap=0)

        # NOTE: Using the section embedding generator here is questionable...
        await generator.embed(section_type="current_report", pages=pages[1:],
                              fiscal_period=filing.get("fiscal_period"), fiscal_year=filing.get("fiscal_year"))

    # async def _enrich_filing(self, filing: dict, pages: List[Dict], enriched_attachments: List[Dict]):
    #     """Generate LLM summary for the 6-K filing using attachment summaries + filing content"""
    #     header = self.filing_summarizer.build_header(self.company, filing)
    #
    #     first_pages = pages[:10]
    #
    #     result = await self.filing_summarizer.summarize(
    #         pages=first_pages,
    #         attachment_summaries=enriched_attachments,
    #         header=header
    #     )
    #
    #     # Update filing with title + summary
    #     if result and (result.get("title") or result.get("summary")):
    #         await self.database.table("filings").update({
    #             "title": result["title"],
    #             "summary": result["summary"]
    #         }).eq("id", filing['id']).execute()
