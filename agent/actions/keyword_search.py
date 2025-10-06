from typing import List, Literal, Optional
from datetime import date
from pydantic import BaseModel, Field, ValidationError, field_validator

from agent.actions.base_action import BaseAction
from agent.message import Action, Message


class KeywordSearchFilings(BaseModel):
    """BM-25 Keyword search across filings, the notes associated with financial statements and attached press releases

    - Specify a company using its ticker symbol
    - Specify the types of filings you are interested in - Annual, Quarterly, Current or All
    - Specify what to search - the a combination of the filings, the notes to financial statements or press releases

    """
    query: str = Field(description="Keyword query")
    symbol: str = Field(description="The ticker symbol of the company to search")
    reports: Literal['annual', 'quarterly', 'current', 'all'] = Field(
        description="Whether to search annual, quarterly, current reports, or ALL three.")
    sources: List[Literal['filings', 'notes', 'press_releases']] = Field(
        description="Specify the types of sources you're interested in searching"
    )
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
        _ = date.fromisoformat(v)
        return v

    @classmethod
    @field_validator("end_date")
    def check_end_date(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return date.today().isoformat()
        _ = date.fromisoformat(v)
        return v


class KeywordSearchFilingsAction(BaseAction):
    name: str = 'KeywordSearchFilings'
    schema = KeywordSearchFilings
    limit: int = 10

    async def call(self, action: Action):
        """Unified keyword search across filing pages, notes, and press releases"""
        try:
            args = self.validate(action)
        except RuntimeError as e:
            self.log_start("KeywordSearchFilings")
            self.log_error(f"Validation failed: {e}")
            return Message(role="tool", status="completed", content=str(e), error=True, action_id=action.id)

        sources_str = ', '.join(args.sources)
        params = f"'{args.query}' in {args.symbol} {args.reports} ({sources_str}), {args.start_date} → {args.end_date}"
        self.log_start("KeywordSearchFilings", params)

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

        # Map reports to forms
        forms = self._get_forms_for_reports(args.reports)
        notes_forms = self._get_notes_forms_for_reports(args.reports)

        # Execute searches based on sources
        all_results = []

        if 'filings' in args.sources:
            filing_results = await self._search_filing_pages(args, forms)
            all_results.extend(filing_results)

        if 'notes' in args.sources:
            notes_results = await self._search_filing_notes(args, notes_forms)
            all_results.extend(notes_results)

        if 'press_releases' in args.sources:
            pr_results = await self._search_press_releases(args)
            all_results.extend(pr_results)

        if not all_results:
            self.log_done("No matches found")
            return Message(role="tool", status="completed", content="No matching results found.", action_id=action.id)

        # Sort by score descending and take top 10
        all_results.sort(key=lambda x: x.get('_score', 0), reverse=True)
        top_results = all_results[:self.limit]

        # Format results
        content = self._format_results(top_results)

        unique_filings = len({r['filing_id'] for r in top_results})
        summary = f"Found {len(top_results)} result(s) across {unique_filings} filing(s)"

        self.log_done(summary)

        return Message(role="tool", status="completed", content=content, action_id=action.id)

    async def _search_filing_pages(self, args: KeywordSearchFilings, forms: List[str]) -> List[dict]:
        """Search filing pages view"""
        try:
            result = (
                self.database
                .table("company_filing_pages")
                .keyword_search(args.query, returning="id,filing_id,page,content,form,filing_date,company_name,company_symbols")
                .contains("company_symbols", args.symbol)
                .in_("form", forms)
                .gte("filing_date", args.start_date)
                .lte("filing_date", args.end_date)
                .limit(self.limit * 2)
                .execute()
            )

            for r in result.data:
                r['_type'] = 'filing_page'
                r['_score'] = result.score[result.data.index(r)] if result.score else 0

            return result.data
        except Exception as e:
            self.log_error(f"Filing pages search failed: {e}")
            return []

    async def _search_filing_notes(self, args: KeywordSearchFilings, forms: List[str]) -> List[dict]:
        """Search filing notes view"""
        try:
            result = (
                self.database
                .table("company_filing_notes")
                .keyword_search(args.query, returning="id,filing_id,title,content,form,filing_date,company_name,company_symbols")
                .contains("company_symbols", args.symbol)
                .in_("form", forms)
                .gte("filing_date", args.start_date)
                .lte("filing_date", args.end_date)
                .limit(self.limit * 2)
                .execute()
            )

            for r in result.data:
                r['_type'] = 'filing_note'
                r['_score'] = result.score[result.data.index(r)] if result.score else 0

            return result.data
        except Exception as e:
            self.log_error(f"Filing notes search failed: {e}")
            return []

    async def _search_press_releases(self, args: KeywordSearchFilings) -> List[dict]:
        """Search press releases view"""
        try:
            result = (
                self.database
                .table("company_filing_press_releases")
                .keyword_search(args.query, returning="id,filing_id,page,content,form,filing_date,company_name,company_symbols")
                .contains("company_symbols", args.symbol)
                .in_("form", ['8-K', '8-K/A'])
                .eq("press_release", True)
                .gte("filing_date", args.start_date)
                .lte("filing_date", args.end_date)
                .limit(self.limit * 2)
                .execute()
            )

            for r in result.data:
                r['_type'] = 'press_release'
                r['_score'] = result.score[result.data.index(r)] if result.score else 0

            return result.data
        except Exception as e:
            self.log_error(f"Press releases search failed: {e}")
            return []

    @staticmethod
    def validate(action: Action) -> KeywordSearchFilings:
        """Validates the action against the Pydantic schema"""
        try:
            return KeywordSearchFilings(**action.body)
        except ValidationError as e:
            raise RuntimeError(f"Validation failed: {e}") from e

    @staticmethod
    def _get_forms_for_reports(report_type: str) -> List[str]:
        """Maps report type to form types with amendments"""
        mapping = {
            'annual': ['10-K', '10-K/A', '20-F', '20-F/A'],
            'quarterly': ['10-Q', '10-Q/A'],
            'current': ['8-K', '8-K/A', '6-K', '6-K/A'],
            'all': ['10-K', '10-K/A', '10-Q', '10-Q/A', '8-K', '8-K/A', '20-F', '20-F/A', '6-K', '6-K/A']
        }
        return mapping.get(report_type, [])

    @staticmethod
    def _get_notes_forms_for_reports(report_type: str) -> List[str]:
        """Maps report type to note-bearing forms only"""
        mapping = {
            'annual': ['10-K', '10-K/A', '20-F', '20-F/A'],
            'quarterly': ['10-Q', '10-Q/A'],
            'current': [],
            'all': ['10-K', '10-K/A', '10-Q', '10-Q/A', '20-F', '20-F/A']
        }
        return mapping.get(report_type, [])

    def _format_results(self, results: List[dict]) -> str:
        """Format all results using type-specific formatters"""
        output = []

        for r in results:
            result_type = r.get('_type')

            if result_type == 'filing_page':
                output.append(self._format_filing_page(r))
            elif result_type == 'filing_note':
                output.append(self._format_filing_note(r))
            elif result_type == 'press_release':
                output.append(self._format_press_release(r))

        return "\n".join(output) if output else "No results found."

    @staticmethod
    def _format_filing_page(r: dict) -> str:
        """Format filing page result"""
        company_name = r.get('company_name', 'Unknown')
        symbols = ','.join(r.get('company_symbols', []))
        form = r.get('form', '?')
        filing_date = r.get('filing_date', '?')
        content = (r.get('content') or '').strip()

        return (
            f"**[Filing Page] #{r['filing_id']} - Page {r['page']}** | {company_name} ({symbols}) | {form} | {filing_date}\n\n"
            f"{content}\n\n---\n"
        )

    @staticmethod
    def _format_filing_note(r: dict) -> str:
        """Format filing note result"""
        company_name = r.get('company_name', 'Unknown')
        symbols = ','.join(r.get('company_symbols', []))
        form = r.get('form', '?')
        filing_date = r.get('filing_date', '?')
        title = r.get('title', 'Untitled Note')
        content = (r.get('content') or '').strip()

        return (
            f"**[Note] {title}** | Filing #{r['filing_id']} | {company_name} ({symbols}) | {form} | {filing_date}\n\n"
            f"{content}\n\n---\n"
        )

    @staticmethod
    def _format_press_release(r: dict) -> str:
        """Format press release result"""
        company_name = r.get('company_name', 'Unknown')
        symbols = ','.join(r.get('company_symbols', []))
        form = r.get('form', '?')
        filing_date = r.get('filing_date', '?')
        content = (r.get('content') or '').strip()

        return (
            f"**[Press Release] #{r['filing_id']} - Page {r['page']}** | {company_name} ({symbols}) | {form} | {filing_date}\n\n"
            f"{content}\n\n---\n"
        )
