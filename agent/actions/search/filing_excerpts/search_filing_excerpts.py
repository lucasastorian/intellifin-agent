import uuid
import traceback
from typing import List, Literal, Optional
from pydantic import BaseModel, Field, field_validator, ValidationError
from datetime import date

from agent.message import Action, Message
from agent.action_response import ActionResponse, ActionFollowUp
from agent.actions.base_action import BaseAction
from agent.actions.search.filing_excerpts.curate_filing_excerpts import CurateFilingExcerptsAction


class SearchFilingSections(BaseModel):
    """Search filing sections using natural language (semantic/vector search).

    Searches structured sections of 10-K, 10-Q, and 20-F filings:
    - Business, Risk Factors, MD&A, Legal Proceedings, Market Risk, etc.
    - Returns relevant excerpts/chunks from selected sections
    - Use natural language to describe what you're looking for

    Does NOT search: Press releases, attachments, or financial statement notes.
    For notes, use SearchFilingNotes. For attachments, use SearchAttachments.
    """
    thought: str = Field(
        description="Explain what you're searching for and how it will help achieve your objective"
    )
    symbol: str = Field(description="The ticker symbol to search")
    start_date: str = Field(
        description="The YYYY-MM-DD report date from which to start search"
    )
    end_date: Optional[str] = Field(
        default=None,
        description="The YYYY-MM-DD report date to cut off the search. Defaults to today"
    )
    sections: List[
        Literal[
            "business",  # 10-K Item 1
            "risk_factors",  # 10-K Item 1A / 10-Q Part II Item 1A
            "properties",  # 10-K Item 2
            "legal_proceedings",  # 10-K Item 3 / 10-Q Part II Item 1
            "market_equity_matters",  # 10-K Item 5
            "md&a",  # 10-K Item 7 / 10-Q Part I Item 2
            "market_risk",  # 10-K Item 7A / 10-Q Part I Item 3
            "controls_procedures",  # 10-K Item 9A / 10-Q Part I Item 4
            "other_information",  # 10-K Item 9B
            "unregistered_sales_equity",  # 10-Q Part II Item 2 (buybacks, private placements)
            "other"  # fallback (store raw heading)
        ]] = Field(
        description="The sections of the filing to search"
    )
    excerpt_description: str = Field(
        description="Describe the excerpt you're looking for. Ex. 'Risk factors related to supply chain & logistics, "
                    "with a focus on South East Asia'"
    )
    reports: Literal['annual', 'quarterly', 'all'] = Field(
        default='all',
        description="Filter by annual (10-K/20-F), quarterly (10-Q), or all reports"
    )
    tables_only: Optional[bool] = Field(
        default=None,
        description="Filter to only chunks with tables (True) or without tables (False)"
    )
    depth: Literal['low', 'medium', 'high'] = Field(
        default='medium',
        description="Search depth: 'low' (5 results), 'medium' (15 results), 'high' (30 results)"
    )

    @field_validator("symbol")
    @classmethod
    def norm_symbol(cls, v: str) -> str:
        return v.strip().upper()

    @field_validator("start_date")
    @classmethod
    def check_start_date(cls, v: str) -> str:
        _ = date.fromisoformat(v)
        return v

    @field_validator("end_date")
    @classmethod
    def check_end_date(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return date.today().isoformat()
        _ = date.fromisoformat(v)
        return v


class SearchFilingSectionsAction(BaseAction):
    name: str = 'SearchFilingSections'
    schema = SearchFilingSections

    async def call(self, action: Action) -> ActionResponse:
        """Executes a search over the 'company_filing_section_chunks' view"""
        try:
            args = self.validate(action)
        except RuntimeError as e:
            self.log_start("SearchFilingSections")
            self.log_error(f"Validation failed: {e}")
            return ActionResponse(
                message=Message(role="tool", status="completed", content=str(e), error=True, action_id=action.id)
            )

        params = f"symbol={args.symbol}, sections={args.sections}, {args.start_date} → {args.end_date}"
        self.log_start("SearchFilingSections", params=params, thought=args.thought)

        forms = self._get_forms_for_reports(args.reports)
        not_found = await self.sync_symbols(symbols=[args.symbol], forms=forms,
                                            start_date=args.start_date, end_date=args.end_date)
        if not_found:
            self.log_error(f"Symbol not found: {args.symbol}")
            return ActionResponse(
                message=Message(
                    role="tool",
                    status="completed",
                    content=f"Could not find symbol on EDGAR: {args.symbol}",
                    error=True,
                    action_id=action.id
                )
            )

        results = await self._search_filing_sections(args, forms)

        if not results:
            self.log_done("No matches found")
            return ActionResponse(
                message=Message(
                    role="tool",
                    status="completed",
                    content="No matching results found.",
                    action_id=action.id
                )
            )

        depth_map = {'low': 5, 'medium': 15, 'high': 30}
        limit = depth_map[args.depth]

        results.sort(key=lambda x: x.get('_score', 0), reverse=True)
        top_results = results[:limit]

        content = self._format_results(top_results)

        unique_filings = len({r['filing_id'] for r in top_results})
        summary = f"Found {len(top_results)} excerpt(s) across {unique_filings} filing(s)"
        self.log_done(summary)

        search_message_id = str(uuid.uuid4())

        search_message = Message(
            id=search_message_id,
            role="tool",
            status="completed",
            content=content,
            action_id=action.id
        )

        curate_action = CurateFilingExcerptsAction(
            excerpts=top_results,
            original_search_message_id=search_message_id,
            database=self.database,
            edgar_user_agent=self.edgar_user_agent,
            start_year=self.start_year
        )

        return ActionResponse(
            message=search_message,
            follow_up=ActionFollowUp(
                actions=[curate_action],
                force=True
            )
        )

    async def _search_filing_sections(self, args: SearchFilingSections, forms: List[str]) -> List[dict]:
        """Vector search filing section chunks using company_filing_section_chunks view"""
        try:
            query = (
                self.database
                .table("company_filing_section_chunks")
                .select(
                    "id,filing_id,section,index,page,pages,has_table,form,filing_date,report_date,"
                    "fiscal_year,fiscal_period,company_name,company_symbols"
                )
                .contains("company_symbols", args.symbol)
                .in_("form", forms)
                .in_("section", args.sections)
                .gte("report_date", args.start_date)
                .lte("report_date", args.end_date)
            )

            if args.tables_only is not None:
                query = query.eq("has_table", args.tables_only)

            depth_map = {'low': 5, 'medium': 15, 'high': 30}
            limit = depth_map[args.depth]

            await query.vector_search(
                args.excerpt_description,
                "embedding",
                topk=limit,
                return_scores=True
            )
            result = await query.execute()

            for r in result.data:
                r['_type'] = 'filing_section_chunk'

            return result.data

        except Exception as e:
            self.log_error(f"Filing section chunks search failed: {e}\n{traceback.format_exc()}")
            return []

    @staticmethod
    def validate(action: Action) -> SearchFilingSections:
        """Validates the action against the Pydantic schema"""
        try:
            return SearchFilingSections(**action.body)
        except ValidationError as e:
            raise RuntimeError(f"Validation failed: {e}") from e

    @staticmethod
    def _get_forms_for_reports(report_type: str) -> List[str]:
        """Maps report type to forms"""
        mapping = {
            'annual': ['10-K', '10-K/A', '20-F', '20-F/A'],
            'quarterly': ['10-Q', '10-Q/A'],
            'all': ['10-K', '10-K/A', '10-Q', '10-Q/A', '20-F', '20-F/A']
        }
        return mapping.get(report_type, [])

    @staticmethod
    def curate(excerpts: List[dict], summary: str) -> str:
        """Reformat search results with only curated excerpts"""
        output = [f"**Search Results (Curated)**\n\n_{summary}_\n"]
        for i, r in enumerate(excerpts):
            output.append(SearchFilingSectionsAction._format_section_chunk(r, i))
        return "\n".join(output)

    def _format_results(self, results: List[dict]) -> str:
        """Format all results"""
        output = []
        for i, r in enumerate(results):
            output.append(self._format_section_chunk(r, i))

        if not output:
            return "No results found."

        # Add curation instructions
        instructions = (
            "\n**INSTRUCTIONS:** Review the excerpts above and select only those relevant to your search query using their IDs. "
            "If none are relevant or only marginally useful, provide a brief summary of what you found instead."
        )
        return "\n".join(output) + instructions

    @staticmethod
    def _format_section_chunk(r: dict, index: int) -> str:
        """Format filing section chunk result"""
        from datetime import datetime

        excerpt_id = r['id']
        company_name = r['company_name']
        symbols = ','.join(r['company_symbols'])
        form = r['form']
        section = r['section'].replace('_', ' ').title()

        # Format dates naturally
        filing_date = datetime.strptime(r['filing_date'], '%Y-%m-%d').strftime('%B %-d, %Y')
        report_date = datetime.strptime(r['report_date'], '%Y-%m-%d').strftime('%B %-d, %Y')

        pages = r['pages']
        page_numbers = [p['page'] for p in pages]
        page_display = f"Page {page_numbers[0]}" if len(
            page_numbers) == 1 else f"Pages {page_numbers[0]}-{page_numbers[-1]}"

        content_parts = []
        for page_data in pages:
            if len(pages) > 1:
                content_parts.append(f"[Page {page_data['page']}]\n{page_data['content'].strip()}")
            else:
                content_parts.append(page_data['content'].strip())
        content = "\n\n".join(content_parts)

        fiscal_year = r['fiscal_year']
        fiscal_period = r['fiscal_period']

        fiscal_info = f"FY{fiscal_year} {fiscal_period}" if fiscal_year and fiscal_period else (
            f"FY{fiscal_year}" if fiscal_year else "")

        return (
                f"**[Excerpt #{index} | ID: {excerpt_id}] {section} ({page_display})** | Filing #{r['filing_id']} | "
                f"{company_name} ({symbols}) | {form} | Filed: {filing_date} | Report: {report_date}" +
                (f" | {fiscal_info}" if fiscal_info else "") + "\n\n" +
                f"{content}\n\n---\n"
        )
