from typing import List, Literal, Dict
from pydantic import BaseModel, Field, create_model

from database import Database
from agent.actions.base_action import BaseAction
from agent.message import Message, Action
from agent.action_response import ActionResponse


class ReadFullFilingNoteAction(BaseAction):
    name: str = 'ReadFullFilingNote'

    def __init__(self, valid_note_ids: List[int], database: Database,
                 edgar_user_agent: str, start_year: int = 2017):
        super().__init__(database=database, edgar_user_agent=edgar_user_agent, start_year=start_year)
        self.valid_note_ids = valid_note_ids

    @property
    def schema(self) -> BaseModel:
        """Returns a dynamically generated schema with valid note IDs as Literal constraint"""

        note_ids = tuple(self.valid_note_ids)

        fields = {
            'thought': (
                str,
                Field(description="Explain why you need to read the full note (e.g., excerpt was truncated, need more context)")
            ),
            'filing_note_id': (
                Literal[note_ids],
                Field(description=f"The filing note ID to read in full. Valid IDs: {list(note_ids)}")
            )
        }

        return create_model(
            'ReadFullFilingNote',
            **fields,
            __doc__=f"""Read the complete content of a financial statement note.

Use this when:
- An excerpt was truncated and you need the full context
- You need to see the complete note to understand the full accounting treatment
- Multiple excerpts from the same note suggest the full note is relevant

Available note IDs are from your curated search results."""
        )

    async def call(self, action: Action) -> ActionResponse:
        """Reads the full content of a filing note"""
        try:
            args = self.schema(**action.body)
        except Exception as e:
            self.log_start("ReadFullFilingNote")
            self.log_error(f"Validation failed: {e}")
            return ActionResponse(
                message=Message(
                    role="tool",
                    status="completed",
                    content=str(e),
                    error=True,
                    action_id=action.id
                )
            )

        self.log_start("ReadFullFilingNote", f"Note #{args.filing_note_id}", thought=args.thought)

        # Get full note using company_filing_notes view
        result = (
            self.database
            .table("company_filing_notes")
            .select("*")
            .eq("id", args.filing_note_id)
            .limit(1)
            .execute()
        )

        if not result.data:
            self.log_error(f"Note #{args.filing_note_id} not found")
            return ActionResponse(
                message=Message(
                    role="tool",
                    status="completed",
                    content=f"Filing note #{args.filing_note_id} not found in database.",
                    error=True,
                    action_id=action.id
                )
            )

        data = result.data[0]

        # Format output
        header = self._format_header(data)
        content = data['content'].strip()

        output = header + "\n\n" + content

        # Truncate if too large
        truncated = False
        if len(output) > 200_000:
            output = header + "\n\n" + content[:200_000] + "\n\n[truncated]"
            truncated = True

        summary = f"Retrieved full note: {data['company_name']} - {data['title']}"
        if truncated:
            summary += " (truncated)"

        self.log_done(summary)

        return ActionResponse(
            message=Message(
                role="tool",
                status="completed",
                content=output,
                action_id=action.id
            )
            # No follow-up - simple read action
        )

    @staticmethod
    def _format_header(data: Dict) -> str:
        """Format note header with metadata"""
        symbols = ",".join(data.get("company_symbols") or [])
        exchanges = ",".join(data.get("company_exchanges") or [])
        fy = data.get("fiscal_year") or "—"
        fp = data.get("fiscal_period") or "—"

        return (
            f"### {data.get('company_name','Unknown')} ({symbols})\n"
            f"**Form:** {data.get('form')} | **Accession:** {data.get('accession_number')}\n"
            f"**Note:** {data['title']}\n"
            f"**Filename:** {data['filename']}\n"
            f"**Report Date:** {data.get('report_date') or '—'} | **Filing Date:** {data.get('filing_date')}\n"
            f"**Fiscal:** {fp} {fy} | **Exchanges:** {exchanges}\n"
            f"**Source:** Full note content"
        )
