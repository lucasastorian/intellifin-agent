from typing import List, Literal, Optional
from datetime import date
from pydantic import BaseModel, Field, ValidationError, field_validator

from agent.actions.base_action import BaseAction
from agent.message import Action, Message


class SearchFilings(BaseModel):
    """Perform a natural language search through chunked excerpts from filings, notes, and press releases

    - Use the 'query' to describe what you're looking for in natural language, ex. 'Statement of operations includes revenues' or 'AI Capex guidance for 2025'
    - This is NOT a keyword search - the more accurately you can describe the excerpt you are searching for, the better the search results will be
    - Specify a symbol of the company whose filings you want to search through. If unsure - use the 'SearchCompanies' tool first

    IMPORTANT - Date Range Logic:
    - start_date and end_date filter by REPORT DATE (when the filing covers, not when it was filed)
    - To find guidance/plans for year X, search filings from year X-1 or earlier (companies provide forward-looking guidance in current filings)
    - Example: To find "2025 capex guidance", search reports from 2024 (or even late 2023), NOT 2025
    - Example: To find "Q1 2025 results", search reports from Q1 2025

    - Optionally specify the types of reports you want to search. Annual, quarterly, current or all of them.
    - Optionally restrict the search ONLY to tables. This is helpful if you are looking for a table - such as a segment breakdown - in particular.
    """
    thought: str = Field(
        description="Describe what you're searching for and how it will help you achieve your objective"
    )
    query: str = Field(
        description="Natural language description of what you're looking for. Be specific and descriptive!")
    symbol: str = Field(description="The ticker symbol of the company to search")
    start_date: str = Field(description="The YYYY-MM-DD filing date for which to start the query")
    end_date: Optional[str] = Field(default=None, description="The optional end date to filter. "
                                                              "If not specified, will search up until today.")
    reports: Literal['annual', 'quarterly', 'current', 'all'] = Field(
        default="all",
        description="Whether to search annual (20-F/10-K/DEF 14A), quarterly (10-Q), current reports (, or ALL three.")
    tables_only: Optional[bool] = Field(default=None,
                                        description="Filter to only chunks with tables (True) "
                                                    "or without tables (False). If not specified, returns all chunks.")
    limit: int = Field(default=5, description="Maximum number of results to return (default: 10)", max=10)


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


