import logging
from typing import Optional, List, Literal
from edgar.xbrl import XBRL

from pipeline.filings.base_filing import BaseFiling
from pipeline.parsers.section_extractor import SectionExtractor

logger = logging.getLogger(__name__)


class FilingTwentyF(BaseFiling):

    async def upsert(self):
        """Upserts the DEF 14A filing"""
        xbrl = await self._load_xbrl()

        if xbrl is None:
            logger.warning(f"Filing {self.filing.form} ({self.accession_number}) missing an XBRL attachment")

        filing_id = self._upsert_filing(xbrl=xbrl)
        pages = await self._upsert_filing_pages(filing_id=filing_id)
        await self._upsert_filing_notes(filing_id=filing_id)
        self._upsert_financial_statements(xbrl=xbrl, filing_id=filing_id)

        self._upsert_filing_chunks(pages=pages, filing_id=filing_id)

        # Update filing counts after all processing is complete
        self._update_filing_counts(filing_id=filing_id)

    def _upsert_filing(self, xbrl: Optional[XBRL]) -> int:
        """Creates a filing record"""
        fiscal_year = xbrl.entity_info['fiscal_year'] if xbrl else None
        fiscal_period = xbrl.entity_info['fiscal_period'] if xbrl else None

        response = self.database.table("filings").upsert({
            "form": self.filing.form,
            "amendment": self.filing.form == "20-F/A",
            "fiscal_year": fiscal_year,
            "fiscal_period": fiscal_period,
            "filing_date": self.filing_date,
            "report_date": self.report_date,
            "accession_number": self.accession_number,
            "company_id": self.company_id
        }, on_conflict="accession_number").execute()

        return response.data[0]['id']

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

    def _upsert_filing_section_pages(self, sections: List[dict], filing_id: int):
        """Upserts raw section pages before chunking"""
        all_pages = []

        for section in sections:
            section_item = section['item']

            # Map 20-F items to section types
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
            self.database.table("filing_section_pages").upsert(
                all_pages,
                on_conflict="filing_id,section,page"
            ).execute()

    def _upsert_filing_chunks(self, pages: List[dict], filing_id: int):
        """Chunks the filing pages and upserts them"""
        # Get company data for header
        company_data = self.database.table("companies").select("*").eq("id", self.company_id).execute().data[0]

        # Get fiscal info
        filing_record = self.database.table("filings").select("fiscal_year,fiscal_period").eq("id", filing_id).execute().data[0]
        fiscal_year = filing_record.get('fiscal_year')
        fiscal_period = filing_record.get('fiscal_period')

        extractor = SectionExtractor(pages=pages, filing_type="20-F")
        sections = extractor.get_sections()

        # Upsert raw section pages
        self._upsert_filing_section_pages(sections, filing_id)

        all_chunks = []

        for section in sections:
            section_item = section['item']

            # Map 20-F items to section types
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

            # Build embedding header
            header = self._build_embedding_header(company_data, section_type, fiscal_year, fiscal_period)

            # Chunk with header
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
            self.database.table("filing_section_chunks").upsert(all_chunks,
                                                                on_conflict="filing_id,section,index").execute()
