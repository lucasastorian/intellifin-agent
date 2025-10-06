from typing import List, Optional
from datetime import date
from pydantic import BaseModel, Field, ValidationError, field_validator

from agent.actions.base_action import BaseAction
from agent.message import Action, Message


class KeywordSearchPressReleases(BaseModel):
    """BM-25 Keyword search across press releases within a date range. Returns top 5 results.

    - Searches ONLY the attached press releases of 8-Ks with item 9.01
    - Filters by filing_date, NOT report date
    """
    query: str = Field(description="Keyword query")
    symbol: str = Field(description="The ticker symbol of the company to search")
    start_date: str = Field(description="The YYYY-MM-DD filing date for which to start the query")
    end_date: Optional[str] = Field(default=None, description="The optional end date to filter. "
                                                "If not specified, will search up until today.")

    @classmethod
    @field_validator("symbol")
    def norm_symbol(cls, v: str) -> str:
        return v.strip().upper()

    @classmethod
    @field_validator("start_date")
    def check_start_date(cls, v: str) -> str:
        _ = date.fromisoformat(v)  # raises if invalid
        return v

    @classmethod
    @field_validator("end_date")
    def check_end_date(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return date.today().isoformat()
        _ = date.fromisoformat(v)
        return v


class KeywordSearchPressReleasesAction(BaseAction):
    name: str = 'KeywordSearchPressReleases'
    schema = KeywordSearchPressReleases
    limit: int = 5

    async def call(self, action: Action):
        """Searches press releases using BM25 keyword search"""
        try:
            args = self.validate(action)
        except RuntimeError as e:
            self.log_start("KeywordSearchPressReleases")
            self.log_error(f"Validation failed: {e}")
            return Message(role="tool", status="completed", content=str(e), error=True, action_id=action.id)

        params = f"'{args.query}' in {args.symbol} press releases, {args.start_date} → {args.end_date}"
        self.log_start("KeywordSearchPressReleases", params)

        # Sync company if needed
        not_found = self.sync_symbols(symbols=[args.symbol])
        if not_found:
            self.log_error(f"Symbol not found: {args.symbol}")
            return Message(
                role="tool",
                status="completed",
                content=f"Could not find symbol {args.symbol} on EDGAR",
                error=True,
                action_id=action.id
            )

        # Get company_id
        company_result = (
            self.database
            .table("companies")
            .select("id,name,symbols")
            .contains("symbols", args.symbol)
            .limit(1)
            .execute()
        )

        if not company_result.data:
            self.log_error(f"Company not found for {args.symbol}")
            return Message(
                role="tool",
                status="completed",
                content=f"Company not found for symbol {args.symbol}",
                error=True,
                action_id=action.id
            )

        company_id = company_result.data[0]['id']
        company_record = company_result.data[0]

        # Get 8-K filings with press releases in date range
        filings_result = (
            self.database
            .table("filings")
            .select("id,company_id,form,filing_date")
            .eq("company_id", company_id)
            .in_("form", ['8-K', '8-K/A'])
            .eq("press_release", True)
            .gte("filing_date", args.start_date)
            .lte("filing_date", args.end_date)
            .execute()
        )

        if not filings_result.data:
            self.log_done("No press releases in date range")
            return Message(
                role="tool",
                status="completed",
                content=f"No 8-K press releases found for {args.symbol} in the requested date range.",
                action_id=action.id
            )

        filing_ids = [f['id'] for f in filings_result.data]
        filing_by_id = {f['id']: f for f in filings_result.data}

        # Keyword search on press release pages
        try:
            results_result = (
                self.database
                .table("press_release_pages")
                .keyword_search(args.query, returning="id,filing_id,page,content")
                .in_("filing_id", filing_ids)
                .limit(self.limit)
                .execute()
            )
        except Exception as e:
            self.log_error(f"Search failed: {e}")
            return Message(role="tool", status="completed", content=f"Search error: {e}", error=True, action_id=action.id)

        if not results_result.data:
            self.log_done("No matches found")
            return Message(role="tool", status="completed", content="No matching press releases found.", action_id=action.id)

        company_by_id = {company_id: company_record}
        content = self._format_results(results_result.data, filing_by_id, company_by_id)

        unique_filings = len({r['filing_id'] for r in results_result.data})
        summary = f"Found {len(results_result.data)} matching page(s) across {unique_filings} press release(s)"

        self.log_done(summary)

        return Message(role="tool", status="completed", content=content, action_id=action.id)

    @staticmethod
    def validate(action: Action) -> KeywordSearchPressReleases:
        """Validates the action against the Pydantic schema"""
        try:
            return KeywordSearchPressReleases(**action.body)
        except ValidationError as e:
            raise RuntimeError(f"Validation failed: {e}") from e

    @staticmethod
    def _format_results(results: List[dict], filing_by_id: dict, company_by_id: dict) -> str:
        """Formats search results as markdown"""
        output = []

        for r in results:
            filing = filing_by_id.get(r['filing_id'], {})
            company = company_by_id.get(filing.get('company_id'), {})

            company_name = company.get('name', 'Unknown')
            symbols = ','.join(company.get('symbols', []))
            form = filing.get('form', '?')
            filing_date = filing.get('filing_date', '?')

            content = (r.get('content') or '').strip()

            output.append(
                f"**Filing #{r['filing_id']} - Page {r['page']}** | {company_name} ({symbols}) | {form} Press Release | {filing_date}\n\n"
                f"{content}\n\n---\n"
            )

        return "\n".join(output) if output else "No matching press releases found."

