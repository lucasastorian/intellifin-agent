import pandas as pd
from typing import List
from pydantic import BaseModel, Field, ValidationError

from agent.actions.base_action import BaseAction
from agent.message import Action, Message
from agent.action_response import ActionResponse


class ListCompanies(BaseModel):
    """List for companies with a matching name or symbol. Returns matching companies with their symbols and metadata.

    - Use this when you don't know the exact ticker symbol or company name
    - Searches by both company name (partial, case-insensitive) and ticker symbol (exact match)
    - Returns up to 10 results with ticker symbols, industry, sector, and exchanges
    """
    query: str = Field(description="Company name, partial name, or ticker symbol to search for (e.g., 'Apple', 'AAPL', 'Microsoft')")


class ListCompaniesAction(BaseAction):
    name: str = 'ListCompanies'
    schema = ListCompanies
    limit: int = 10

    async def call(self, action: Action):
        """Searches companies by name"""
        try:
            args = ListCompanies(**action.body)
        except ValidationError as e:
            self.log_start("ListCompanies")
            self.log_error(f"Validation failed: {e}")
            return ActionResponse(
                message=Message(
                    role="tool",
                    status="completed",
                    content=str(e),
                    error=True,
                    action_id=action.id
                )
            )

        params = f"'{args.query}'"
        self.log_start("ListCompanies", params)

        name_results = await (
            self.database
            .table("companies")
            .select("id,name,symbols,exchanges,industry,sector,market_cap,delisted")
            .ilike("name", args.query)
            .limit(self.limit)
            .execute()
        )

        symbol_results = await (
            self.database
            .table("companies")
            .select("id,name,symbols,exchanges,industry,sector,market_cap,delisted")
            .contains("symbols", args.query.upper())
            .limit(self.limit)
            .execute()
        )

        seen_ids = set()
        matches = []
        for result_set in [name_results.data, symbol_results.data]:
            for company in result_set:
                if company['id'] not in seen_ids:
                    seen_ids.add(company['id'])
                    matches.append(company)
                    if len(matches) >= self.limit:
                        break
            if len(matches) >= self.limit:
                break

        if not matches:
            self.log_done("No companies found")
            return ActionResponse(
                message=Message(
                    role="tool",
                    status="completed",
                    content=f"No companies found matching '{args.query}'",
                    action_id=action.id
                )
            )

        content = self._format_to_md(matches)

        summary = f"Found {len(matches)} compan{'y' if len(matches) == 1 else 'ies'}"
        self.log_done(summary)

        return ActionResponse(
            message=Message(
                role="tool",
                status="completed",
                content=content,
                action_id=action.id
            )
        )

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
                "market_cap": fmt_market_cap(c.get('market_cap')),
                "delisted": "Yes" if c.get('delisted') else "No"
            })

        df = pd.DataFrame(rows, columns=["name", "symbols", "exchanges", "sector", "industry", "market_cap", "delisted"])
        return df.to_markdown(index=False)
