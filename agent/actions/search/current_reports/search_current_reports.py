import traceback
from typing import List, Literal, Optional
from pydantic import BaseModel, Field, field_validator
from datetime import date, datetime

from agent.message import Action, Message
from agent.action_response import ActionResponse, ActionFollowUp
from agent.actions.base_action import BaseAction
from agent.actions.search.current_reports.read_filing import ReadFilingAction
from agent.actions.search.current_reports.read_attachment import ReadAttachmentAction
from agent.actions.search.current_reports.exit_reading import ExitReadingAction


class SearchCurrentReports(BaseModel):
    """Search current reports (8-K, 6-K filings) using natural language (semantic/vector search on summaries).

    Searches current reports to identify relevant filings:
    - 8-K: Material events, M&A, earnings, management changes, contract awards, etc.
    - 6-K: Foreign private issuer current reports (equivalent to 8-K for non-US companies)
    - Returns filings with their summaries and available attachments
    - After search, you can read specific filings or attachments using the reading actions

    Does NOT search: Filing body content, press releases, or 10-K/Q reports.
    For press releases, use SearchPressReleases. For filing sections, use SearchFilingSections.
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
    query_description: str = Field(
        description="Describe what you're looking for. Ex. 'Preferred stock offerings', 'Debt financing announcements'"
    )
    depth: Literal['low', 'medium', 'high'] = Field(
        default='medium',
        description="Search depth: 'low' (5 results), 'medium' (10 results), 'high' (20 results)"
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


class SearchCurrentReportsAction(BaseAction):
    name: str = 'SearchCurrentReports'
    schema = SearchCurrentReports

    async def call(self, action: Action) -> ActionResponse:
        """Executes a search over current report summaries and enables reading loop"""
        try:
            args = SearchCurrentReports(**action.body)
        except Exception as e:
            self.log_start("SearchCurrentReports")
            self.log_error(f"Validation failed: {e}")
            return ActionResponse(
                message=Message(role="tool", status="completed", content=str(e), error=True, action_id=action.id)
            )

        params = f"symbol={args.symbol}, {args.start_date} → {args.end_date}"
        self.log_start("SearchCurrentReports", params=params, thought=args.thought)

        forms = ['8-K', '8-K/A', '6-K', '6-K/A']
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

        results = await self._search_current_reports(args, forms)

        if not results:
            self.log_done("No matches found")
            return ActionResponse(
                message=Message(
                    role="tool",
                    status="completed",
                    content="No matching current reports found.",
                    action_id=action.id
                )
            )

        depth_map = {'low': 5, 'medium': 10, 'high': 20}
        limit = depth_map[args.depth]

        results = results[:limit]

        filing_ids = [r['id'] for r in results]
        attachments_by_filing = await self._load_attachments(filing_ids)

        content = self._format_results(results, attachments_by_filing)

        summary = f"Found {len(results)} current report(s)"
        self.log_done(summary)

        all_filing_ids = [r['id'] for r in results]
        all_attachment_ids = [
            att['id']
            for filing_atts in attachments_by_filing.values()
            for att in filing_atts
        ]

        read_filing = ReadFilingAction(
            valid_filing_ids=all_filing_ids,
            siblings=None,  # Will set after all created
            database=self.database,
            edgar_user_agent=self.edgar_user_agent,
            start_year=self.start_year
        )

        read_attachment = ReadAttachmentAction(
            valid_attachment_ids=all_attachment_ids,
            siblings=None,
            database=self.database,
            edgar_user_agent=self.edgar_user_agent,
            start_year=self.start_year
        )

        exit_action = ExitReadingAction(
            database=self.database,
            edgar_user_agent=self.edgar_user_agent,
            start_year=self.start_year
        )

        siblings = (read_filing, read_attachment, exit_action)
        read_filing.siblings = siblings
        read_attachment.siblings = siblings

        return ActionResponse(
            message=Message(
                role="tool",
                status="completed",
                content=content,
                action_id=action.id
            ),
            follow_up=ActionFollowUp(
                actions=[read_filing, read_attachment, exit_action],
                force=True
            )
        )

    async def _search_current_reports(self, args: SearchCurrentReports, forms: List[str]) -> List[dict]:
        """Vector search current report summaries using company_filings view"""
        try:
            depth_map = {'low': 5, 'medium': 10, 'high': 20}
            limit = depth_map[args.depth]

            result = (
                self.database
                .table("company_filings")
                .select(
                    "id,form,title,summary,items,filing_date,report_date,num_pages,num_attachments,"
                    "company_name,company_symbols,fiscal_year,fiscal_period"
                )
                .contains("company_symbols", args.symbol)
                .in_("form", forms)
                .gte("filing_date", args.start_date)
                .lte("filing_date", args.end_date)
                .vector_search(
                    args.query_description,
                    "summary",
                    topk=limit,
                    return_scores=True
                )
                .execute()
            )

            return result.data

        except Exception as e:
            self.log_error(f"Current report search failed: {e}\n{traceback.format_exc()}")
            return []

    async def _load_attachments(self, filing_ids: List[int]) -> dict:
        """Load attachments for given filing IDs, returns dict mapping filing_id -> list of attachments"""
        if not filing_ids:
            return {}

        result = (
            self.database
            .table("filing_attachments")
            .select("id,filing_id,exhibit_number,title,type,num_pages")
            .in_("filing_id", filing_ids)
            .order("exhibit_number")
            .execute()
        )

        attachments_by_filing = {}
        for att in result.data:
            filing_id = att['filing_id']
            if filing_id not in attachments_by_filing:
                attachments_by_filing[filing_id] = []
            attachments_by_filing[filing_id].append(att)

        return attachments_by_filing

    def _format_results(self, results: List[dict], attachments_by_filing: dict) -> str:
        """Format search results with nested attachments"""
        output = []

        for i, r in enumerate(results):
            output.append(self._format_filing(r, i, attachments_by_filing.get(r['id'], [])))

        if not output:
            return "No results found."

        instructions = (
            "\n**NEXT STEPS:** Use ReadFiling or ReadAttachment to read specific documents from the results above. "
            "When finished reading, call ExitReading to continue."
        )
        return "\n".join(output) + instructions

    @staticmethod
    def _format_filing(filing: dict, index: int, attachments: List[dict]) -> str:
        """Format a single filing with nested attachments"""
        filing_id = filing['id']
        form = filing['form']
        title = filing.get('title') or '—'
        summary = filing.get('summary') or '—'
        items = ','.join(filing.get('items', [])) if filing.get('items') else '—'
        num_pages = filing.get('num_pages', '?')
        company_name = filing['company_name']
        symbols = ','.join(filing['company_symbols'])

        filing_date = datetime.strptime(filing['filing_date'], '%Y-%m-%d').strftime('%B %-d, %Y')

        score = filing.get('_score', 0.0)

        output = [
            f"**[Filing #{index} | ID: {filing_id}]**",
            f"{company_name} ({symbols}) | {form} | Filed: {filing_date}",
            f"**Title:** {title}",
            f"**Items:** {items} | **Pages:** {num_pages}",
            f"**Summary:** {summary}",
        ]

        if attachments:
            output.append(f"\n**Attachments ({len(attachments)}):**")
            for att in attachments:
                att_id = att['id']
                exhibit_num = att['exhibit_number']
                att_title = att.get('title') or '—'
                att_type = att.get('type', '').replace('_', ' ').title()
                att_pages = att.get('num_pages', '?')

                output.append(
                    f"  • [ID: {att_id}] Exhibit {exhibit_num} ({att_type}) - {att_title} | {att_pages} pages"
                )

        output.append("\n---\n")
        return "\n".join(output)
