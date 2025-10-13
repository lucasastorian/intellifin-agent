from abc import ABC, abstractmethod
from typing import List
from pipeline.chunker.markdown_chunker import MarkdownChunker


class BaseEmbeddingGenerator(ABC):
    """Base class for embedding generators with shared header building logic"""

    def __init__(self, company: dict, filing: dict, chunk_size: int = 512, chunk_overlap: int = 128):
        self.company = company
        self.filing = filing
        self.chunker = MarkdownChunker(chunk_size=chunk_size, chunk_overlap=chunk_overlap)

    def _build_company_header(self) -> str:
        """Build company name and ticker header"""
        name = self.company.get('name', '')
        symbols = self.company.get('symbols', [])
        exchanges = self.company.get('exchanges', [])
        ticker = f"{symbols[0]} - {exchanges[0]}" if symbols and exchanges else symbols[0] if symbols else ""

        return f"# {name}{f' ({ticker})' if ticker else ''}" if name else ""

    def _build_sector_industry(self) -> str:
        """Build sector and industry line"""
        sector = self.company.get('sector', '')
        industry = self.company.get('industry', '')

        return f"{f'Sector: {sector}' if sector else ''}{' | ' if sector and industry else ''}{f'Industry: {industry}' if industry else ''}" if sector or industry else ""

    def _build_filing_metadata(self, fiscal_year: int = None, fiscal_period: str = None) -> str:
        """Build filing metadata line"""
        form = self.filing.get('form', '')
        filing_date = self.filing.get('filing_date', '')
        report_date = self.filing.get('report_date', '')

        period_str = f"FY {fiscal_year}{f' {fiscal_period}' if fiscal_period and fiscal_period != 'FY' else ''}" if fiscal_year else ""

        return f"{f'Form {form}' if form else ''}{f' | {period_str}' if period_str else ''}{f' | Filed: {filing_date}' if filing_date else ''}{f' | Period Ending: {report_date}' if report_date else ''}"

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
