from typing import List, Literal, Optional
from datetime import date
import traceback
from pydantic import BaseModel, Field, ValidationError, field_validator

from agent.actions.base_action import BaseAction
from agent.message import Action, Message


class SearchFilingNotes(BaseModel):
    """Search financial statement footnotes (10-K, 10-Q, 20-F notes only)

    Use for: Segment breakdowns, detailed schedules, accounting policies, supplementary financial data
    Content: Segment reporting, debt/lease schedules, equity details, tax provisions, acquisition details
    Forms: 10-K, 10-Q, 20-F notes only (8-K/6-K do NOT contain financial statement notes)

    NOT for: Earnings releases (use SearchPressReleases) or main filing narrative (use SearchFilings)

    Query tips: Describe the table/schedule structure and what categories you expect
    - Good: "Table showing segment revenue by product category including iPhone, Mac, iPad, Services"
    - Bad: "segment revenue"
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
    reports: Literal['annual', 'quarterly', 'all'] = Field(
        default="all",
        description="Whether to search annual (20-F/10-K) or quarterly (10-Q) notes, or ALL. Note: 8-K/6-K do not contain financial statement notes."
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


class SearchFilingNotesAction(BaseAction):
    name: str = 'SearchFilingNotes'
    schema = SearchFilingNotes

    async def call(self, action: Action):
        """Semantic search across filing note chunks"""
        try:
            args = self.validate(action)
        except RuntimeError as e:
            self.log_start("SearchFilingNotes")
            self.log_error(f"Validation failed: {e}")
            return Message(role="tool", status="completed", content=str(e), error=True, action_id=action.id)

        params = f"'{args.query}' in {args.symbol} {args.reports} notes, {args.start_date} � {args.end_date}"
        self.log_start("SearchFilingNotes", params=params, thought=args.thought)

        forms = self._get_notes_forms_for_reports(args.reports)
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
        results = await self._search_filing_notes(args, forms)

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

    async def _search_filing_notes(self, args: SearchFilingNotes, forms: List[str]) -> List[dict]:
        """Vector search filing note chunks using company_filing_note_chunks view"""
        try:
            query = (
                self.database
                .table("company_filing_note_chunks")
                .select(
                    "id,filing_id,filing_note_id,index,note_title,note_filename,content,has_table,form,filing_date,report_date,fiscal_year,fiscal_period,company_name,company_symbols")
                .contains("company_symbols", args.symbol)
                .in_("form", forms)
                .gte("report_date", args.start_date)
                .lte("report_date", args.end_date)
            )

            if args.tables_only is not None:
                query = query.eq("has_table", args.tables_only)

            result = query.vector_search(args.query, "content", topk=args.limit * 2, return_scores=True).execute()

            for r in result.data:
                r['_type'] = 'filing_note_chunk'

            return result.data
        except Exception as e:
            self.log_error(f"Filing note chunks search failed: {e}\n{traceback.format_exc()}")
            return []

    @staticmethod
    def validate(action: Action) -> SearchFilingNotes:
        """Validates the action against the Pydantic schema"""
        try:
            return SearchFilingNotes(**action.body)
        except ValidationError as e:
            raise RuntimeError(f"Validation failed: {e}") from e

    @staticmethod
    def _get_notes_forms_for_reports(report_type: str) -> List[str]:
        """Maps report type to note-bearing forms only"""
        mapping = {
            'annual': ['10-K', '10-K/A', '20-F', '20-F/A'],
            'quarterly': ['10-Q', '10-Q/A'],
            'all': ['10-K', '10-K/A', '10-Q', '10-Q/A', '20-F', '20-F/A']
        }
        return mapping.get(report_type, [])

    def _format_results(self, results: List[dict]) -> str:
        """Format all results"""
        output = []
        for r in results:
            output.append(self._format_filing_note_chunk(r))
        return "\n".join(output) if output else "No results found."

    @staticmethod
    def _format_filing_note_chunk(r: dict) -> str:
        """Format filing note chunk result"""
        company_name = r.get('company_name', 'Unknown')
        symbols = ','.join(r.get('company_symbols', []))
        form = r.get('form', '?')
        filing_date = r.get('filing_date', '?')
        report_date = r.get('report_date', '?')
        fiscal_year = r.get('fiscal_year', '')
        fiscal_period = r.get('fiscal_period', '')
        note_title = r.get('note_title', 'Untitled Note')
        content = (r.get('content') or '').strip()
        score = r.get('_score', 0.0)

        fiscal_info = f"FY{fiscal_year} {fiscal_period}" if fiscal_year and fiscal_period else (
            f"FY{fiscal_year}" if fiscal_year else "")

        return (
                f"**[Note Chunk #{r['index']}] {note_title}** | Score: {score:.3f} | Filing #{r['filing_id']} | {company_name} ({symbols}) | {form} | Filed: {filing_date} | Report: {report_date}" +
                (f" | {fiscal_info}" if fiscal_info else "") + "\n\n" +
                f"{content}\n\n---\n"
        )
