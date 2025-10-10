import uuid
from typing import List, Literal, Dict, Optional
from pydantic import BaseModel, Field, create_model

from database import Database
from agent.actions.base_action import BaseAction
from agent.message import Message, Action
from agent.action_response import ActionResponse, ActionFollowUp, ContextRefinement


class CurateReadPagesAction(BaseAction):
    name: str = 'CurateReadPages'

    def __init__(self, pages_read: List[Dict], source_type: Literal['filing', 'attachment'],
                 source_id: int, read_message_id: str, siblings: tuple,
                 database: Database, edgar_user_agent: str, start_year: int = 2017):
        super().__init__(database=database, edgar_user_agent=edgar_user_agent, start_year=start_year)
        self.pages_read = pages_read
        self.source_type = source_type
        self.source_id = source_id
        self.read_message_id = read_message_id
        self.siblings = siblings

    @property
    def schema(self) -> BaseModel:
        """Returns a dynamically generated schema with page numbers as Literal constraint"""

        page_numbers = tuple(p['page'] for p in self.pages_read)

        fields = {
            'thought': (
                str,
                Field(description="Explain which pages are relevant and why, or why you're summarizing")
            ),
            'curation_mode': (
                Literal['select_pages', 'summarize'],
                Field(
                    description="'select_pages' to keep only specific relevant pages, "
                                "'summarize' to replace all pages with a concise summary of findings"
                )
            ),
            'selected_pages': (
                List[Literal[page_numbers]],
                Field(
                    default=[],
                    description=f"Page numbers to keep (only if curation_mode='select_pages'). "
                                f"Available pages: {list(page_numbers)}"
                )
            ),
            'summary': (
                str,
                Field(
                    default="",
                    description="Concise summary of key findings (only if curation_mode='summarize')"
                )
            )
        }

        source_desc = f"{'filing' if self.source_type == 'filing' else 'attachment'} #{self.source_id}"

        return create_model(
            'CurateReadPages',
            **fields,
            __doc__=f"""Curate the pages you just read from {source_desc}.

You just read {len(self.pages_read)} page(s). To maintain clean context and improve performance:

**select_pages mode:**
- Select ONLY the pages that contain relevant information for your task
- Drop pages that don't contribute to answering the user's question
- Use this when you found specific facts/data on certain pages

**summarize mode:**
- Replace all pages with a concise summary of what you learned
- Use this when you need the general findings but not the full text
- Particularly useful after exploratory reading

This curation reduces context size and improves accuracy for subsequent analysis."""
        )

    async def call(self, action: Action) -> ActionResponse:
        """Curates read pages by selecting relevant ones or summarizing"""
        try:
            args = self.schema(**action.body)
        except Exception as e:
            self.log_start("CurateReadPages")
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

        mode = args.curation_mode
        self.log_start("CurateReadPages", params=f"mode={mode}", thought=args.thought)

        if mode == 'select_pages':
            selected_pages = args.selected_pages
            if not selected_pages:
                self.log_error("No pages selected in select_pages mode")
                return ActionResponse(
                    message=Message(
                        role="tool",
                        status="completed",
                        content="Error: select_pages mode requires at least one page to be selected",
                        error=True,
                        action_id=action.id
                    )
                )

            # Filter to selected pages
            pages_to_keep = [p for p in self.pages_read if p['page'] in selected_pages]
            refined_content = self._format_selected_pages(pages_to_keep)
            summary = f"Refined to {len(selected_pages)} relevant page(s)"

        elif mode == 'summarize':
            summary_text = args.summary
            if not summary_text:
                self.log_error("No summary provided in summarize mode")
                return ActionResponse(
                    message=Message(
                        role="tool",
                        status="completed",
                        content="Error: summarize mode requires a summary",
                        error=True,
                        action_id=action.id
                    )
                )

            refined_content = f"**Summary of pages read:** {summary_text}"
            summary = "Replaced pages with summary"

        else:
            # Shouldn't happen due to Literal validation
            refined_content = None
            summary = "No curation applied"

        self.log_done(summary)

        return ActionResponse(
            message=Message(
                role="tool",
                status="completed",
                content=summary,
                action_id=action.id
            ),
            context_refinement=ContextRefinement(
                message_id=self.read_message_id,
                refined_content=refined_content
            ),
            follow_up=ActionFollowUp(
                actions=list(self.siblings),
                force=True
            )
        )

    def _format_selected_pages(self, pages: List[Dict]) -> str:
        """Format selected pages back into readable format"""
        parts = []
        for p in pages:
            page_num = p['page']
            content = p['content'].strip()
            parts.append(f"\n---\n**Page {page_num}**\n\n{content}")

        source_type_display = "Filing" if self.source_type == "filing" else "Attachment"
        header = f"**[Curated Pages from {source_type_display} #{self.source_id}]**\n"

        return header + "".join(parts) if parts else "_No content retained._"
