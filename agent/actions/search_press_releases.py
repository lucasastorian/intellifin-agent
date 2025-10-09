from typing import List, Optional
from datetime import date
import traceback
from pydantic import BaseModel, Field, ValidationError, field_validator

from agent.actions.base_action import BaseAction
from agent.message import Action, Message


class SearchPressReleases(BaseModel):
    """Vector search across earnings press releases (8-K exhibits only)

    Use for: Quarterly results, forward guidance, executive quotes, headline metrics, management outlook
    Content: Earnings announcements, preliminary results, guidance ranges, forward-looking statements
    Forms: 8-K exhibits only (NOT 6-K - foreign issuers don't attach press releases)

    NOT for: Financial footnotes (use SearchFilingNotes) or main filing content (use SearchFilings)

    Query tips: Be specific about metrics, time periods, and units
    - Good: "Q1 2025 revenue guidance range in US dollars with FX rate assumptions"
    - Bad: "Q1 guidance"
    """
    thought: str = Field(
        description="Describe what you're searching for and how it will help you achieve your objective"
    )
    query: str = Field(
        description="Natural language description of what you're looking for. Be specific and descriptive!"
    )
    symbol: str = Field(description="The ticker symbol of the company to search")
    start_date: str = Field(description="The YYYY-MM-DD report date for which to start the query")
    end_date: Optional[str] = Field(
        default=None,
        description="The optional end date to filter. If not specified, will search up until today."
    )
    tables_only: Optional[bool] = Field(
        default=None,
        description="Filter to only chunks with tables (True) or without tables (False). If not specified, returns all chunks."
    )
    limit: int = Field(default=5, description="Maximum number of results to return (default: 5)", le=10)

    @classmethod
    @field_validator("symbol")
    def norm_symbol(cls, v: str) -> str:
        return v.strip().upper()

    @classmethod
    @field_validator("start_date")
    def check_start_date(cls, v: str) -> str:
        _ = date.fromisoformat(v)
        return v

    @field_validator("end_date")
    @classmethod
    def check_end_date(cls, v: Optional[str]) -> str:
        if v is None:
            return date.today().isoformat()
        _ = date.fromisoformat(v)
        return v


class SearchPressReleasesAction(BaseAction):
    name: str = 'SearchPressReleases'
    schema = SearchPressReleases

    async def call(self, action: Action):
        """Semantic search across press release chunks"""
        try:
            args = self.validate(action)
        except RuntimeError as e:
            self.log_start("SearchPressReleases")
            self.log_error(f"Validation failed: {e}")
            return Message(role="tool", status="completed", content=str(e), error=True, action_id=action.id)

        params = f"'{args.query}' in {args.symbol} press releases, {args.start_date} � {args.end_date}"
        self.log_start("SearchPressReleases", params=params, thought=args.thought)

        forms = ['8-K', '8-K/A']
        not_found = self.sync_symbols(symbols=[args.symbol], forms=forms,
                                       start_date=args.start_date, end_date=args.end_date)
        if not_found:
            self.log_error(f"Symbol not found: {args.symbol}")
            return Message(
                role="tool",
                status="completed",
                content=f"Could not find symbol {args.symbol} on EDGAR",
                error=True,
                action_id=action.id
            )

        results = await self._search_press_release_chunks(args)

        if not results:
            self.log_done("No matches found")
            return Message(role="tool", status="completed", content="No matching results found.", action_id=action.id)

        results.sort(key=lambda x: x.get('_score', 0), reverse=True)
        top_results = results[:args.limit]

        content = self._format_results(top_results)

        unique_filings = len({r['filing_id'] for r in top_results})
        summary = f"Found {len(top_results)} result(s) across {unique_filings} filing(s)"

        self.log_done(summary)

        return Message(role="tool", status="completed", content=content, action_id=action.id)

    async def _search_press_release_chunks(self, args: SearchPressReleases) -> List[dict]:
        """Vector search press release chunks using company_filing_attachment_chunks view"""
        try:
            query = (
                self.database
                .table("company_filing_attachment_chunks")
                .select(
                    "id,filing_id,attachment_id,page,content,index,has_table,exhibit_number,attachment_type,form,filing_date,report_date,company_name,company_symbols")
                .contains("company_symbols", args.symbol)
                .in_("form", ['8-K', '8-K/A'])
                .eq("attachment_type", "press_release")
                .gte("report_date", args.start_date)
                .lte("report_date", args.end_date)
            )

            # Apply has_table filter if specified
            if args.tables_only is not None:
                query = query.eq("has_table", args.tables_only)

            result = query.vector_search(args.query, "content", topk=args.limit * 2, return_scores=True).execute()

            # Add result type marker
            for r in result.data:
                r['_type'] = 'press_release_chunk'

            return result.data
        except Exception as e:
            self.log_error(f"Press release chunks search failed: {e}\n{traceback.format_exc()}")
            return []

    @staticmethod
    def validate(action: Action) -> SearchPressReleases:
        """Validates the action against the Pydantic schema"""
        try:
            return SearchPressReleases(**action.body)
        except ValidationError as e:
            raise RuntimeError(f"Validation failed: {e}") from e

    def _format_results(self, results: List[dict]) -> str:
        """Format all results"""
        output = []
        for r in results:
            output.append(self._format_press_release_chunk(r))
        return "\n".join(output) if output else "No results found."

    @staticmethod
    def _format_press_release_chunk(r: dict) -> str:
        """Format press release chunk result"""
        company_name = r.get('company_name', 'Unknown')
        symbols = ','.join(r.get('company_symbols', []))
        form = r.get('form', '?')
        filing_date = r.get('filing_date', '?')
        report_date = r.get('report_date', '?')
        content = (r.get('content') or '').strip()
        score = r.get('_score', 0.0)

        return (
                f"**[Press Release Chunk #{r['index']}] Filing #{r['filing_id']} - Page {r['page']}** | Score: {score:.3f} | {company_name} ({symbols}) | {form} | Filed: {filing_date} | Report: {report_date}\n\n" +
                f"{content}\n\n---\n"
        )
