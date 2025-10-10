from abc import ABC, abstractmethod

from database import Database


class BaseDocument(ABC):

    def __init__(self, filing_id: int,  database: Database):
        self.filing_id = filing_id
        self.database = database

    @abstractmethod
    def preview(self) -> str:
        """Generate a string preview of the document"""
        raise NotImplementedError

    async def _load_filing(self) -> dict:
        """Loads the filing from the database"""
        return await self.database.table("company_filings").select("*").eq("filing_id", self.filing_id).single().execute()

    async def _load_attachments(self) -> dict:
        """Loads the filing from the database"""
        return await self.database.table("filing_attachments").select("*").eq("filing_id", self.filing_id).single().execute()

    async def _load_notes(self) -> dict:
        """Loads the filing from the database"""
        return await self.database.table("filing_notes").select("*").eq("filing_id", self.filing_id).single().execute()

    # @staticmethod
    # def build_header(company_data: dict, filing_data: dict) -> str:
    #     """Build context header for LLM prompt"""
    #     parts = []
    #
    #     name = company_data.get('name')
    #     symbols = company_data.get('symbols', [])
    #     exchanges = company_data.get('exchanges', [])
    #     ticker = f"{symbols[0]} - {exchanges[0]}" if symbols and exchanges else symbols[0] if symbols else ""
    #
    #     if name:
    #         parts.append(f"Company: {name}{f' ({ticker})' if ticker else ''}")
    #
    #     sector = company_data.get('sector')
    #     industry = company_data.get('industry')
    #     if sector or industry:
    #         sector_str = f"Sector: {sector}" if sector else ""
    #         industry_str = f"Industry: {industry}" if industry else ""
    #         parts.append(" | ".join(filter(None, [sector_str, industry_str])))
    #
    #     form = filing_data.get('form')
    #     filing_date = filing_data.get('filing_date')
    #     items = filing_data.get('items')
    #
    #     if form:
    #         filing_parts = [f"Form: {form}"]
    #         if filing_date:
    #             filing_parts.append(f"Filed: {filing_date}")
    #         if items:
    #             filing_parts.append(f"Items: {', '.join(items)}")
    #         parts.append(" | ".join(filing_parts))
    #
    #     return "\n".join(parts)
