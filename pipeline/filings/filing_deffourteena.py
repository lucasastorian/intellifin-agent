from typing import List
from edgar.xbrl import XBRL

from pipeline.filings.base_filing import BaseFiling
from pipeline.enrichment.section_embedding_generator import SectionEmbeddingGenerator


class FilingDefFourteenA(BaseFiling):

    async def upsert(self):
        """Upserts the DEF 14A filing and chunks the proxy statement content"""
        async with self.database.batch_embeddings():
            xbrl = await self._load_xbrl()

            filing = await self._upsert_filing(xbrl=xbrl)
            pages = await self._upsert_filing_pages(filing_id=filing['id'])

            await self._upsert_filing_chunks(pages=pages, filing=filing)

        await self._update_filing_counts(num_pages=len(pages), num_attachments=0, filing_id=filing['id'])
        await self._mark_synced(filing_id=filing['id'])

    async def _upsert_filing(self, xbrl: XBRL) -> dict:
        """Creates a filing record"""
        response = await self.database.table("filings").upsert({
            "form": self.filing.form,
            "amendment": self.filing.form == "DEF 14A/A",
            "filing_date": self.filing_date,
            "report_date": self.report_date,
            "accession_number": self.accession_number,
            "company_id": self.company_id
        }, on_conflict="accession_number").execute()

        return response.data[0]

    async def _upsert_filing_chunks(self, pages: List[dict], filing: dict):
        """Chunk the entire proxy statement and save in filing_section_chunks"""
        generator = SectionEmbeddingGenerator(self.company, filing=filing, chunk_size=1024, chunk_overlap=0)

        chunks = await generator.embed(
            section_type="proxy_statement",
            pages=pages,
            fiscal_period=filing.get("fiscal_period"),
            fiscal_year=filing.get("fiscal_year")
        )

        all_chunks = []
        for i, chunk in enumerate(chunks):
            all_chunks.append({
                "index": i,
                "section": "proxy_statement",
                "page": chunk.page,
                "pages": chunk.pages,
                "embedding": chunk.embedding_text,
                "has_table": chunk.has_table,
                "filing_id": filing['id'],
                "company_id": self.company_id
            })

        if all_chunks:
            await self.database.table("filing_section_chunks").upsert(
                all_chunks,
                on_conflict="filing_id,section,index"
            ).execute()
