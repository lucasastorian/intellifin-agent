import uuid
import traceback
from typing import List, Literal, Optional
from pydantic import BaseModel, Field, field_validator
from datetime import date

from agent.message import Action, Message
from agent.action_response import ActionResponse, ActionFollowUp
from agent.actions.base_action import BaseAction
from agent.actions.__search.filing_notes.curate_filing_note_excerpts import CurateFilingNoteExcerptsAction


class SearchFilingNotes(BaseModel):
    """Search financial statement notes using natural language (semantic/vector search).

    Searches note excerpts from 10-K/10-Q/20-F filings:
    - Accounting policies, revenue recognition, debt covenants, leases, etc.
    - Returns relevant excerpts/chunks from notes
    - Use natural language to describe what you're looking for
    - After curation, you can optionally read full notes for more context

    Does NOT search: Filing body, press releases, or attachments.
    For filing sections, use SearchFilingSections. For press releases, use SearchPressReleases.
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
        description="Describe the excerpt you're looking for. Ex. 'Revenue recognition policy details', 'Debt covenant terms'"
    )
    tables_only: Optional[bool] = Field(
        default=None,
        description="Filter to only chunks with tables (True) or without tables (False)"
    )
    depth: Literal['low', 'medium', 'high'] = Field(
        default='medium',
        description="Search depth: 'low' (10 results), 'medium' (15 results), 'high' (30 results)"
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


class SearchFilingNotesAction(BaseAction):
    name: str = 'SearchFilingNotes'
    schema = SearchFilingNotes

    async def call(self, action: Action) -> ActionResponse:
        """Executes a search over filing note chunks"""
        try:
            args = SearchFilingNotes(**action.body)
        except Exception as e:
            self.log_start("SearchFilingNotes")
            self.log_error(f"Validation failed: {e}")
            return ActionResponse(
                message=Message(role="tool", status="completed", content=str(e), error=True, action_id=action.id)
            )

        params = f"symbol={args.symbol}, {args.start_date} → {args.end_date}"
        self.log_start("SearchFilingNotes", params=params, thought=args.thought)

        # Forms that have financial statement notes
        forms = ['10-K', '10-K/A', '10-Q', '10-Q/A', '20-F', '20-F/A']
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

        results = await self._search_filing_notes(args)

        if not results:
            self.log_done("No matches found", content=f"No filing note matches for query '{args.query}'")
            return ActionResponse(
                message=Message(
                    role="tool",
                    status="completed",
                    content="No matching filing notes found.",
                    action_id=action.id
                )
            )

        depth_map = {'low': 10, 'medium': 15, 'high': 30}
        limit = depth_map[args.depth]

        results.sort(key=lambda x: x.get('_score', 0), reverse=True)
        top_results = results[:limit]

        content = self._format_results(top_results)

        unique_notes = len({r['filing_note_id'] for r in top_results})
        unique_filings = len({r['filing_id'] for r in top_results})
        summary = f"Found {len(top_results)} excerpt(s) from {unique_notes} note(s) across {unique_filings} filing(s)"
        self.log_done(summary, content=content)

        search_message_id = str(uuid.uuid4())

        search_message = Message(
            id=search_message_id,
            role="tool",
            status="completed",
            content=content,
            action_id=action.id
        )

        curate_action = CurateFilingNoteExcerptsAction(
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

    async def _search_filing_notes(self, args: SearchFilingNotes) -> List[dict]:
        """Vector search filing note chunks using company_filing_note_chunks view"""
        try:
            query = (
                self.database
                .table("company_filing_note_chunks")
                .select(
                    "id,filing_note_id,note_title,note_filename,content,has_table,"
                    "filing_id,form,filing_date,fiscal_year,fiscal_period,"
                    "company_name,company_symbols"
                )
                .contains("company_symbols", args.symbol)
                .gte("filing_date", args.start_date)
                .lte("filing_date", args.end_date)
            )

            if args.tables_only is not None:
                query = query.eq("has_table", args.tables_only)

            depth_map = {'low': 10, 'medium': 15, 'high': 30}
            limit = depth_map[args.depth]

            result = await query.vector_search(
                args.excerpt_description,
                "embedding",  # Vector search on embedding field (has header context)
                topk=limit,
                return_scores=True
            ).execute()

            for r in result.data:
                r['_type'] = 'filing_note_chunk'

            return result.data

        except Exception as e:
            self.log_error(f"Filing note search failed: {e}\n{traceback.format_exc()}")
            return []

    @staticmethod
    def curate(excerpts: List[dict], summary: str) -> str:
        """Reformat search results with only curated excerpts"""
        output = [f"**Filing Note Search Results (Curated)**\n\n_{summary}_\n"]
        for i, r in enumerate(excerpts):
            output.append(SearchFilingNotesAction._format_note_excerpt(r, i))
        return "\n".join(output)

    def _format_results(self, results: List[dict]) -> str:
        """Format all results"""
        output = []
        for i, r in enumerate(results):
            output.append(self._format_note_excerpt(r, i))

        if not output:
            return "No results found."

        # Add curation instructions
        instructions = (
            "\n**INSTRUCTIONS:** Review the note excerpts above and select only those relevant to your search query using their IDs. "
            "If none are relevant or only marginally useful, provide a brief summary of what you found instead."
        )
        return "\n".join(output) + instructions

    @staticmethod
    def _format_note_excerpt(r: dict, index: int) -> str:
        """Format filing note excerpt result"""
        from datetime import datetime

        excerpt_id = r['id']
        note_id = r['filing_note_id']
        note_title = r['note_title']
        company_name = r['company_name']
        symbols = ','.join(r['company_symbols'])
        form = r['form']

        # Format date naturally
        filing_date = datetime.strptime(r['filing_date'], '%Y-%m-%d').strftime('%B %-d, %Y')

        content = r['content'].strip()

        # Score is injected by vector search
        score = r.get('_score', 0.0)

        # Fiscal period
        fy = r.get('fiscal_year') or '—'
        fp = r.get('fiscal_period') or '—'

        return (
            f"**[Excerpt #{index} | ID: {excerpt_id}] Note: {note_title}** | Score: {score:.3f} | Note ID: {note_id} | Filing #{r['filing_id']}\n"
            f"{company_name} ({symbols}) | {form} | Fiscal: {fp} {fy} | Filed: {filing_date}\n\n"
            f"{content}\n\n---\n"
        )
