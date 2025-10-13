import asyncio
import traceback
from pydantic import BaseModel, Field
from typing import List, Literal, Optional
from datetime import date, timedelta

from agent.message import Action, Message
from agent.action_response import ActionResponse
from agent.actions.base_action import BaseAction


# TODO: Add search_mode parameter: Literal["semantic", "keyword"]
# - semantic (default): Vector search for conceptual/natural language queries
# - keyword: BM25 keyword search for exact terms (company names, technical terms, product names, exact phrases)
# Implementation: Add search_mode to SemanticSearch schema, conditionally use .keyword_search() vs .vector_search()
# Use cases for keyword: "Taiwan Semiconductor", "ASC 606", "material weakness", specific company/product names


def default_start_date() -> str:
    """Default to 1 year ago"""
    return (date.today() - timedelta(days=365)).strftime('%Y-%m-%d')


def default_end_date() -> str:
    """Default to today"""
    return date.today().strftime('%Y-%m-%d')


class SemanticSearch(BaseModel):
    """Search a single company's SEC filings and earnings transcripts via a natural language query

    * Be specific about format: "table showing...", "discussion of...", "accounting policy for..."
    * Include temporal context: "Q1 2024 guidance", "fiscal 2023 depreciation", "forward-looking capex"
    * Specify what you expect: "percentage breakdown", "dollar amounts", "risk factors related to..."
    * Think about document type: earnings calls for guidance, 8-Ks for events, 10-Ks for policies
    """
    symbol: str = Field(
        description="Stock ticker symbol (e.g., 'AAPL', 'MSFT')"
    )
    query: str = Field(
        description=(
            "Describe exactly what you're looking for. "
            "Examples: 'forward capital expenditure guidance for 2025', "
            "'accounting policy for revenue recognition', "
            "'risk factors related to supply chain disruption', "
            "'table of R&D expenses by quarter'"
        ),
        min_length=5
    )

    start_date: str = Field(
        description="Start date (YYYY-MM-DD)."
    )

    end_date: str = Field(
        default_factory=default_end_date,
        description="End date (YYYY-MM-DD). Defaults to today."
    )

    document_types: List[Literal[
        "annual_reports",
        "quarterly_reports",
        "current_reports",
        "proxy_statements",
        "earnings_transcripts",
    ]] = Field(
        description=(
            "Filter by document type."
            "earnings_transcript: guidance, Q&A | quarterly_report: 10-Q interim results | "
            "annual_report: 10-K comprehensive | current_report: 8-K material events"
        )
    )

    current_report_focus: Optional[List[Literal[
        "financing_terms",  # EX-1.1, EX-3.1
        "debt_terms",  # EX-4.1/4.2
        "merger_terms",  # EX-2.1
        "press_investor"  # EX-99 / 99.1 / 99.2
    ]]] = Field(
        description="The types of documents to focus on for current reports in particular"
    )

    limit: Literal[5, 10, 20] = Field(
        default=5,
        description="Number of results to return. Start with 5 for most queries."
    )

    class Config:
        json_schema_extra = {
            "examples": [
                {
                    "symbol": "MSFT",
                    "query": "forward capital expenditure guidance AI infrastructure spending 2025",
                    "document_types": ["earnings_transcript"],
                    "start_date": "2024-01-01",
                    "limit": 5
                },
                {
                    "symbol": "NVDA",
                    "query": "risk factors related to export controls China revenue restrictions",
                    "document_types": ["annual_report", "quarterly_report"],
                    "start_date": "2024-01-01",
                    "limit": 10
                },
                {
                    "symbol": "META",
                    "query": "accounting policy for capitalizing internal-use software development costs",
                    "document_types": ["annual_report"],
                    "start_date": "2023-01-01",
                    "limit": 5
                },
                {
                    "symbol": "BA",
                    "query": "material production halt announcement or delivery delays",
                    "document_types": ["current_report"],
                    "start_date": "2024-01-01",
                    "limit": 5
                }
            ]
        }
        extra = "forbid"


