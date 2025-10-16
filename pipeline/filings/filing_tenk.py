import asyncio
import logging
from edgar.xbrl import XBRL
from typing import Optional, List, Literal, Dict

from pipeline.filings.base_filing import BaseFiling
from pipeline.parsers.section_extractor import SectionExtractor
from pipeline.enrichment.section_embedding_generator import SectionEmbeddingGenerator

logger = logging.getLogger(__name__)


class FilingTenK(BaseFiling):

    async def upsert(self):
        """Upserts the 10-K filing and associated pages"""
        xbrl = await self._load_xbrl()

        # if xbrl is None:
        #     logger.warning(f"Filing {self.filing.form} ({self.accession_number}) missing an XBRL attachment")

        # Filing and core document pages
        filing = await self._upsert_filing(xbrl=xbrl)
        pages = await self._upsert_filing_pages(filing_id=filing['id'])

        # Financial Statements
        await self._upsert_financial_statements(xbrl=xbrl, filing_id=filing['id'])

        # Core filing sections + section chunks
        sections = await self._upsert_filing_section_pages(pages=pages, filing=filing, filing_type='10-K')
        await self._upsert_filing_section_chunks(sections=sections, filing=filing)

        # Filing notes and note chunks
        if self.filing.reports:
            note_ids, processed_notes = await self._upsert_filing_notes(filing_id=filing['id'])
            await self._upsert_filing_note_chunks(note_ids=note_ids, processed_notes=processed_notes, filing=filing)
        else:
            logger.warning(f"{self.accession_number} has no reports?")

        # Attachments and attachment chunks
        attachment_data = await self._upsert_attachments_and_pages(filing_id=filing['id'])

        # Metadata updates + mark filing as synced
        await self._update_filing_counts(num_pages=len(pages), num_attachments=len(attachment_data),
                                         filing_id=filing['id'])
        await self._mark_synced(filing_id=filing['id'])

        print(f"Upserted {self.filing.company} 10-K for FY ending {self.report_date}")

    async def _upsert_filing(self, xbrl: Optional[XBRL]) -> dict:
        """Creates a filing record"""
        fiscal_year = xbrl.entity_info['fiscal_year'] if xbrl else None
        fiscal_period = xbrl.entity_info['fiscal_period'] if xbrl else None

        response = await self.database.table("filings").upsert({
            "form": self.filing.form,
            "amendment": self.filing.form == "10-K/A",
            "fiscal_year": fiscal_year,
            "fiscal_period": fiscal_period,
            "filing_date": self.filing_date,
            "report_date": self.report_date,
            "accession_number": self.accession_number,
            "company_id": self.company_id,
            "synced": False  # Explicitly set to False, mark True only when fully complete
        }, on_conflict="accession_number").execute()

        return response.data[0]

    async def _upsert_attachments(self, filing_id: int) -> List[Dict]:
        """Upserts material attachments (exhibits) for 10-K filings"""
        material_exhibit_prefixes = ["2", "4", "10", "99"]

        def material_filter(exhibit_number: str) -> bool:
            prefix = exhibit_number.split(".")[0] if "." in exhibit_number else exhibit_number
            return prefix in material_exhibit_prefixes

        return await super()._upsert_attachments(filing_id, exhibit_filter=material_filter)

    async def _upsert_filing_section_pages(self, pages: List[dict], filing: dict,
                                           filing_type: Literal['10-K', '10-Q', '20-F']):
        """Chunks the filing pages and upserts them"""
        extractor = SectionExtractor(pages=pages, filing_type=filing_type)
        sections = extractor.get_sections()

        all_pages = []

        for section in sections:
            if section['item'] in ['ITEM 1', 'ITEM 1A', 'ITEM 2', 'ITEM 3', 'ITEM 5', 'ITEM 7',
                                   'ITEM 7A', 'ITEM 9A', 'ITEM 9B']:
                section_type = {
                    "ITEM 1": "business",
                    "ITEM 1A": "risk_factors",
                    "ITEM 2": "properties",
                    "ITEM 3": "legal_proceedings",
                    "ITEM 5": "market_equity_matters",
                    "ITEM 7": "md&a",
                    "ITEM 7A": "market_risk",
                    "ITEM 9A": "controls_procedures",
                    "ITEM 9B": "other_information"
                }.get(section['item'])

                if not section_type:
                    continue

                for page in section['pages']:
                    all_pages.append({
                        "section": section_type,
                        "page": page['page'],
                        "content": page['content'],
                        "has_table": page.get('has_table', False),
                        "filing_id": filing['id'],
                        "company_id": self.company_id
                    })

        if all_pages:
            await self.database.table("filing_section_pages").upsert(
                all_pages,
                on_conflict="filing_id,section,page"
            ).execute()

        return sections

    async def _upsert_filing_section_chunks(self, sections: List[dict], filing: dict):
        """Upserts chunks for each filing section separately for contextualized embeddings"""
        fiscal_year = filing.get('fiscal_year')
        fiscal_period = filing.get('fiscal_period')

        generator = SectionEmbeddingGenerator(self.company, filing, chunk_size=1024, chunk_overlap=0)

        # Upsert each section's chunks separately to preserve document boundaries
        # for contextualized embeddings (voyage-context-3 compatibility)
        for section in sections:
            if section['item'] in ['ITEM 1', 'ITEM 1A', 'ITEM 2', 'ITEM 3', 'ITEM 5', 'ITEM 6', 'ITEM 7',
                                   'ITEM 7A', 'ITEM 9A', 'ITEM 9B']:
                section_type = {
                    "ITEM 1": "business",
                    "ITEM 1A": "risk_factors",
                    "ITEM 2": "properties",
                    "ITEM 3": "legal_proceedings",
                    "ITEM 5": "market_equity_matters",
                    "ITEM 6": "selected_financial_data",  # Deprecated as of 2021. [RESERVED].
                    "ITEM 7": "md&a",
                    "ITEM 7A": "market_risk",
                    "ITEM 9A": "controls_procedures",
                    "ITEM 9B": "other_information"
                }.get(section['item'])

                if not section_type:
                    continue

                chunks = await generator.embed(section_type, section['pages'], fiscal_year=fiscal_year,
                                               fiscal_period=fiscal_period)

                section_chunks = []
                for i, chunk in enumerate(chunks):
                    section_chunks.append({
                        "index": i,
                        "section": section_type,
                        "page": chunk.page,
                        "pages": chunk.pages,
                        "embedding": chunk.embedding_text,  # Uses header + content
                        "has_table": chunk.has_table,
                        "filing_id": filing['id'],
                        "company_id": self.company_id
                    })

                if section_chunks:
                    await self.database.table("filing_section_chunks").upsert(section_chunks,
                                                                              on_conflict="filing_id,section,index").execute()
