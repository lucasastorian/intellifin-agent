from typing import List, Literal
from pydantic import BaseModel, Field, ValidationError

from agent.actions.base_action import BaseAction
from agent.message import Action, Message

AllowedNotesForm = Literal["10-K", "10-Q", "20-F"]


class KeywordSearchFilingPages(BaseModel):
    """BM-25 Keyword search across the primary document pages of one or more filings. Returns the top 10 matching pages.

    - Will search any filing: 10-Ks/10-Qs/20-Fs, etc. etc.
    - For 8-Ks with attached press releases, this does NOT actually search the press releases.
    """
    filing_ids: List[int] = Field(description="Exact filing ids to search within.")
    query: str = Field(..., description="Keyword query")


class KeywordSearchFilingPagesAction(BaseAction):
    name: str = 'KeywordSearchFilingPages'
    schema = KeywordSearchFilingPages
    limit: int = 10

    async def call(self, action: Action):
        """Searches filing pages using BM25 keyword search"""
        try:
            args = self.validate(action)
        except RuntimeError as e:
            self.log_start("KeywordSearchFilingPages")
            self.log_error(f"Validation failed: {e}")
            return Message(role="tool", status="completed", content=str(e), error=True, action_id=action.id)

        params = f"Query: '{args.query}' across {len(args.filing_ids)} filing(s)"
        self.log_start("KeywordSearchFilingPages", params)

        try:
            results = (
                self.database
                .table("filing_pages")
                .keyword_search(args.query, returning="id,filing_id,page,content")
                .in_("filing_id", args.filing_ids)
                .limit(self.limit)
                .execute()
            )

        except Exception as e:
            self.log_error(f"Search failed: {e}")
            return Message(role="tool", status="completed", content=f"Search error: {e}", error=True, action_id=action.id)

        if not results:
            self.log_done("No matches found")
            return Message(role="tool", status="completed", content="No matching pages found.", action_id=action.id)

        filing_ids = list({r['filing_id'] for r in results})
        filings = (
            self.database
            .table("filings")
            .select("id,company_id,form,filing_date,accession_number")
            .in_("id", filing_ids)
            .execute()
        )
        filing_by_id = {f['id']: f for f in filings}

        company_ids = list({f['company_id'] for f in filings})
        companies = (
            self.database
            .table("companies")
            .select("id,name,symbols")
            .in_("id", company_ids)
            .execute()
        )
        company_by_id = {c['id']: c for c in companies}

        content = self._format_results(results, filing_by_id, company_by_id)

        unique_filings = len(filing_ids)
        summary = f"Found {len(results)} matching page(s) across {unique_filings} filing(s)"

        self.log_done(summary)

        return Message(role="tool", status="completed", content=content, action_id=action.id)

    @staticmethod
    def validate(action: Action) -> KeywordSearchFilingPages:
        """Validates the action against the Pydantic schema"""
        try:
            return KeywordSearchFilingPages(**action.body)
        except ValidationError as e:
            raise RuntimeError(f"Validation failed: {e}") from e

    @staticmethod
    def _format_results(results: List[dict], filing_by_id: dict, company_by_id: dict) -> str:
        """Formats search results as markdown"""
        output = []

        for r in results:
            filing = filing_by_id.get(r['filing_id'], {})
            company = company_by_id.get(filing.get('company_id'), {})

            company_name = company.get('name', 'Unknown')
            symbols = ','.join(company.get('symbols', []))
            form = filing.get('form', '?')
            filing_date = filing.get('filing_date', '?')

            content = (r.get('content') or '').strip()

            output.append(
                f"**Filing #{r['filing_id']} - Page {r['page']}** | {company_name} ({symbols}) | {form} | {filing_date}\n\n"
                f"{content}\n\n---\n"
            )

        return "\n".join(output) if output else "No matching pages found."
