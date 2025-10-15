from typing import List
from pydantic import BaseModel, Field, ValidationError

from agent.actions.base_action import BaseAction
from agent.message import Action, Message
from agent.action_response import ActionResponse


class Plan(BaseModel):
    """Create a plan for answering the user's question. Use this for complex questions requiring multiple steps.

    Typical workflow for financial analysis:
    1. **Identify metrics needed** - What specific data points answer the question?
    2. **Search & retrieve** - Use SearchFilingSections, SearchPressReleases, SearchFilingNotes, ViewFinancialStatements
    3. **Calculate & synthesize** - Use PythonExec for calculations, combine data from multiple sources

    This is optional - only use when the question is complex enough to benefit from explicit planning.
    """

    objective: str = Field(
        description="One sentence summarizing what you're trying to determine or calculate"
    )

    steps: List[str] = Field(
        description="Ordered list of specific steps to execute. Be concrete about what data/metrics to retrieve and from where."
    )

    potential_complications: List[str] = Field(
        default_factory=list,
        description="Optional list of potential issues (e.g., fiscal year misalignment, data unavailability, calculation challenges)"
    )


class PlanAction(BaseAction):
    """Creates a plan for answering complex financial questions"""

    name: str = 'Plan'
    schema = Plan

    async def call(self, action: Action):
        """Creates and displays a plan"""
        try:
            args = Plan(**action.body)
        except ValidationError as e:
            self.log_start("Plan")
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

        self.log_start("Plan", f"Created plan with {len(args.steps)} steps")

        # Format as markdown
        content_parts = [
            f"## Plan\n",
            f"**Objective**: {args.objective}\n",
            f"\n**Steps**:"
        ]

        for i, step in enumerate(args.steps, 1):
            content_parts.append(f"\n{i}. {step}")

        if args.potential_complications:
            content_parts.append("\n\n**Potential Complications**:")
            for comp in args.potential_complications:
                content_parts.append(f"\n- {comp}")

        content = "".join(content_parts)

        self.log_done(f"Plan created", content=content)

        return ActionResponse(
            message=Message(
                role="tool",
                status="completed",
                content=content,
                action_id=action.id
            )
        )
