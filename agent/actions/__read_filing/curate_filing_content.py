import uuid
from typing import List, Literal, Optional, Tuple, Dict
from pydantic import BaseModel, Field, create_model

from agent.message import Action, Message
from agent.action_response import ActionResponse, ActionFollowUp, ContextRefinement
from agent.actions.base_action import BaseAction


class CurateFilingContentAction(BaseAction):
    name: str = 'CurateFilingContent'

    def __init__(self, document_type: str, pages_read: List[Dict], read_message_id: str,
                 siblings: Optional[Tuple] = None, database=None, edgar_user_agent: str = None, start_year: int = 2017):
        super().__init__(database=database, edgar_user_agent=edgar_user_agent, start_year=start_year)
        self.document_type = document_type
        self.pages_read = pages_read  # List of {page: int, content: str} for page-based docs, empty for notes
        self.read_message_id = read_message_id
        self.siblings = siblings

    @property
    def schema(self) -> BaseModel:
        """Returns a dynamically generated schema based on document type"""

        if self.document_type in ['primary_document', 'attachment']:
            # Page-based curation: select_pages or summarize
            page_numbers = tuple(p['page'] for p in self.pages_read)

            fields = {
                'thought': (
                    str,
                    Field(description="Explain your curation decision")
                ),
                'curation_mode': (
                    Literal['select_pages', 'summarize'],
                    Field(description="'select_pages' to keep only specific relevant pages, 'summarize' to replace all pages with a concise summary")
                ),
                'selected_pages': (
                    List[Literal[page_numbers]],
                    Field(default=[], description=f"Page numbers to keep (only used when curation_mode='select_pages'). Available pages: {list(page_numbers)}")
                ),
                'summary': (
                    str,
                    Field(default="", description="Concise summary of the content (only used when curation_mode='summarize')")
                )
            }

            doc_type_display = "attachment" if self.document_type == "attachment" else "primary document"
            docstring = f"""Curate the {doc_type_display} content you just read.

**Purpose:** Reduce context bloat by keeping only relevant information.

**Modes:**
- **select_pages**: Keep only specific pages that are relevant to your analysis
- **summarize**: Replace all content with a concise summary of key findings

Choose 'select_pages' when specific details matter. Choose 'summarize' when you just need high-level takeaways."""

        else:  # note
            # Note-based curation: keep or summarize
            fields = {
                'thought': (
                    str,
                    Field(description="Explain your curation decision")
                ),
                'curation_mode': (
                    Literal['keep', 'summarize'],
                    Field(description="'keep' to retain full note content, 'summarize' to replace with a concise summary")
                ),
                'summary': (
                    str,
                    Field(default="", description="Concise summary of the note (only used when curation_mode='summarize')")
                )
            }

            docstring = """Curate the financial statement note you just read.

**Purpose:** Reduce context bloat by managing note content.

**Modes:**
- **keep**: Keep the full note content as-is
- **summarize**: Replace with a concise summary of key accounting policies/disclosures

Choose 'keep' when you need the full details. Choose 'summarize' for high-level understanding."""

        return create_model(
            'CurateFilingContent',
            **fields,
            __doc__=docstring
        )

    async def call(self, action: Action) -> ActionResponse:
        """Curates filing content and applies context refinement"""
        try:
            args = self.schema(**action.body)
        except Exception as e:
            self.log_start("CurateFilingContent")
            self.log_error(f"Validation failed: {e}")
            return ActionResponse(
                message=Message(role="tool", status="completed", content=str(e), error=True, action_id=action.id)
            )

        self.log_start("CurateFilingContent", params=f"mode={args.curation_mode}", thought=args.thought)

        # Build refined content based on mode
        if self.document_type in ['primary_document', 'attachment']:
            if args.curation_mode == 'select_pages':
                refined_content = self._build_selected_pages_content(args.selected_pages)
                summary_msg = f"Kept {len(args.selected_pages)} of {len(self.pages_read)} pages"
            else:  # summarize
                refined_content = f"**[Summarized Content]**\n\n{args.summary}\n\n*Note: Original content replaced with summary by curation step to reduce context size.*"
                summary_msg = f"Summarized {len(self.pages_read)} pages"
        else:  # note
            if args.curation_mode == 'keep':
                refined_content = None  # Keep original content
                summary_msg = "Kept full note content"
            else:  # summarize
                refined_content = f"**[Summarized Note]**\n\n{args.summary}\n\n*Note: Original content replaced with summary by curation step to reduce context size.*"
                summary_msg = "Summarized note content"

        self.log_done(summary_msg, content=content)

        response = ActionResponse(
            message=Message(
                role="tool",
                status="completed",
                content=f"Curation complete: {summary_msg}",
                action_id=action.id
            ),
            follow_up=ActionFollowUp(
                actions=list(self.siblings),
                force=False
            )
        )

        if refined_content is not None:
            response.context_refinement = ContextRefinement(
                message_id=self.read_message_id,
                refined_content=refined_content
            )

        return response

    def _build_selected_pages_content(self, selected_pages: List[int]) -> str:
        """Build content string from selected pages"""
        pages_dict = {p['page']: p['content'] for p in self.pages_read}

        selected_content = []
        for page_num in sorted(selected_pages):
            if page_num in pages_dict:
                selected_content.append(f"**Page {page_num}**\n{pages_dict[page_num]}")

        if not selected_content:
            return "**[No pages selected]**"

        return "\n\n---\n\n".join(selected_content)