class SearchFilingsAction(BaseAction):
    name: str = 'SearchFilings'
    schema = SearchFilings

    async def call(self, action: Action):
        """Semantic search across filing chunks, notes, and press releases"""
        try:
            args = self.validate(action)
        except RuntimeError as e:
            self.log_start("SearchFilings")
            self.log_error(f"Validation failed: {e}")
            return Message(role="tool", status="completed", content=str(e), error=True, action_id=action.id)

        params = f"'{args.query}' in {args.symbol} {args.reports}, {args.start_date} → {args.end_date}"
        self.log_start("SearchFilings", params=params, thought=args.thought)

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

        forms = self._get_forms_for_reports(args.reports)
        notes_forms = self._get_notes_forms_for_reports(args.reports)

        all_results = []

        filing_chunk_results = await self._search_filing_chunks(args, forms)
        all_results.extend(filing_chunk_results)

        notes_results = await self._search_filing_notes(args, notes_forms)
        all_results.extend(notes_results)

        pr_chunk_results = await self._search_press_release_chunks(args)
        all_results.extend(pr_chunk_results)

        if not all_results:
            self.log_done("No matches found")
            return Message(role="tool", status="completed", content="No matching results found.", action_id=action.id)

        all_results.sort(key=lambda x: x.get('_score', 0), reverse=True)
        top_results = all_results[:args.limit]

        content = self._format_results(top_results)

        unique_filings = len({r['filing_id'] for r in top_results})
        summary = f"Found {len(top_results)} result(s) across {unique_filings} filing(s)"

        self.log_done(summary)

        return Message(role="tool", status="completed", content=content, action_id=action.id)

    async def _search_filing_chunks(self, args: SearchFilings, forms: List[str]) -> List[dict]:
        """Vector search filing chunks using company_filing_chunks view"""
        try:
            query = (
                self.database
                .table("company_filing_chunks")
                .select(
                    "id,filing_id,page,content,index,has_table,form,filing_date,report_date,fiscal_year,fiscal_period,company_name,company_symbols")
                .contains("company_symbols", args.symbol)
                .in_("form", forms)
                .gte("report_date", args.start_date)
                .lte("report_date", args.end_date)
            )

            # Apply has_table filter if specified
            if args.tables_only is not None:
                query = query.eq("has_table", args.tables_only)

            result = query.vector_search(args.query, "content", topk=args.limit * 2, return_scores=True).execute()

            # Add result type marker
            for r in result.data:
                r['_type'] = 'filing_chunk'

            return result.data
        except Exception as e:
            self.log_error(f"Filing chunks search failed: {e}")
            return []

    async def _search_filing_notes(self, args: SearchFilings, forms: List[str]) -> List[dict]:
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

            # Apply has_table filter if specified
            if args.tables_only is not None:
                query = query.eq("has_table", args.tables_only)

            result = query.vector_search(args.query, "content", topk=args.limit * 2, return_scores=True).execute()

            # Add result type marker
            for r in result.data:
                r['_type'] = 'filing_note_chunk'

            return result.data
        except Exception as e:
            self.log_error(f"Filing note chunks search failed: {e}")
            return []

    async def _search_press_release_chunks(self, args: SearchFilings) -> List[dict]:
        """Vector search press release chunks using company_press_release_chunks view"""
        try:
            query = (
                self.database
                .table("company_press_release_chunks")
                .select(
                    "id,filing_id,page,content,index,has_table,form,report_date,report_date,company_name,company_symbols")
                .contains("company_symbols", args.symbol)
                .in_("form", ['8-K', '8-K/A'])
                .eq("press_release", True)
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
            self.log_error(f"Press release chunks search failed: {e}")
            return []

    @staticmethod
    def validate(action: Action) -> SearchFilings:
        """Validates the action against the Pydantic schema"""
        try:
            return SearchFilings(**action.body)
        except ValidationError as e:
            raise RuntimeError(f"Validation failed: {e}") from e

    @staticmethod
    def _get_forms_for_reports(report_type: str) -> List[str]:
        """Maps report type to form types with amendments"""
        mapping = {
            'annual': ['10-K', '10-K/A', '20-F', '20-F/A', 'DEF 14A', 'DEF 14A/A'],
            'quarterly': ['10-Q', '10-Q/A'],
            'current': ['8-K', '8-K/A', '6-K', '6-K/A'],
            'all': ['10-K', '10-K/A', '10-Q', '10-Q/A', '8-K', '8-K/A', '20-F', '20-F/A', '6-K', '6-K/A',  'DEF 14A', 'DEF 14A/A']
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

            if result_type == 'filing_chunk':
                output.append(self._format_filing_chunk(r))
            elif result_type == 'filing_note_chunk':
                output.append(self._format_filing_note_chunk(r))
            elif result_type == 'press_release_chunk':
                output.append(self._format_press_release_chunk(r))

        return "\n".join(output) if output else "No results found."

    @staticmethod
    def _format_filing_chunk(r: dict) -> str:
        """Format filing chunk result"""
        company_name = r.get('company_name', 'Unknown')
        symbols = ','.join(r.get('company_symbols', []))
        form = r.get('form', '?')
        filing_date = r.get('filing_date', '?')
        report_date = r.get('report_date', '?')
        fiscal_year = r.get('fiscal_year', '')
        fiscal_period = r.get('fiscal_period', '')
        content = (r.get('content') or '').strip()
        score = r.get('_score', 0.0)

        fiscal_info = f"FY{fiscal_year} {fiscal_period}" if fiscal_year and fiscal_period else (
            f"FY{fiscal_year}" if fiscal_year else "")

        return (
                f"**[Chunk #{r['index']}] Filing #{r['filing_id']} - Page {r['page']}** | Score: {score:.3f} | {company_name} ({symbols}) | {form} | Filed: {filing_date} | Report: {report_date}" +
                (f" | {fiscal_info}" if fiscal_info else "") + "\n\n" +
                f"{content}\n\n---\n"
        )

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
