from abc import ABC, abstractmethod
from typing import List
from pipeline.chunker.markdown_chunker import MarkdownChunker


class BaseEmbeddingGenerator(ABC):
    """Base class for embedding generators with shared header building logic"""

    def __init__(self, company: dict, filing: dict):
        self.company = company
        self.filing = filing
        self.chunker = MarkdownChunker()

    def _build_company_header(self) -> str:
        """Build company name and ticker header"""
        name = self.company.get('name')
        symbols = self.company.get('symbols', [])
        exchanges = self.company.get('exchanges', [])
        ticker = f"{symbols[0]} - {exchanges[0]}" if symbols and exchanges else symbols[0] if symbols else ""

        if name:
            return f"# {name}{f' ({ticker})' if ticker else ''}"
        return ""

    def _build_sector_industry(self) -> str:
        """Build sector and industry line"""
        sector = self.company.get('sector')
        industry = self.company.get('industry')
        if sector or industry:
            sector_str = f"Sector: {sector}" if sector else ""
            industry_str = f"Industry: {industry}" if industry else ""
            return " | ".join(filter(None, [sector_str, industry_str]))
        return ""

    def _build_filing_metadata(self, fiscal_year: int = None, fiscal_period: str = None) -> str:
        """Build filing metadata line"""
        form = self.filing.get('form')
        filing_date = self.filing.get('filing_date')
        report_date = self.filing.get('report_date')

        filing_parts = []
        if form:
            filing_parts.append(f"Form {form}")
        if fiscal_year:
            period_str = f"FY {fiscal_year}"
            if fiscal_period and fiscal_period != 'FY':
                period_str += f" {fiscal_period}"
            filing_parts.append(period_str)
        if filing_date:
            filing_parts.append(f"Filed: {filing_date}")
        if report_date:
            filing_parts.append(f"Period Ending: {report_date}")

        return " | ".join(filing_parts) if filing_parts else ""

    @abstractmethod
    def _build_context_specific_header(self, **kwargs) -> str:
        """Build context-specific header (e.g., note title, section name, attachment info)"""
        raise NotImplementedError

    def _build_header(self, **kwargs) -> str:
        """Build complete header combining all parts"""
        parts = []

        company_header = self._build_company_header()
        if company_header:
            parts.append(company_header)

        sector_industry = self._build_sector_industry()
        if sector_industry:
            parts.append(sector_industry)

        filing_metadata = self._build_filing_metadata(
            kwargs.get('fiscal_year'),
            kwargs.get('fiscal_period')
        )
        if filing_metadata:
            parts.append(filing_metadata)

        context_header = self._build_context_specific_header(**kwargs)
        if context_header:
            parts.append(context_header)

        return "\n".join(parts)

    async def embed(self, pages: List[dict], **kwargs) -> List[dict]:
        """
        Generate embedding chunks with contextual header

        Args:
            pages: List of page dicts with 'page' and 'content' keys
            **kwargs: Context-specific arguments

        Returns:
            List of chunk objects with embedding_text
        """
        header = self._build_header(**kwargs)
        chunks = self.chunker.split(pages=pages, header=header)
        return chunks
