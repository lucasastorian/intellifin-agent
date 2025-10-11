from typing import Dict, List
from pipeline.enrichment.base_client import BaseLLMClient
from pipeline.enrichment.models import AttachmentSummary


class AttachmentSummarizer:
    """Handles LLM-based summarization of filing attachments"""

    SYSTEM_PROMPT = """Generate a concise title and 2-3 sentence summary. Include: document type, parties, key details (amounts, dates, terms), and purpose."""

    def __init__(self, client: BaseLLMClient):
        self.client = client

    async def summarize(self, pages: List[Dict], header: str) -> Dict[str, str]:
        """
        Generate title and summary for an attachment

        Args:
            pages: List of page dicts with 'page' and 'content' keys (first 5 pages)
            header: Context header (company, filing, etc.)

        Returns:
            Dict with 'title' and 'summary' keys
        """
        page_contents = "\n---\n".join([p['content'] for p in pages])
        user_message = f"{header}\n{page_contents}"

        # Limit to 5k chars
        if len(user_message) > 5000:
            user_message = user_message[:5000]

        result = await self.client.parse(
            system_prompt=self.SYSTEM_PROMPT,
            user_message=user_message,
            response_model=AttachmentSummary
        )

        return {"title": result.title, "summary": result.summary}

    @staticmethod
    def build_header(company_data: dict, filing_data: dict) -> str:
        """Build context header for LLM prompt"""
        parts = []

        name = company_data.get('name')
        symbols = company_data.get('symbols', [])
        exchanges = company_data.get('exchanges', [])
        ticker = f"{symbols[0]} - {exchanges[0]}" if symbols and exchanges else symbols[0] if symbols else ""

        if name:
            parts.append(f"Company: {name}{f' ({ticker})' if ticker else ''}")

        sector = company_data.get('sector')
        industry = company_data.get('industry')
        if sector or industry:
            sector_str = f"Sector: {sector}" if sector else ""
            industry_str = f"Industry: {industry}" if industry else ""
            parts.append(" | ".join(filter(None, [sector_str, industry_str])))

        form = filing_data.get('form')
        filing_date = filing_data.get('filing_date')
        items = filing_data.get('items')

        if form:
            filing_parts = [f"Form: {form}"]
            if filing_date:
                filing_parts.append(f"Filed: {filing_date}")
            if items:
                filing_parts.append(f"Items: {', '.join(items)}")
            parts.append(" | ".join(filing_parts))

        return "\n".join(parts)
