from typing import List, Optional
from datetime import date
import traceback
from pydantic import BaseModel, Field, ValidationError, field_validator

from agent.actions.base_action import BaseAction
from agent.message import Action, Message


class SearchAttachments(BaseModel):
    """Vector search across filing attachments/exhibits (8-K, 10-K, 10-Q)

    Use for: Material contracts, underwriting agreements, certificates of designation, debt instruments, M&A agreements, subsidiaries lists
    Content: Exhibits 1.x (underwriting), 2.x (M&A), 3.x (certificates), 4.x (instruments), 10.x (contracts), 21.x (subsidiaries), 99.x (other)
    Forms: 8-K, 10-K, 10-Q exhibits (NOT press releases - use SearchPressReleases for those)

    NOT for: Press releases (use SearchPressReleases), financial footnotes (use SearchFilingNotes), or main filing body (use SearchFilings)

    Query tips: Be specific about document types, terms, and structure
    - Good: "Certificate of designation for Series D preferred stock with conversion terms and liquidation preference"
    - Bad: "preferred stock certificate"
    """
    thought: str = Field(
        description="Describe what you're searching for and how it will help you achieve your objective"
    )
    query: str = Field(
        description="Natural language description of what you're looking for. Be specific and descriptive!"
    )
    symbols: List[str] = Field(description="List of ticker symbols to search")
    forms: Optional[List[str]] = Field(
        default=None,
        description="Forms to search (e.g., ['8-K', '10-K', '10-Q']). If not specified, searches all forms."
    )
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

    @field_validator("symbols")
    @classmethod
    def norm_symbols(cls, v: List[str]) -> List[str]:
        return [s.strip().upper() for s in v]

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


class SearchAttachmentsAction(BaseAction):
    name: str = 'SearchAttachments'
    schema = SearchAttachments

    async def call(self, action: Action):
        """Semantic search across attachment chunks"""
        try:
            args = self.validate(action)
        except RuntimeError as e:
            self.log_start("SearchAttachments")
            self.log_error(f"Validation failed: {e}")
            return Message(role="tool", status="completed", content=str(e), error=True, action_id=action.id)

        symbols_str = ",".join(args.symbols)
        forms_str = ",".join(args.forms) if args.forms else "all"
        params = f"'{args.query}' in {symbols_str} attachments ({forms_str}), {args.start_date} → {args.end_date}"
        self.log_start("SearchAttachments", params=params, thought=args.thought)

        not_found = self.sync_symbols(symbols=args.symbols, forms=args.forms,
                                       start_date=args.start_date, end_date=args.end_date)
        if not_found:
            self.log_error(f"Symbols not found: {', '.join(not_found)}")
            return Message(
                role="tool",
                status="completed",
                content=f"Could not find symbols on EDGAR: {', '.join(not_found)}",
                error=True,
                action_id=action.id
            )

        results = await self._search_attachment_chunks(args)

        if not results:
            self.log_done("No matches found")
            return Message(role="tool", status="completed", content="No matching results found.", action_id=action.id)

        results.sort(key=lambda x: x.get('_score', 0), reverse=True)
        top_results = results[:args.limit]

        content = self._format_results(top_results)

        unique_filings = len({r['filing_id'] for r in top_results})
        unique_attachments = len({r['attachment_id'] for r in top_results})
        summary = f"Found {len(top_results)} result(s) across {unique_attachments} attachment(s) in {unique_filings} filing(s)"

        self.log_done(summary)

        return Message(role="tool", status="completed", content=content, action_id=action.id)

    async def _search_attachment_chunks(self, args: SearchAttachments) -> List[dict]:
        """Vector search attachment chunks using company_filing_attachment_chunks view"""
        try:
            query = (
                self.database
                .table("company_filing_attachment_chunks")
                .select(
                    "id,filing_id,attachment_id,exhibit_number,attachment_filename,attachment_description,page,content,index,has_table,form,filing_date,report_date,company_name,company_symbols")
            )

            # Filter by symbols (OR condition)
            for symbol in args.symbols:
                query = query.contains("company_symbols", symbol)

            # Filter by forms if specified
            if args.forms:
                query = query.in_("form", args.forms)

            # Filter by date range
            query = (
                query
                .gte("report_date", args.start_date)
                .lte("report_date", args.end_date)
            )

            # Apply has_table filter if specified
            if args.tables_only is not None:
                query = query.eq("has_table", args.tables_only)

            result = query.vector_search(args.query, "content", topk=args.limit * 2, return_scores=True).execute()

            # Add result type marker
            for r in result.data:
                r['_type'] = 'attachment_chunk'

            return result.data
        except Exception as e:
            self.log_error(f"Attachment chunks search failed: {e}\n{traceback.format_exc()}")
            return []

    @staticmethod
    def validate(action: Action) -> SearchAttachments:
        """Validates the action against the Pydantic schema"""
        try:
            return SearchAttachments(**action.body)
        except ValidationError as e:
            raise RuntimeError(f"Validation failed: {e}") from e

    def _format_results(self, results: List[dict]) -> str:
        """Format all results"""
        output = []
        for r in results:
            output.append(self._format_attachment_chunk(r))
        return "\n".join(output) if output else "No results found."

    @staticmethod
    def _format_attachment_chunk(r: dict) -> str:
        """Format attachment chunk result"""
        company_name = r.get('company_name', 'Unknown')
        symbols = ','.join(r.get('company_symbols', []))
        form = r.get('form', '?')
        filing_date = r.get('filing_date', '?')
        report_date = r.get('report_date', '?')
        exhibit = r.get('exhibit_number', '?')
        filename = r.get('attachment_filename', '?')
        description = r.get('attachment_description', '')
        content = (r.get('content') or '').strip()
        score = r.get('_score', 0.0)

        header = f"**[Attachment Chunk #{r['index']}] Exhibit {exhibit} - Page {r['page']}** | Score: {score:.3f}"
        meta = f"{company_name} ({symbols}) | {form} | Filed: {filing_date} | Report: {report_date}"
        if description:
            meta += f"\n**Description:** {description}"
        meta += f"\n**Filename:** {filename}"

        return f"{header}\n{meta}\n\n{content}\n\n---\n"
