import asyncio
import traceback
from pydantic import BaseModel, Field
from typing import Literal

from agent.message import Action, Message
from agent.action_response import ActionResponse
from agent.actions.base_action import BaseAction


class SearchFiling(BaseModel):
    """Search within a specific filing by filing_id for targeted information retrieval

    * Much faster than SemanticSearch when you already know which filing to search
    * Searches both structured notes (MD&A, footnotes) and raw sections (Risk Factors, etc.)
    * Use this after ListFilings to search within a known filing
    """
    filing_id: int = Field(
        description="The filing ID to search within (get this from ListFilings)"
    )
    query: str = Field(
        description=(
            "Describe exactly what you're looking for in this filing. "
            "Examples: 'adjusted EBITDA definition and reconciliation', "
            "'depreciation policy', 'revenue recognition methodology', "
            "'table of segment revenue by geography'"
        ),
        min_length=5
    )
    limit: Literal[5, 10, 20] = Field(
        default=5,
        description="Number of results to return. Start with 5 for most queries."
    )

    class Config:
        json_schema_extra = {
            "examples": [
                {
                    "filing_id": 123,
                    "query": "adjusted EBITDA definition including what items are added back and excluded from the calculation",
                    "limit": 5
                },
                {
                    "filing_id": 456,
                    "query": "table showing revenue breakdown by product category and geographic region",
                    "limit": 10
                }
            ]
        }
        extra = "forbid"


class SearchFilingAction(BaseAction):
    name: str = 'SearchFiling'
    schema = SearchFiling

    async def call(self, action: Action) -> ActionResponse:
        """Executes vector search within a specific filing's note and section chunks"""
        try:
            args = SearchFiling(**action.body)
        except Exception as e:
            self.log_start("SearchFiling")
            self.log_error(f"Validation failed: {e}")
            return ActionResponse(
                message=Message(role="tool", status="completed", content=str(e), error=True, action_id=action.id)
            )

        params = f"filing_id={args.filing_id}, query='{args.query}', limit={args.limit}"
        self.log_start("SearchFiling", params=params)

        # Verify filing exists
        filing_result = await (
            self.database
            .table("company_filings")
            .select("id,form,filing_date,company_name,company_symbols")
            .eq("id", args.filing_id)
            .execute()
        )

        if not filing_result.data:
            self.log_error(f"Filing {args.filing_id} not found")
            return ActionResponse(
                message=Message(
                    role="tool",
                    status="completed",
                    content=f"Filing {args.filing_id} not found.",
                    error=True,
                    action_id=action.id
                )
            )

        filing = filing_result.data[0]

        # Search both note chunks and section chunks in parallel
        tasks = [
            self._search_note_chunks(args, filing),
            self._search_section_chunks(args, filing)
        ]

        results_list = await asyncio.gather(*tasks)

        all_results = []
        for results in results_list:
            all_results.extend(results)

        if not all_results:
            self.log_done("No matches found", content=f"No matching results found in filing {args.filing_id}.")
            return ActionResponse(
                message=Message(
                    role="tool",
                    status="completed",
                    content=f"No matching results found in filing {args.filing_id}.",
                    action_id=action.id
                )
            )

        # Sort by score and limit
        all_results.sort(key=lambda x: x.get('_score', 0), reverse=True)
        top_results = all_results[:args.limit]

        content = self._format_results(top_results, filing)

        summary = f"Found {len(top_results)} excerpt(s) in {filing['form']} filing"
        self.log_done(summary, content=content)

        return ActionResponse(
            message=Message(
                role="tool",
                status="completed",
                content=content,
                action_id=action.id
            )
        )

    async def _search_note_chunks(self, args: SearchFiling, filing: dict) -> list:
        """Vector search filing note chunks"""
        try:
            query = (
                self.database
                .table("company_filing_note_chunks")
                .select(
                    "id,filing_note_id,note_title,content,has_table,"
                    "filing_id,form,filing_date,fiscal_year,fiscal_period"
                )
                .eq("filing_id", args.filing_id)
            )

            result = await query.vector_search(
                args.query,
                "embedding",
                topk=args.limit * 2,
                return_scores=True
            ).execute()

            for r in result.data:
                r['_type'] = 'filing_note_chunk'
                r['company_name'] = filing['company_name']
                r['company_symbols'] = filing['company_symbols']

            return result.data

        except Exception as e:
            self.log_error(f"Note chunk search failed: {e}\n{traceback.format_exc()}")
            return []

    async def _search_section_chunks(self, args: SearchFiling, filing: dict) -> list:
        """Vector search filing section chunks"""
        try:
            query = (
                self.database
                .table("company_filing_section_chunks")
                .select(
                    "id,section,page,pages,has_table,"
                    "filing_id,form,filing_date,report_date,fiscal_year,fiscal_period"
                )
                .eq("filing_id", args.filing_id)
            )

            result = await query.vector_search(
                args.query,
                "embedding",
                topk=args.limit * 2,
                return_scores=True
            ).execute()

            for r in result.data:
                r['_type'] = 'filing_section_chunk'
                r['company_name'] = filing['company_name']
                r['company_symbols'] = filing['company_symbols']

            return result.data

        except Exception as e:
            self.log_error(f"Section chunk search failed: {e}\n{traceback.format_exc()}")
            return []

    def _format_results(self, results: list, filing: dict) -> str:
        """Format merged results with filing context header"""
        company_name = filing['company_name']
        symbols = ','.join(filing['company_symbols'])
        form = filing['form']
        filing_date = filing['filing_date']

        header = f"**Search Results from {form} Filing (ID: {filing['id']})**\n"
        header += f"{company_name} ({symbols}) | Filed: {filing_date}\n\n---\n\n"

        output = [header]
        for i, r in enumerate(results, start=1):
            if r['_type'] == 'filing_note_chunk':
                output.append(self._format_note_chunk(r, i))
            elif r['_type'] == 'filing_section_chunk':
                output.append(self._format_section_chunk(r, i))

        return "".join(output) if len(output) > 1 else "No results found."

    @staticmethod
    def _format_note_chunk(r: dict, index: int) -> str:
        """Format filing note chunk result"""
        from datetime import datetime

        excerpt_id = r['id']
        note_title = r['note_title']
        content = r['content'].strip()
        fy = r.get('fiscal_year') or '—'
        fp = r.get('fiscal_period') or '—'

        return (
            f"**[Excerpt {index} | ID: {excerpt_id}] Note: {note_title}**\n"
            f"Fiscal: {fp} {fy}\n\n"
            f"{content}\n\n---\n\n"
        )

    @staticmethod
    def _format_section_chunk(r: dict, index: int) -> str:
        """Format filing section chunk result"""
        from datetime import datetime

        excerpt_id = r['id']
        section = r['section'].replace('_', ' ').title()

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

        fiscal_year = r.get('fiscal_year')
        fiscal_period = r.get('fiscal_period')
        fiscal_info = f"Fiscal: {fiscal_period} FY{fiscal_year}" if fiscal_year and fiscal_period else (
            f"Fiscal: FY{fiscal_year}" if fiscal_year else "")

        return (
            f"**[Excerpt {index} | ID: {excerpt_id}] {section} ({page_display})**\n" +
            (f"{fiscal_info}\n\n" if fiscal_info else "\n") +
            f"{content}\n\n---\n\n"
        )
