from typing import List, Literal, Optional
from datetime import date
from pydantic import BaseModel, Field, ValidationError, field_validator

from agent.actions.base_action import BaseAction
from agent.message import Action, Message


class SearchContent(BaseModel):
    """Semantic search across filing content using vector embeddings

    IMPORTANT: This is NOT a keyword search! Describe what you're looking for in natural language.
    The more descriptive and detailed your query, the better the results.

    Examples of GOOD queries:
    - "Discussion of revenue growth drivers and market expansion strategies"
    - "Information about supply chain challenges and inventory management issues"
    - "Details on debt covenants, credit facilities, and liquidity position"

    Examples of BAD queries:
    - "revenue"  (too vague)
    - "Q4 2024"  (use date filters instead)

    - Specify a company using its ticker symbol
    - Specify the types of filings you are interested in - Annual, Quarterly, Current or All
    - The query will search across filing chunks, notes to financial statements, and press release chunks
    """
    query: str = Field(
        description="Natural language description of what you're looking for. Be specific and descriptive!")
    symbol: str = Field(description="The ticker symbol of the company to search")
    reports: Literal['annual', 'quarterly', 'current', 'all'] = Field(
        description="Whether to search annual, quarterly, current reports, or ALL three.")
    start_date: str = Field(description="The YYYY-MM-DD filing date for which to start the query")
    end_date: Optional[str] = Field(default=None, description="The optional end date to filter. "
                                                              "If not specified, will search up until today.")
    limit: int = Field(default=10, description="Maximum number of results to return (default: 10)", max=20)

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


class SearchContentAction(BaseAction):
    name: str = 'SearchContent'
    schema = SearchContent

    async def call(self, action: Action):
        """Semantic search across filing chunks, notes, and press releases"""
        try:
            args = self.validate(action)
        except RuntimeError as e:
            self.log_start("SearchContent")
            self.log_error(f"Validation failed: {e}")
            return Message(role="tool", status="completed", content=str(e), error=True, action_id=action.id)

        params = f"'{args.query}' in {args.symbol} {args.reports}, {args.start_date} → {args.end_date}"
        self.log_start("SearchContent", params)

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

        # Execute vector searches across all tables
        all_results = []

        # Search filing chunks
        filing_chunk_results = await self._search_filing_chunks(args, forms)
        all_results.extend(filing_chunk_results)

        # Search filing notes
        notes_results = await self._search_filing_notes(args, notes_forms)
        all_results.extend(notes_results)

        # Search press release chunks
        pr_chunk_results = await self._search_press_release_chunks(args)
        all_results.extend(pr_chunk_results)

        if not all_results:
            self.log_done("No matches found")
            return Message(role="tool", status="completed", content="No matching results found.", action_id=action.id)

        # Rerank by cosine similarity score and take top N
        all_results.sort(key=lambda x: x.get('_score', 0), reverse=True)
        top_results = all_results[:args.limit]

        # Format results
        content = self._format_results(top_results)

        unique_filings = len({r['filing_id'] for r in top_results})
        summary = f"Found {len(top_results)} result(s) across {unique_filings} filing(s)"

        self.log_done(summary)

        return Message(role="tool", status="completed", content=content, action_id=action.id)

    async def _search_filing_chunks(self, args: SearchContent, forms: List[str]) -> List[dict]:
        """Vector search filing chunks using company_filing_chunks view"""
        try:
            result = (
                self.database
                .table("company_filing_chunks")
                .select(
                    "id,filing_id,page,content,index,form,filing_date,report_date,fiscal_year,fiscal_period,company_name,company_symbols")
                .contains("company_symbols", args.symbol)
                .in_("form", forms)
                .gte("filing_date", args.start_date)
                .lte("filing_date", args.end_date)
                .vector_search(args.query, "content", topk=args.limit * 2, return_scores=True)
                .execute()
            )

            # Add result type marker
            for r in result.data:
                r['_type'] = 'filing_chunk'

            return result.data
        except Exception as e:
            self.log_error(f"Filing chunks search failed: {e}")
            return []

    async def _search_filing_notes(self, args: SearchContent, forms: List[str]) -> List[dict]:
        """Vector search filing note chunks using company_filing_note_chunks view"""
        try:
            result = (
                self.database
                .table("company_filing_note_chunks")
                .select(
                    "id,filing_id,filing_note_id,index,note_title,note_filename,content,form,filing_date,report_date,fiscal_year,fiscal_period,company_name,company_symbols")
                .contains("company_symbols", args.symbol)
                .in_("form", forms)
                .gte("filing_date", args.start_date)
                .lte("filing_date", args.end_date)
                .vector_search(args.query, "content", topk=args.limit * 2, return_scores=True)
                .execute()
            )

            # Add result type marker
            for r in result.data:
                r['_type'] = 'filing_note_chunk'

            return result.data
        except Exception as e:
            self.log_error(f"Filing note chunks search failed: {e}")
            return []

    async def _search_press_release_chunks(self, args: SearchContent) -> List[dict]:
        """Vector search press release chunks using company_press_release_chunks view"""
        try:
            result = (
                self.database
                .table("company_press_release_chunks")
                .select("id,filing_id,page,content,index,form,filing_date,report_date,company_name,company_symbols")
                .contains("company_symbols", args.symbol)
                .in_("form", ['8-K', '8-K/A'])
                .eq("press_release", True)
                .gte("filing_date", args.start_date)
                .lte("filing_date", args.end_date)
                .vector_search(args.query, "content", topk=args.limit * 2, return_scores=True)
                .execute()
            )

            # Add result type marker
            for r in result.data:
                r['_type'] = 'press_release_chunk'

            return result.data
        except Exception as e:
            self.log_error(f"Press release chunks search failed: {e}")
            return []

    @staticmethod
    def validate(action: Action) -> SearchContent:
        """Validates the action against the Pydantic schema"""
        try:
            return SearchContent(**action.body)
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
