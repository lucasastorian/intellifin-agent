from pydantic import BaseModel, Field

from agent.message import Action, Message
from agent.action_response import ActionResponse
from agent.actions.base_action import BaseAction


class ExitFilingReading(BaseModel):
    """Exit the filing exploration loop and return to normal agent flow.

    Use this when you've finished reading the filing content you need and are ready to:
    - Answer the user's question
    - Perform analysis or calculations
    - Search for other information
    - Take any other action outside the current filing
    """
    thought: str = Field(description="Explain why you're done reading this filing (e.g., 'Found all necessary information', 'Need to search for different data')")


class ExitFilingReadingAction(BaseAction):
    name: str = 'ExitFilingReading'
    schema = ExitFilingReading

    async def call(self, action: Action) -> ActionResponse:
        """Exits the filing reading loop"""
        try:
            args = ExitFilingReading(**action.body)
        except Exception as e:
            self.log_start("ExitFilingReading")
            self.log_error(f"Validation failed: {e}")
            return ActionResponse(
                message=Message(role="tool", status="completed", content=str(e), error=True, action_id=action.id)
            )

        self.log_start("ExitFilingReading", thought=args.thought)
        self.log_done("Exited filing reading loop")

        return ActionResponse(
            message=Message(
                role="tool",
                status="completed",
                content="Exited filing reading. You can now use other actions or respond to the user.",
                action_id=action.id
            )
            # No follow-up = terminal action
        )
