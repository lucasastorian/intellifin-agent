from typing import Dict
from pydantic import BaseModel
from pipeline.enrichment.base_client import BaseLLMClient


class NotePreview(BaseModel):
    """One-sentence preview for a filing note"""
    preview: str


class NotePreviewGenerator:
    """Generates one-sentence previews for filing notes"""

    SYSTEM_PROMPT = """Generate one sentence (15-25 words) describing what this note covers. Focus on key topics and material information."""

    def __init__(self, client: BaseLLMClient):
        self.client = client

    @staticmethod
    def build_header(company_data: dict, filing_data: dict) -> str:
        """Build context header for LLM prompt"""
        name = company_data.get('name', '')
        symbols = company_data.get('symbols', [])
        exchanges = company_data.get('exchanges', [])
        ticker = f"{symbols[0]} - {exchanges[0]}" if symbols and exchanges else symbols[0] if symbols else ""

        sector = company_data.get('sector', '')
        industry = company_data.get('industry', '')

        form = filing_data.get('form', '')
        filing_date = filing_data.get('filing_date', '')
        fiscal_year = filing_data.get('fiscal_year')
        fiscal_period = filing_data.get('fiscal_period', '')

        period_str = f"FY {fiscal_year}{f' {fiscal_period}' if fiscal_period and fiscal_period != 'FY' else ''}" if fiscal_year else ""

        return f"""Company: {name}{f' ({ticker})' if ticker else ''}
{f'Sector: {sector}' if sector else ''}{' | ' if sector and industry else ''}{f'Industry: {industry}' if industry else ''}
Form: {form}{f' | {period_str}' if period_str else ''}{f' | Filed: {filing_date}' if filing_date else ''}"""

    async def generate(self, note_title: str, note_content: str, company_data: dict, filing_data: dict) -> str:
        """
        Generate one-sentence preview for a note

        Args:
            note_title: Original note title from filing (e.g., "Revenue")
            note_content: First ~2000 chars of note markdown content
            company_data: Company metadata dict
            filing_data: Filing metadata dict

        Returns:
            One-sentence preview string
        """
        content_preview = note_content[:1500] if len(note_content) > 1500 else note_content
        header = self.build_header(company_data, filing_data)

        user_message = f"{header}\nNote: {note_title}\n{content_preview}"

        result = await self.client.parse(
            system_prompt=self.SYSTEM_PROMPT,
            user_message=user_message,
            response_model=NotePreview
        )

        return result.preview
