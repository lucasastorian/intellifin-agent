import asyncio
import logging
from typing import Optional, List, Literal, Dict
from edgar.xbrl import XBRL

from pipeline.filings.base_filing import BaseFiling
from pipeline.parsers.parser import Parser
from pipeline.parsers.section_extractor import SectionExtractor

logger = logging.getLogger(__name__)


class FilingTenQ(BaseFiling):

    def upsert(self):
        """Upserts the 10-Q filing and associated pages"""
        xbrl = self.filing.xbrl()

        if xbrl is None:
            logger.warning(f"Filing {self.filing.form} ({self.accession_number}) missing an XBRL attachment")

        filing_id = self._upsert_filing(xbrl=xbrl)
        filing = self.database.table("filings").select("*").eq("id", filing_id).execute().data[0]
        pages = self._upsert_filing_pages(filing_id=filing_id)
        self._upsert_filing_notes(filing_id=filing_id)
        self._upsert_financial_statements(xbrl=xbrl, filing_id=filing_id)
        attachment_data = self._upsert_attachments(filing_id=filing_id)
        self._upsert_filing_chunks(pages=pages, filing_id=filing_id)

        # Run async enrichment for attachments
        if attachment_data:
            asyncio.run(self._enrich_attachments(attachment_data, filing))

        # Update filing counts after all processing is complete
        self._update_filing_counts(filing_id=filing_id)

    def _upsert_filing(self, xbrl: Optional[XBRL]) -> int:
        """Creates a filing record"""
        fiscal_year = xbrl.entity_info['fiscal_year'] if xbrl else None
        fiscal_period = xbrl.entity_info['fiscal_period'] if xbrl else None

        response = self.database.table("filings").upsert({
            "form": self.filing.form,
            "amendment": self.filing.form == "10-Q/A",
            "fiscal_year": fiscal_year,
            "fiscal_period": fiscal_period,
            "filing_date": self.filing_date,
            "report_date": self.report_date,
            "accession_number": self.accession_number,
            "company_id": self.company_id
        }, on_conflict="accession_number").execute()

        return response.data[0]['id']

    def _upsert_attachments(self, filing_id: int) -> List[Dict]:
        """Upserts material attachments (exhibits) for 10-Q filings, returns data for enrichment"""
        documents = self.filing.attachments.documents
        attachment_data = []

        # Only pull material exhibits: contracts, M&A, debt instruments, press releases
        material_exhibit_prefixes = ["2", "4", "10", "99"]

        for document in documents:
            if not document.document_type or not document.document_type.startswith("EX-"):
                continue

            exhibit_number = document.document_type.replace("EX-", "")

            # Filter to material exhibits only
            prefix = exhibit_number.split(".")[0] if "." in exhibit_number else exhibit_number
            if prefix not in material_exhibit_prefixes:
                continue

            if not document.is_html():
                continue

            parser = Parser(content=document.content)
            pages = parser.get_pages()

            if not pages:
                continue

            # Infer attachment type from exhibit number
            attachment_type = self.infer_attachment_type(exhibit_number)

            # Upsert attachment metadata
            attachment_response = self.database.table("filing_attachments").upsert({
                "exhibit_number": exhibit_number,
                "filename": document.document or f"ex-{exhibit_number}",
                "description": document.description,
                "num_pages": len(pages),
                "type": attachment_type,
                "filing_id": filing_id,
                "company_id": self.company_id
            }, on_conflict="filing_id,exhibit_number").execute()

            if not attachment_response.data:
                continue

            attachment_id = attachment_response.data[0]['id']

            # Upsert attachment pages
            self.database.table("filing_attachment_pages").upsert([{
                "page": page['page'],
                "content": page['content'],
                "attachment_id": attachment_id,
                "filing_id": filing_id,
                "company_id": self.company_id
            } for page in pages], on_conflict="attachment_id,page").execute()

            # Upsert attachment chunks
            self._upsert_filing_attachment_chunks(pages=pages, attachment_id=attachment_id, filing_id=filing_id)

            attachment_data.append({
                "attachment_id": attachment_id,
                "exhibit_number": exhibit_number,
                "pages": pages[:10]
            })

        return attachment_data

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
            if section['item'] in ['ITEM 1', 'ITEM 1A', 'ITEM 2', 'ITEM 3', 'ITEM 4']:
                # Map items based on part context
                if section['part'] == 'PART I':
                    section_type = {
                        "ITEM 2": "md&a",
                        "ITEM 3": "market_risk",
                        "ITEM 4": "controls_procedures"
                    }.get(section['item'])
                elif section['part'] == 'PART II':
                    section_type = {
                        "ITEM 1": "legal_proceedings",
                        "ITEM 1A": "risk_factors",
                        "ITEM 2": "unregistered_sales_equity"
                    }.get(section['item'])
                else:
                    section_type = None

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

        extractor = SectionExtractor(pages=pages, filing_type="10-Q")
        sections = extractor.get_sections()

        # Upsert raw section pages
        self._upsert_filing_section_pages(sections, filing_id)

        all_chunks = []

        for section in sections:
            # Part I items: 2 (MD&A), 3 (Market Risk), 4 (Controls)
            # Part II items: 1 (Legal), 1A (Risk Factors), 2 (Unregistered Sales)
            if section['item'] in ['ITEM 1', 'ITEM 1A', 'ITEM 2', 'ITEM 3', 'ITEM 4']:
                # Map items based on part context
                if section['part'] == 'PART I':
                    section_type = {
                        "ITEM 2": "md&a",
                        "ITEM 3": "market_risk",
                        "ITEM 4": "controls_procedures"
                    }.get(section['item'])
                elif section['part'] == 'PART II':
                    section_type = {
                        "ITEM 1": "legal_proceedings",
                        "ITEM 1A": "risk_factors",
                        "ITEM 2": "unregistered_sales_equity"
                    }.get(section['item'])
                else:
                    section_type = None

                if not section_type:
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
                        "content": chunk.content,
                        "embedding": chunk.embedding_text,  # Uses header + content
                        "has_table": chunk.has_table,
                        "filing_id": filing_id,
                        "company_id": self.company_id
                    })

        if all_chunks:
            self.database.table("filing_section_chunks").upsert(all_chunks,
                                                                on_conflict="filing_id,section,index").execute()
