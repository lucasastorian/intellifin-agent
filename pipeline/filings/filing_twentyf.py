import logging
from typing import Optional, List
from edgar.xbrl import XBRL

from pipeline.filings.base_filing import BaseFiling
from pipeline.parsers.section_extractor import SectionExtractor
from pipeline.enrichment.section_embedding_generator import SectionEmbeddingGenerator

logger = logging.getLogger(__name__)


class FilingTwentyF(BaseFiling):

    async def upsert(self):
        """Upserts the DEF 14A filing"""
        xbrl = await self._load_xbrl()

        if xbrl is None:
            logger.warning(f"Filing {self.filing.form} ({self.accession_number}) missing an XBRL attachment")

        filing = await self._upsert_filing(xbrl=xbrl)
        pages = await self._upsert_filing_pages(filing_id=filing['id'])
        await self._upsert_filing_notes(filing_id=filing['id'], filing=filing)
        await self._upsert_financial_statements(xbrl=xbrl, filing_id=filing['id'])
        sections = await self._upsert_filing_section_pages(pages=pages, filing_id=filing['id'])
        await self._upsert_filing_section_chunks(sections=sections, filing=filing)

        # TODO: Upsert attachments here?
        await self._update_filing_counts(num_pages=len(pages), num_attachments=None, filing_id=filing['id'])

        await self._mark_synced(filing_id=filing['id'])

    async def _upsert_filing(self, xbrl: Optional[XBRL]) -> dict:
        """Creates a filing record"""
        fiscal_year = xbrl.entity_info['fiscal_year'] if xbrl else None
        fiscal_period = xbrl.entity_info['fiscal_period'] if xbrl else None

        response = await self.database.table("filings").upsert({
            "form": self.filing.form,
            "amendment": self.filing.form == "20-F/A",
            "fiscal_year": fiscal_year,
            "fiscal_period": fiscal_period,
            "filing_date": self.filing_date,
            "report_date": self.report_date,
            "accession_number": self.accession_number,
            "company_id": self.company_id
        }, on_conflict="accession_number").execute()

        return response.data[0]

    async def _upsert_filing_section_pages(self, pages: List[dict], filing_id: int) -> List[dict]:
        """Upserts raw section pages before chunking"""

        extractor = SectionExtractor(pages=pages, filing_type="20-F")
        sections = extractor.get_sections()

        all_pages = []

        for section in sections:
            section_item = section['item']

            if section_item == 'ITEM 3':
                section_type = 'risk_factors'
            elif section_item == 'ITEM 4':
                section_type = 'business'
            elif section_item == 'ITEM 5':
                section_type = 'md&a'
            elif section_item == 'ITEM 11':
                section_type = 'market_risk'
            elif section_item in ['ITEM 16', 'ITEM 16A', 'ITEM 16B', 'ITEM 16C', 'ITEM 16D',
                                  'ITEM 16E', 'ITEM 16F', 'ITEM 16G', 'ITEM 16H', 'ITEM 16I']:
                section_type = 'controls_procedures'
            else:
                continue

            for page in section['pages']:
                all_pages.append({
                    "section": section_type,
                    "page": page['page'],
                    "content": page['content'],
                    "has_table": page.get('has_table', False),
                    "filing_id": filing_id,
                    "company_id": self.company_id
                })

        if all_pages:
            await self.database.table("filing_section_pages").upsert(
                all_pages,
                on_conflict="filing_id,section,page"
            ).execute()

        return sections

    async def _upsert_filing_section_chunks(self, sections: List[dict], filing: dict):
        """Chunks the filing pages and upserts them"""
        fiscal_year = filing.get('fiscal_year')
        fiscal_period = filing.get('fiscal_period')

        generator = SectionEmbeddingGenerator(self.company, filing=filing, chunk_size=1024, chunk_overlap=0)

        all_chunks = []

        for section in sections:
            section_item = section['item']

            if section_item == 'ITEM 3':
                section_type = 'risk_factors'
            elif section_item == 'ITEM 4':
                section_type = 'business'
            elif section_item == 'ITEM 5':
                section_type = 'md&a'
            elif section_item == 'ITEM 11':
                section_type = 'market_risk'
            elif section_item in ['ITEM 16', 'ITEM 16A', 'ITEM 16B', 'ITEM 16C', 'ITEM 16D',
                                  'ITEM 16E', 'ITEM 16F', 'ITEM 16G', 'ITEM 16H', 'ITEM 16I']:
                section_type = 'controls_procedures'
            else:
                continue

            chunks = await generator.embed(section_type, section['pages'], fiscal_year, fiscal_period)

            for i, chunk in enumerate(chunks):
                all_chunks.append({
                    "index": i,
                    "section": section_type,
                    "page": chunk.page,
                    "pages": chunk.pages,
                    "embedding": chunk.embedding_text,  # Uses header + content
                    "has_table": chunk.has_table,
                    "filing_id": filing['id'],
                    "company_id": self.company_id
                })

        if all_chunks:
            await self.database.table("filing_section_chunks").upsert(all_chunks,
                                                                      on_conflict="filing_id,section,index").execute()
