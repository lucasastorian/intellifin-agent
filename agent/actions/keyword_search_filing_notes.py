from typing import List, Literal, Optional
from datetime import date
from pydantic import BaseModel, Field, ValidationError, field_validator

from agent.actions.base_action import BaseAction
from agent.message import Action, Message

AllowedNotesForm = Literal["10-K", "10-Q", "20-F"]


class KeywordSearchFilingNotes(BaseModel):
    """BM-25 Keyword search across filing notes within a date range. Returns top 5 notes.

    - Searches annual/quarterly forms with notes: 10-K, 10-Q, 20-F
    - Filters by filing_date, NOT report date
    """
    query: str = Field(description="Keyword query")
    symbol: str = Field(description="The ticker symbol of the company to search")
    reports: Literal['Annual', 'Quarterly', 'All'] = Field(
        description="Whether to search annual (10-K/20-F), quarterly (10-Q), or all note-bearing reports")
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


class KeywordSearchFilingNotesAction(BaseAction):
    name: str = 'KeywordSearchFilingNotes'
    schema = KeywordSearchFilingNotes
    limit: int = 5

    async def call(self, action: Action):
        """Searches filing notes using BM25 keyword search"""
        try:
            args = self.validate(action)
        except RuntimeError as e:
            self.log_start("KeywordSearchFilingNotes")
            self.log_error(f"Validation failed: {e}")
            return Message(role="tool", status="completed", content=str(e), error=True, action_id=action.id)

        params = f"'{args.query}' in {args.symbol} {args.reports} notes, {args.start_date} → {args.end_date}"
        self.log_start("KeywordSearchFilingNotes", params)

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

        # Map reports to forms (only note-bearing forms)
        forms = self._get_forms_for_reports(args.reports)

        # Keyword search on company_filing_notes view
        try:
            results_result = (
                self.database
                .table("company_filing_notes")
                .keyword_search(args.query, returning="id,filing_id,title,content,form,filing_date,company_name,company_symbols")
                .contains("company_symbols", args.symbol)
                .in_("form", forms)
                .gte("filing_date", args.start_date)
                .lte("filing_date", args.end_date)
                .limit(self.limit)
                .execute()
            )
        except Exception as e:
            self.log_error(f"Search failed: {e}")
            return Message(role="tool", status="completed", content=f"Search error: {e}", error=True, action_id=action.id)

        if not results_result.data:
            self.log_done("No matches found")
            return Message(role="tool", status="completed", content="No matching notes found.", action_id=action.id)

        content = self._format_results(results_result.data)

        unique_filings = len({r['filing_id'] for r in results_result.data})
        summary = f"Found {len(results_result.data)} matching note(s) across {unique_filings} filing(s)"

        self.log_done(summary)

        return Message(role="tool", status="completed", content=content, action_id=action.id)

    @staticmethod
    def validate(action: Action) -> KeywordSearchFilingNotes:
        """Validates the action against the Pydantic schema"""
        try:
            return KeywordSearchFilingNotes(**action.body)
        except ValidationError as e:
            raise RuntimeError(f"Validation failed: {e}") from e

    @staticmethod
    def _get_forms_for_reports(report_type: str) -> List[str]:
        """Maps report type to note-bearing forms with amendments"""
        mapping = {
            'Annual': ['10-K', '10-K/A', '20-F', '20-F/A'],
            'Quarterly': ['10-Q', '10-Q/A'],
            'All': ['10-K', '10-K/A', '10-Q', '10-Q/A', '20-F', '20-F/A']
        }
        return mapping.get(report_type, [])

    @staticmethod
    def _format_results(results: List[dict]) -> str:
        """Formats search results as markdown"""
        output = []

        for r in results:
            company_name = r.get('company_name', 'Unknown')
            symbols = ','.join(r.get('company_symbols', []))
            form = r.get('form', '?')
            filing_date = r.get('filing_date', '?')
            title = r.get('title', 'Untitled Note')
            content = (r.get('content') or '').strip()

            output.append(
                f"**{title}** | Filing #{r['filing_id']} | {company_name} ({symbols}) | {form} | {filing_date}\n\n"
                f"{content}\n\n---\n"
            )

        return "\n".join(output) if output else "No matching notes found."
