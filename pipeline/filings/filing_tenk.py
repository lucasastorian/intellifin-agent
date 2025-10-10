import asyncio
import logging
from edgar.xbrl import XBRL
from typing import Optional, List, Literal, Dict

from pipeline.filings.base_filing import BaseFiling
from pipeline.parsers.parser import Parser
from pipeline.parsers.section_extractor import SectionExtractor

logger = logging.getLogger(__name__)


class FilingTenK(BaseFiling):

    async def upsert(self):
        """Upserts the 10-K filing and associated pages"""
        xbrl = await self._load_xbrl()

        if xbrl is None:
            logger.warning(f"Filing {self.filing.form} ({self.accession_number}) missing an XBRL attachment")

        filing_id = await self._upsert_filing(xbrl=xbrl)
        filing = (await self.database.table("filings").select("*").eq("id", filing_id).execute()).data[0]
        pages = await self._upsert_filing_pages(filing_id=filing_id)
        await self._upsert_filing_notes(filing_id=filing_id)
        await self._upsert_financial_statements(xbrl=xbrl, filing_id=filing_id)
        attachment_data = await self._upsert_attachments(filing_id=filing_id)
        await self._upsert_filing_chunks(pages=pages, filing_id=filing_id, filing_type='10-K')

        # Run async enrichment for attachments
        if attachment_data:
            try:
                await self._enrich_attachments(attachment_data, filing)
            except Exception as e:
                print(f"ERROR enriching 10-K attachments {self.accession_number}: {e}")
                import traceback
                traceback.print_exc()

        await self._update_filing_counts(filing_id=filing_id)

    async def _upsert_filing(self, xbrl: Optional[XBRL]) -> int:
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
            "company_id": self.company_id
        }, on_conflict="accession_number").execute()

        return response.data[0]['id']

    async def _upsert_attachments(self, filing_id: int) -> List[Dict]:
        """Upserts material attachments (exhibits) for 10-K filings"""
        # Only pull material exhibits: contracts, M&A, debt instruments, press releases
        material_exhibit_prefixes = ["2", "4", "10", "99"]

        def material_filter(exhibit_number: str) -> bool:
            prefix = exhibit_number.split(".")[0] if "." in exhibit_number else exhibit_number
            return prefix in material_exhibit_prefixes

        return super()._upsert_attachments(filing_id, exhibit_filter=material_filter)

    def _build_embedding_header(self, company_data: dict, section_type: str, fiscal_year: int = None, fiscal_period: str = None) -> str:
        """Build a rich contextual header for embedding"""
        parts = []

        # Company header
        name = company_data.get('name')
        symbols = company_data.get('symbols', [])
        exchanges = company_data.get('exchanges', [])
        ticker = f"{symbols[0]} - {exchanges[0]}" if symbols and exchanges else symbols[0] if symbols else ""

        if name:
            parts.append(f"# {name}{f' ({ticker})' if ticker else ''}")

        # Sector/Industry
        sector = company_data.get('sector')
        industry = company_data.get('industry')
        if sector or industry:
            sector_str = f"Sector: {sector}" if sector else ""
            industry_str = f"Industry: {industry}" if industry else ""
            parts.append(" | ".join(filter(None, [sector_str, industry_str])))

        # Filing metadata
        filing_parts = [f"Form {self.filing.form}"]
        if fiscal_year:
            period_str = f"FY {fiscal_year}"
            if fiscal_period and fiscal_period != 'FY':
                period_str += f" {fiscal_period}"
            filing_parts.append(period_str)
        filing_parts.append(f"Filed: {self.filing_date}")
        if self.report_date:
            filing_parts.append(f"Period Ending: {self.report_date}")
        parts.append(" | ".join(filing_parts))

        # Section
        section_name = section_type.replace('_', ' ').title()
        parts.append(f"\n## {section_name}\n")

        return "\n".join(parts)

    async def _upsert_filing_section_pages(self, sections: List[dict], filing_id: int):
        """Upserts raw section pages before chunking"""
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
                        "filing_id": filing_id,
                        "company_id": self.company_id
                    })

        if all_pages:
            await self.database.table("filing_section_pages").upsert(
                all_pages,
                on_conflict="filing_id,section,page"
            ).execute()

    async def _upsert_filing_chunks(self, pages: List[dict], filing_id: int, filing_type: Literal['10-K', '10-Q', '20-F']):
        """Chunks the filing pages and upserts them"""
        company_data = (await self.database.table("companies").select("*").eq("id", self.company_id).execute()).data[0]

        filing_record = (await self.database.table("filings").select("fiscal_year,fiscal_period").eq("id", filing_id).execute()).data[0]
        fiscal_year = filing_record.get('fiscal_year')
        fiscal_period = filing_record.get('fiscal_period')

        extractor = SectionExtractor(pages=pages, filing_type=filing_type)
        sections = extractor.get_sections()

        self._upsert_filing_section_pages(sections, filing_id)

        all_chunks = []

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

                header = self._build_embedding_header(company_data, section_type, fiscal_year, fiscal_period)

                chunks = self.markdown_chunker.split(pages=section['pages'], header=header)

                for i, chunk in enumerate(chunks):
                    all_chunks.append({
                        "index": i,
                        "section": section_type,
                        "page": chunk.page,
                        "pages": chunk.pages,
                        "embedding": chunk.embedding_text,  # Uses header + content
                        "has_table": chunk.has_table,
                        "filing_id": filing_id,
                        "company_id": self.company_id
                    })

        if all_chunks:
            await self.database.table("filing_section_chunks").upsert(all_chunks,
                                                                on_conflict="filing_id,section,index").execute()