class SemanticSearchAction(BaseAction):
    name: str = 'SemanticSearch'
    schema = SemanticSearch

    async def call(self, action: Action) -> ActionResponse:
        """Executes parallel vector searches across filing note chunks and section chunks"""
        try:
            args = SemanticSearch(**action.body)
        except Exception as e:
            self.log_start("SemanticSearch")
            self.log_error(f"Validation failed: {e}")
            return ActionResponse(
                message=Message(role="tool", status="completed", content=str(e), error=True, action_id=action.id)
            )

        params = f"symbol={args.symbol}, query='{args.query}', {args.start_date} → {args.end_date}, documents={args.document_types}"
        if args.current_report_focus:
            params += f", focus={args.current_report_focus}"
        params += f", limit={args.limit}"
        self.log_start("SemanticSearch", params=params)

        forms = self._get_forms_for_document_types(args.document_types)
        include_transcripts = "earnings_transcript" in args.document_types
        not_found = await self.sync_symbols(
            symbols=[args.symbol],
            forms=forms,
            start_date=args.start_date,
            end_date=args.end_date,
            include_earnings_transcripts=include_transcripts
        )

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

        tasks = []

        if forms:
            tasks.extend([
                self._search_note_chunks(args, forms),
                self._search_section_chunks(args, forms),
                self._search_attachment_chunks(args, forms)
            ])

        if "earnings_transcript" in args.document_types:
            tasks.append(self._search_transcript_chunks(args))

        results_list = await asyncio.gather(*tasks)

        all_results = []
        for results in results_list:
            all_results.extend(results)

        if not all_results:
            self.log_done("No matches found")
            return ActionResponse(
                message=Message(
                    role="tool",
                    status="completed",
                    content="No matching results found.",
                    action_id=action.id
                )
            )

        all_results.sort(key=lambda x: x.get('_score', 0), reverse=True)
        top_results = all_results[:args.limit]

        content = self._format_results(top_results)

        summary = f"Found {len(top_results)} excerpt(s) across {len({r.get('filing_id') or r.get('transcript_id') for r in top_results})} document(s)"
        self.log_done(summary)

        return ActionResponse(
            message=Message(
                role="tool",
                status="completed",
                content=content,
                action_id=action.id
            )
        )

    async def _search_note_chunks(self, args: SemanticSearch, forms: List[str]) -> List[dict]:
        """Vector search filing note chunks"""
        try:
            query = (
                self.database
                .table("company_filing_note_chunks")
                .select(
                    "id,filing_note_id,note_title,content,has_table,"
                    "filing_id,form,filing_date,fiscal_year,fiscal_period,"
                    "company_name,company_symbols"
                )
                .contains("company_symbols", args.symbol)
                .in_("form", forms)
                .gte("filing_date", args.start_date)
                .lte("filing_date", args.end_date)
            )

            result = await query.vector_search(
                args.query,
                "embedding",
                topk=args.limit * 2,
                return_scores=True
            ).execute()

            for r in result.data:
                r['_type'] = 'filing_note_chunk'

            return result.data

        except Exception as e:
            self.log_error(f"Note chunk search failed: {e}\n{traceback.format_exc()}")
            return []

    async def _search_section_chunks(self, args: SemanticSearch, forms: List[str]) -> List[dict]:
        """Vector search filing section chunks"""
        try:
            query = (
                self.database
                .table("company_filing_section_chunks")
                .select(
                    "id,section,page,pages,has_table,"
                    "filing_id,form,filing_date,report_date,fiscal_year,fiscal_period,"
                    "company_name,company_symbols"
                )
                .contains("company_symbols", args.symbol)
                .in_("form", forms)
                .gte("report_date", args.start_date)
                .lte("report_date", args.end_date)
            )

            result = await query.vector_search(
                args.query,
                "embedding",
                topk=args.limit * 2,
                return_scores=True
            ).execute()

            for r in result.data:
                r['_type'] = 'filing_section_chunk'

            return result.data

        except Exception as e:
            self.log_error(f"Section chunk search failed: {e}\n{traceback.format_exc()}")
            return []

    async def _search_attachment_chunks(self, args: SemanticSearch, forms: List[str]) -> List[dict]:
        """Vector search filing attachment chunks with optional focus filtering"""
        try:
            query = (
                self.database
                .table("company_filing_attachment_chunks")
                .select(
                    "id,index,page,pages,has_table,exhibit_number,attachment_description,attachment_type,"
                    "filing_id,form,filing_date,report_date,fiscal_year,fiscal_period,"
                    "company_name,company_symbols"
                )
                .contains("company_symbols", args.symbol)
                .in_("form", forms)
                .gte("filing_date", args.start_date)
                .lte("filing_date", args.end_date)
            )

            # Apply current_report_focus filtering if specified
            if args.current_report_focus:
                attachment_types = self._get_attachment_types_for_focus(args.current_report_focus)
                query = query.in_("attachment_type", attachment_types)

            result = await query.vector_search(
                args.query,
                "embedding",
                topk=args.limit * 2,
                return_scores=True
            ).execute()

            for r in result.data:
                r['_type'] = 'filing_attachment_chunk'

            return result.data

        except Exception as e:
            self.log_error(f"Attachment chunk search failed: {e}\n{traceback.format_exc()}")
            return []

    async def _search_transcript_chunks(self, args: SemanticSearch) -> List[dict]:
        """Vector search earnings transcript chunks"""
        try:
            query = (
                self.database
                .table("company_earnings_transcript_chunks")
                .select(
                    "id,index,sections,transcript_id,fiscal_year,fiscal_period,"
                    "company_name,company_symbols"
                )
                .contains("company_symbols", args.symbol)
            )

            result = await query.vector_search(
                args.query,
                "embedding",
                topk=args.limit * 2,
                return_scores=True
            ).execute()

            for r in result.data:
                r['_type'] = 'earnings_transcript_chunk'

            return result.data

        except Exception as e:
            self.log_error(f"Transcript chunk search failed: {e}\n{traceback.format_exc()}")
            return []

    @staticmethod
    def _get_forms_for_document_types(document_types: List[str]) -> List[str]:
        """Maps document types to SEC forms"""
        mapping = {
            'annual_report': ['10-K', '10-K/A', '20-F', '20-F/A'],
            'quarterly_report': ['10-Q', '10-Q/A'],
            'current_report': ['8-K', '8-K/A', '6-K', '6-K/A'],
            'proxy_statements': ['DEF 14A', 'DEF 14A/A'],
        }

        forms = []
        for doc_type in document_types:
            if doc_type in mapping:
                forms.extend(mapping[doc_type])

        return list(set(forms))

    @staticmethod
    def _get_attachment_types_for_focus(focus_areas: List[str]) -> List[str]:
        """Maps current_report_focus values to attachment types"""
        mapping = {
            'financing_terms': ['underwriting_agreement', 'certificate_of_designations'],
            'debt_terms': ['indenture', 'supplemental_indenture', 'debt_instrument'],
            'merger_terms': ['merger_agreement'],
            'press_investor': ['press_or_investor']
        }

        attachment_types = []
        for focus in focus_areas:
            if focus in mapping:
                attachment_types.extend(mapping[focus])

        return list(set(attachment_types))

    def _format_results(self, results: List[dict]) -> str:
        """Format merged results"""
        output = []
        for i, r in enumerate(results):
            if r['_type'] == 'filing_note_chunk':
                output.append(self._format_note_chunk(r, i))
            elif r['_type'] == 'filing_section_chunk':
                output.append(self._format_section_chunk(r, i))
            elif r['_type'] == 'filing_attachment_chunk':
                output.append(self._format_attachment_chunk(r, i))
            elif r['_type'] == 'earnings_transcript_chunk':
                output.append(self._format_transcript_chunk(r, i))

        return "\n".join(output) if output else "No results found."

    @staticmethod
    def _format_note_chunk(r: dict, index: int) -> str:
        """Format filing note chunk result"""
        from datetime import datetime

        excerpt_id = r['id']
        note_title = r['note_title']
        company_name = r['company_name']
        symbols = ','.join(r['company_symbols'])
        form = r['form']
        filing_date = datetime.strptime(r['filing_date'], '%Y-%m-%d').strftime('%B %-d, %Y')
        content = r['content'].strip()
        score = r.get('_score', 0.0)
        fy = r.get('fiscal_year') or '—'
        fp = r.get('fiscal_period') or '—'

        return (
            f"**[Excerpt #{index} | ID: {excerpt_id}] Note: {note_title}**  Filing #{r['filing_id']}\n"
            f"{company_name} ({symbols}) | {form} | Fiscal: {fp} {fy} | Filed: {filing_date}\n\n"
            f"{content}\n\n---\n"
        )

    @staticmethod
    def _format_section_chunk(r: dict, index: int) -> str:
        """Format filing section chunk result"""
        from datetime import datetime

        excerpt_id = r['id']
        company_name = r['company_name']
        symbols = ','.join(r['company_symbols'])
        form = r['form']
        section = r['section'].replace('_', ' ').title()
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

        fiscal_year = r.get('fiscal_year')
        fiscal_period = r.get('fiscal_period')
        fiscal_info = f"FY{fiscal_year} {fiscal_period}" if fiscal_year and fiscal_period else (
            f"FY{fiscal_year}" if fiscal_year else "")

        score = r.get('_score', 0.0)

        return (
                f"**[Excerpt #{index} | ID: {excerpt_id}] {section} ({page_display})** | Filing #{r['filing_id']}\n"
                f"{company_name} ({symbols}) | {form} | Filed: {filing_date} | Report: {report_date}" +
                (f" | {fiscal_info}" if fiscal_info else "") + "\n\n" +
                f"{content}\n\n---\n"
        )

    @staticmethod
    def _format_attachment_chunk(r: dict, index: int) -> str:
        """Format filing attachment chunk result (press releases)"""
        from datetime import datetime

        excerpt_id = r['id']
        company_name = r['company_name']
        symbols = ','.join(r['company_symbols'])
        form = r['form']
        exhibit_number = r['exhibit_number']
        attachment_type = r['attachment_type'].replace('_', ' ').title()
        description = r.get('attachment_description', '')
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

        fiscal_year = r.get('fiscal_year')
        fiscal_period = r.get('fiscal_period')
        fiscal_info = f"FY{fiscal_year} {fiscal_period}" if fiscal_year and fiscal_period else (
            f"FY{fiscal_year}" if fiscal_year else "")

        score = r.get('_score', 0.0)

        return (
                f"**[Excerpt #{index} | ID: {excerpt_id}] {attachment_type}: EX-{exhibit_number} ({page_display})** | Filing #{r['filing_id']}\n"
                f"{company_name} ({symbols}) | {form} | Filed: {filing_date}" +
                (f" | {fiscal_info}" if fiscal_info else "") +
                (f"\n{description}" if description else "") + "\n\n" +
                f"{content}\n\n---\n"
        )

    @staticmethod
    def _format_transcript_chunk(r: dict, index: int) -> str:
        """Format earnings transcript chunk result"""
        excerpt_id = r['id']
        company_name = r['company_name']
        symbols = ','.join(r['company_symbols'])
        fiscal_year = r['fiscal_year']
        fiscal_period = r['fiscal_period']
        sections = r['sections']
        score = r.get('_score', 0.0)

        content_parts = []
        for section in sections:
            speaker = section.get('speaker', 'Unknown')
            content = section.get('content', '').strip()
            content_parts.append(f"**{speaker}:** {content}")

        content = "\n\n".join(content_parts)

        return (
            f"**[Excerpt #{index} | ID: {excerpt_id}] Earnings Transcript** | Transcript #{r['transcript_id']}\n"
            f"{company_name} ({symbols}) | {fiscal_period} FY{fiscal_year}\n\n"
            f"{content}\n\n---\n"
        )
