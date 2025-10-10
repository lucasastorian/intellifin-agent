from typing import List, Literal
from pydantic import BaseModel, Field, create_model

from database import Database
from agent.actions.base_action import BaseAction
from agent.message import Message, Action
from agent.action_response import ActionResponse, ContextRefinement


class CuratePressReleasesAction(BaseAction):

    def __init__(self, excerpts: List[dict], original_search_message_id: str, database: Database,
                 edgar_user_agent: str, start_year: int = 2017):
        super().__init__(database=database, edgar_user_agent=edgar_user_agent, start_year=start_year)
        self.excerpts = excerpts
        self.original_search_message_id = original_search_message_id
        self.name = 'CuratePressReleases'

    @property
    def schema(self) -> BaseModel:
        """Returns a dynamically generated schema based on available excerpts"""

        # Use actual database IDs as Literal constraint
        excerpt_ids = tuple(excerpt['id'] for excerpt in self.excerpts)

        fields = {
            'thought': (
                str,
                Field(description="Explain your curation decision and reasoning")
            ),
            'search_results_relevant': (
                Literal['relevant', 'not_relevant'],
                Field(
                    description="Decide whether any of the press release excerpts are relevant to your query. "
                                "If 'relevant', the context will be updated to show only selected excerpts. "
                                "If 'not_relevant', the entire search output will be replaced with your summary."
                )
            ),
            'selected_excerpt_ids': (
                List[Literal[excerpt_ids]],
                Field(
                    default=[],
                    description=f"Select excerpt IDs of relevant excerpts (IDs shown in search results). "
                                f"Empty list if search_results_relevant='not_relevant'."
                )
            ),
            'summary': (
                str,
                Field(
                    description="Brief summary of your findings. Used to replace full search output if not_relevant, "
                                "or as a summary note if relevant excerpts are selected."
                )
            )
        }

        return create_model(
            'CuratePressReleases',
            **fields,
            __doc__=f"""Curate the {len(self.excerpts)} press release search results by selecting relevant excerpts or summarizing findings.

This action allows you to refine your context window by:
1. Selecting only the relevant excerpts (if any are useful)
2. Replacing the entire search output with a concise summary (if results are not useful)

Your selection will modify the previous search message to reduce token usage and improve focus."""
        )

    async def call(self, action: Action) -> ActionResponse:
        """Curate press release search results by selecting relevant excerpts"""
        try:
            args = self.schema(**action.body)
        except Exception as e:
            self.log_start("CuratePressReleases")
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

        self.log_start("CuratePressReleases", params=f"relevance={args.search_results_relevant}")

        selected_ids = getattr(args, 'selected_excerpt_ids', [])
        summary = args.summary

        if args.search_results_relevant == 'relevant' and selected_ids:
            # Map selected IDs to excerpts
            id_to_excerpt = {excerpt['id']: excerpt for excerpt in self.excerpts}
            selected_excerpts = [id_to_excerpt[excerpt_id] for excerpt_id in selected_ids if excerpt_id in id_to_excerpt]

            from agent.actions.search.press_releases.search_press_releases import SearchPressReleasesAction

            replacement_content = SearchPressReleasesAction.curate(
                excerpts=selected_excerpts,
                summary=summary
            )

            confirmation = f"Context refined to {len(selected_excerpts)} relevant press release excerpt(s)."
        else:
            replacement_content = "Press release search results not relevant (see curation summary)."
            confirmation = "Context refined (press release results not relevant)."

        self.log_done(f"Curated to {len(selected_ids) if selected_ids else 0} excerpts")

        return ActionResponse(
            message=Message(
                role="tool",
                status="completed",
                content=confirmation,
                action_id=action.id
            ),
            context_refinement=ContextRefinement(
                message_id=self.original_search_message_id,
                refined_content=replacement_content
            )
        )
