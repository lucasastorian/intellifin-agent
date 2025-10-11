from typing import List, Optional, TYPE_CHECKING
from dataclasses import dataclass

from agent.message import Message

if TYPE_CHECKING:
    from agent.actions.base_action import BaseAction


@dataclass
class ContextRefinement:
    """Represents a retroactive context refinement operation"""
    message_id: str      # ID of message to refine in history
    refined_content: str # Refined content for that message


@dataclass
class ActionFollowUp:
    actions: List['BaseAction']
    force: bool


@dataclass
class ActionSummary:
    """Compact summary of action execution for UI display"""
    headline: str  # One-line result (e.g., "Found 5 companies")
    details: Optional[dict] = None  # Key metrics (e.g., {"count": 5, "tickers": ["X", "Y"]})


@dataclass
class ActionResponse:
    message: Message
    summary: Optional[ActionSummary] = None
    follow_up: Optional[ActionFollowUp] = None
    context_refinement: Optional[ContextRefinement] = None
