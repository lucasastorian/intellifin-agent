from typing import List
from pipeline.enrichment.base_embedding_generator import BaseEmbeddingGenerator


class SectionEmbeddingGenerator(BaseEmbeddingGenerator):
    """Generates embeddings for filing sections with contextual headers"""

    def _build_context_specific_header(self, **kwargs) -> str:
        """Build section-specific header with section name"""
        section_type = kwargs.get('section_type')
        if section_type:
            section_name = section_type.replace('_', ' ').title()
            return f"\n## {section_name}\n"
        return ""

    async def embed(self, section_type: str, pages: List[dict], fiscal_year: int = None, fiscal_period: str = None) -> List[dict]:
        """
        Generate embedding chunks for a filing section with contextual header

        Args:
            section_type: Type of section (e.g., 'md&a', 'risk_factors', 'business')
            pages: List of page dicts with 'page' and 'content' keys
            fiscal_year: Fiscal year for the filing
            fiscal_period: Fiscal period for the filing (e.g., 'Q1', 'FY')

        Returns:
            List of chunk objects with embedding_text
        """
        return await super().embed(
            pages=pages,
            section_type=section_type,
            fiscal_year=fiscal_year,
            fiscal_period=fiscal_period
        )
