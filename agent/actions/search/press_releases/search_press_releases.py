import uuid
import traceback
from typing import List, Literal, Optional
from pydantic import BaseModel, Field, field_validator, ValidationError
from datetime import date

from agent.message import Action, Message
from agent.action_response import ActionResponse, ActionFollowUp
from agent.actions.base_action import BaseAction
from agent.actions.search.press_releases.curate_press_releases import CuratePressReleasesAction


class SearchPressReleases(BaseModel):
    """Search press release attachments using natural language (semantic/vector search).

    Searches press releases filed as exhibits (typically Exhibit 99.1) with 8-K and other filings:
    - Earnings announcements, M&A announcements, product launches, etc.
    - Returns relevant excerpts/chunks from press releases
    - Use natural language to describe what you're looking for

    Does NOT search: Filing sections (10-K/Q body), financial statement notes, or other attachments.
    For filing sections, use SearchFilingSections. For notes, use SearchFilingNotes.
    """
    thought: str = Field(
        description="Explain what you're searching for and how it will help achieve your objective"
    )
    symbol: str = Field(description="The ticker symbol to search")
    start_date: str = Field(
        description="The YYYY-MM-DD filing date from which to start search"
    )
    end_date: Optional[str] = Field(
        default=None,
        description="The YYYY-MM-DD filing date to cut off the search. Defaults to today"
    )
    excerpt_description: str = Field(
        description="Describe the excerpt you're looking for. Ex. 'Earnings guidance and revenue forecasts'"
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


class SearchPressReleasesAction(BaseAction):
    name: str = 'SearchPressReleases'
    schema = SearchPressReleases

    async def call(self, action: Action) -> ActionResponse:
        """Executes a search over press release chunks"""
        try:
            args = self.validate(action)
        except RuntimeError as e:
            self.log_start("SearchPressReleases")
            self.log_error(f"Validation failed: {e}")
            return ActionResponse(
                message=Message(role="tool", status="completed", content=str(e), error=True, action_id=action.id)
            )

        params = f"symbol={args.symbol}, {args.start_date} → {args.end_date}"
        self.log_start("SearchPressReleases", params=params, thought=args.thought)

        # All forms that can have press releases
        forms = ['8-K', '8-K/A', '10-K', '10-K/A', '10-Q', '10-Q/A', '20-F', '20-F/A', '6-K', '6-K/A']
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

        results = await self._search_press_releases(args)

        if not results:
            self.log_done("No matches found")
            return ActionResponse(
                message=Message(
                    role="tool",
                    status="completed",
                    content="No matching press releases found.",
                    action_id=action.id
                )
            )

        depth_map = {'low': 5, 'medium': 15, 'high': 30}
        limit = depth_map[args.depth]

        results.sort(key=lambda x: x.get('_score', 0), reverse=True)
        top_results = results[:limit]

        content = self._format_results(top_results)

        unique_filings = len({r['filing_id'] for r in top_results})
        summary = f"Found {len(top_results)} press release excerpt(s) across {unique_filings} filing(s)"
        self.log_done(summary)

        search_message_id = str(uuid.uuid4())

        search_message = Message(
            id=search_message_id,
            role="tool",
            status="completed",
            content=content,
            action_id=action.id
        )

        curate_action = CuratePressReleasesAction(
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

    async def _search_press_releases(self, args: SearchPressReleases) -> List[dict]:
        """Vector search press release chunks using company_filing_attachment_chunks view"""
        try:
            query = (
                self.database
                .table("company_filing_attachment_chunks")
                .select(
                    "id,filing_id,attachment_id,exhibit_number,attachment_filename,attachment_description,"
                    "page,pages,has_table,form,filing_date,company_name,company_symbols"
                )
                .contains("company_symbols", args.symbol)
                .eq("attachment_type", "press_release")
                .gte("filing_date", args.start_date)
                .lte("filing_date", args.end_date)
            )

            if args.tables_only is not None:
                query = query.eq("has_table", args.tables_only)

            depth_map = {'low': 5, 'medium': 15, 'high': 30}
            limit = depth_map[args.depth]

            result = await query.vector_search(
                query=args.excerpt_description,
                column="embedding",
                topk=limit,
                return_scores=True
            ).execute()

            print(f"Vector search returned {limit} results")

            for r in result.data:
                r['_type'] = 'press_release_chunk'

            return result.data

        except Exception as e:
            self.log_error(f"Press release search failed: {e}\n{traceback.format_exc()}")
            return []

    @staticmethod
    def validate(action: Action) -> SearchPressReleases:
        """Validates the action against the Pydantic schema"""
        try:
            return SearchPressReleases(**action.body)
        except ValidationError as e:
            raise RuntimeError(f"Validation failed: {e}") from e

    @staticmethod
    def curate(excerpts: List[dict], summary: str) -> str:
        """Reformat search results with only curated excerpts"""
        output = [f"**Press Release Search Results (Curated)**\n\n_{summary}_\n"]
        for i, r in enumerate(excerpts):
            output.append(SearchPressReleasesAction._format_press_release_chunk(r, i))
        return "\n".join(output)

    def _format_results(self, results: List[dict]) -> str:
        """Format all results"""
        output = []
        for i, r in enumerate(results):
            output.append(self._format_press_release_chunk(r, i))

        if not output:
            return "No results found."

        # Add curation instructions
        instructions = (
            "\n**INSTRUCTIONS:** Review the press release excerpts above and select only those relevant to your search query using their IDs. "
            "If none are relevant or only marginally useful, provide a brief summary of what you found instead."
        )
        return "\n".join(output) + instructions

    @staticmethod
    def _format_press_release_chunk(r: dict, index: int) -> str:
        """Format press release chunk result"""
        from datetime import datetime

        excerpt_id = r['id']
        company_name = r['company_name']
        symbols = ','.join(r['company_symbols'])
        form = r['form']
        exhibit_number = r['exhibit_number']

        filing_date = datetime.strptime(r['filing_date'], '%Y-%m-%d').strftime('%B %-d, %Y')

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

        score = r.get('_score', 0.0)

        description = r.get('attachment_description')
        desc_display = f" - {description}" if description else ""

        return (
                f"**[Excerpt #{index} | ID: {excerpt_id}] Press Release ({page_display})** | Score: {score:.3f} | Filing #{r['filing_id']} | "
                f"{company_name} ({symbols}) | {form} | Exhibit {exhibit_number}{desc_display} | Filed: {filing_date}\n\n" +
                f"{content}\n\n---\n"
        )
