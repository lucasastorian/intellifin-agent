from typing import Dict, List
from pipeline.enrichment.base_client import BaseLLMClient
from pipeline.enrichment.models import FilingSummary


class FilingSummarizer:
    """Handles LLM-based summarization of 8-K/6-K filings"""

    SYSTEM_PROMPT = """You are an expert financial analyst summarizing SEC Form 8-K filings.

Generate a concise title and summary for this filing based on the context, attachments, and filing content provided.

Guidelines:
- Title: Create an event-focused headline (e.g., "Tesla Completes $5B Capital Raise", "Microsoft CFO Amy Hood to Retire")
- Summary: 2-3 sentences synthesizing:
  1. The material business event(s) disclosed
  2. Key financial details, dates, and parties involved
  3. Business rationale or impact
- Integrate information across all items and attachments
- Be specific with numbers, dates, and entity names
- Focus on what investors care about most"""

    def __init__(self, client: BaseLLMClient):
        self.client = client

    async def summarize(self, pages: List[Dict], attachment_summaries: List[Dict], header: str) -> Dict[str, str]:
        """
        Generate title and summary for an 8-K filing

        Args:
            pages: First 10 pages of the filing with 'page' and 'content' keys
            attachment_summaries: List of dicts with 'exhibit_number', 'title', 'summary'
            header: Context header (company, filing, items, etc.)

        Returns:
            Dict with 'title' and 'summary' keys
        """
        # Build attachment summaries section
        attachment_section = ""
        if attachment_summaries:
            attachment_section = "\n\n## Attachments:\n\n"
            for att in attachment_summaries:
                exhibit = att.get('exhibit_number', 'Unknown')
                title = att.get('title', 'No title')
                summary = att.get('summary', 'No summary')
                attachment_section += f"**Exhibit {exhibit}**: {title}\n{summary}\n\n"

        # Build pages section
        page_contents = "\n\n---\n\n".join([f"Page {p['page']}\n\n{p['content']}" for p in pages])

        user_message = f"{header}{attachment_section}\n## Filing Content:\n\n{page_contents}"

        result = await self.client.parse(
            system_prompt=self.SYSTEM_PROMPT,
            user_message=user_message,
            response_model=FilingSummary,
        )

        return {"title": result.title, "summary": result.summary}

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
        items = filing_data.get('items', [])
        items_str = ', '.join(items) if items else ''

        return f"""Company: {name}{f' ({ticker})' if ticker else ''}
{f'Sector: {sector}' if sector else ''}{' | ' if sector and industry else ''}{f'Industry: {industry}' if industry else ''}
Form: {form}{f' | Filed: {filing_date}' if filing_date else ''}{f' | Items: {items_str}' if items_str else ''}"""
