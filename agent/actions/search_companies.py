import pandas as pd
from typing import List
from pydantic import BaseModel, Field, ValidationError

from agent.actions.base_action import BaseAction
from agent.message import Action, Message


class SearchCompanies(BaseModel):
    """Search for companies by name. Returns matching companies with their symbols and metadata.

    - Use this when you don't know the ticker symbol for a company
    - Performs case-insensitive partial name matching
    - Returns up to 10 results with ticker symbols, industry, sector, and exchanges
    """
    query: str = Field(description="Company name or partial name to search for (e.g., 'Apple', 'Microsoft')")


class SearchCompaniesAction(BaseAction):
    name: str = 'SearchCompanies'
    schema = SearchCompanies
    limit: int = 10

    async def call(self, action: Action):
        """Searches companies by name"""
        self.log_start("SearchCompanies")

        try:
            args = self.validate(action)
        except RuntimeError as e:

            self.log_error(f"Validation failed: {e}")
            return Message(role="tool", status="completed", content=str(e), error=True, action_id=action.id)

        params = f"'{args.query}'"
        self.log_start("SearchCompanies", params)

        result = (
            self.database
            .table("companies")
            .select("id,name,symbols,exchanges,industry,sector,market_cap")
            .execute()
        )

        query_lower = args.query.lower()
        matches = [
            c for c in result.data
            if query_lower in c['name'].lower()
        ]

        matches = matches[:self.limit]

        if not matches:
            self.log_done("No companies found")
            return Message(
                role="tool",
                status="completed",
                content=f"No companies found matching '{args.query}'",
                action_id=action.id
            )

        content = self._format_to_md(matches)

        summary = f"Found {len(matches)} compan{'y' if len(matches) == 1 else 'ies'}"
        self.log_done(summary)

        return Message(role="tool", status="completed", content=content, action_id=action.id)

    @staticmethod
    def validate(action: Action) -> SearchCompanies:
        """Validates the action against the Pydantic schema"""
        try:
            return SearchCompanies(**action.body)
        except ValidationError as e:
            raise RuntimeError(f"Validation failed: {e}") from e

    @staticmethod
    def _format_to_md(companies: List[dict]) -> str:
        """Formats companies as markdown table"""
        def fmt_items(v):
            return ",".join(v) if isinstance(v, list) else ""

        def fmt_market_cap(v):
            if v is None:
                return "-"
            if v >= 1e12:
                return f"${v/1e12:.1f}T"
            elif v >= 1e9:
                return f"${v/1e9:.1f}B"
            elif v >= 1e6:
                return f"${v/1e6:.1f}M"
            else:
                return f"${v:,.0f}"

        rows = []
        for c in companies:
            rows.append({
                "name": c['name'],
                "symbols": fmt_items(c['symbols']),
                "exchanges": fmt_items(c['exchanges']),
                "sector": c.get('sector') or '-',
                "industry": c.get('industry') or '-',
                "market_cap": fmt_market_cap(c.get('market_cap'))
            })

        df = pd.DataFrame(rows, columns=["name", "symbols", "exchanges", "sector", "industry", "market_cap"])
        return df.to_markdown(index=False)
