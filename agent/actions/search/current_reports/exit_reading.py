from pydantic import BaseModel, Field

from agent.actions.base_action import BaseAction
from agent.message import Message, Action
from agent.action_response import ActionResponse


class ExitReading(BaseModel):
    """Exit the reading loop and return to main conversation.

    Use this when you've finished reading all relevant filings and attachments
    and are ready to answer the user's question or take other actions.
    """
    thought: str = Field(
        description="Explain what you learned from reading and why you're ready to exit"
    )


class ExitReadingAction(BaseAction):
    name: str = 'ExitReading'
    schema = ExitReading

    async def call(self, action: Action) -> ActionResponse:
        """Exits the reading loop by not providing any follow-up actions"""
        try:
            args = ExitReading(**action.body)
        except Exception as e:
            self.log_start("ExitReading")
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

        self.log_start("ExitReading")
        self.log_done("Exited reading mode")

        return ActionResponse(
            message=Message(
                role="tool",
                status="completed",
                content="Exited reading mode. You can now use other actions or provide your answer.",
                action_id=action.id
            ),
            follow_up=None  # ← This breaks the forced reading loop
        )
