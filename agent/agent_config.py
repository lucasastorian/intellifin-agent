from enum import Enum
from typing import List
from dataclasses import dataclass


class AgentMode(Enum):
    BASIC = "basic"  # Just model, no tools
    WEB_SEARCH = "web_search"  # Model + web search
    WEB_CODE = "web_code"  # Model + web search + code execution
    FULL = "full"  # Model + web search + all tools + code
    FULL_NO_WEB = "full_no_web"  # Model + all tools + code, no web search


@dataclass
class AgentConfig:
    enable_web_search: bool
    enabled_actions: List[str]  # List of action class names to enable


def get_agent_config(mode: AgentMode) -> AgentConfig:
    """Returns agent configuration based on mode"""

    if mode == AgentMode.BASIC:
        return AgentConfig(
            enable_web_search=False,
            enabled_actions=[]
        )

    elif mode == AgentMode.WEB_SEARCH:
        return AgentConfig(
            enable_web_search=True,
            enabled_actions=[]
        )

    elif mode == AgentMode.WEB_CODE:
        return AgentConfig(
            enable_web_search=True,
            enabled_actions=["PythonExecAction"]
        )

    elif mode == AgentMode.FULL:
        return AgentConfig(
            enable_web_search=True,
            enabled_actions=[
                "PlanAction",
                "ListCompaniesAction",
                "ListFilingsAction",
                "ReadFilingAction",
                "SearchFilingSectionsAction",
                "SearchPressReleasesAction",
                "SearchCurrentReportsAction",
                "SearchFilingNotesActionNew",
                "ViewFinancialStatementsAction",
                "PythonExecAction"
            ]
        )

    elif mode == AgentMode.FULL_NO_WEB:
        return AgentConfig(
            enable_web_search=False,
            enabled_actions=[
                "PlanAction",
                "ListCompaniesAction",
                "ListFilingsAction",
                "ReadFilingAction",
                "SearchFilingSectionsAction",
                "SearchPressReleasesAction",
                "SearchCurrentReportsAction",
                "SearchFilingNotesActionNew",
                "ViewFinancialStatementsAction",
                "PythonExecAction"
            ]
        )
