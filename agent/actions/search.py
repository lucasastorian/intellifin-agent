from pydantic import BaseModel, Field
from typing import List, Literal, Optional
from datetime import date, timedelta


def default_start_date() -> str:
    """Default to 1 year ago"""
    return (date.today() - timedelta(days=365)).strftime('%Y-%m-%d')


def default_end_date() -> str:
    """Default to today"""
    return date.today().strftime('%Y-%m-%d')


DocumentType = Literal[
    "annual_report",  # 10-K
    "quarterly_report",  # 10-Q
    "current_report",  # 8-K
    "earnings_transcript"
]


class SemanticSearch(BaseModel):
    """Search a single company's SEC filings and earnings transcripts via a natural language query

    * Be specific about format: "table showing...", "discussion of...", "accounting policy for..."
    * Include temporal context: "Q1 2024 guidance", "fiscal 2023 depreciation", "forward-looking capex"
    * Specify what you expect: "percentage breakdown", "dollar amounts", "risk factors related to..."
    * Think about document type: earnings calls for guidance, 8-Ks for events, 10-Ks for policies
    """
    symbol: str = Field(
        description="Stock ticker symbol (e.g., 'AAPL', 'MSFT')"
    )
    query: str = Field(
        description=(
            "Describe exactly what you're looking for. "
            "Examples: 'forward capital expenditure guidance for 2025', "
            "'accounting policy for revenue recognition', "
            "'risk factors related to supply chain disruption', "
            "'table of R&D expenses by quarter'"
        ),
        min_length=5
    )

    document_types: List[DocumentType] = Field(
        description=(
            "Filter by document type."
            "earnings_transcript: guidance, Q&A | quarterly_report: 10-Q interim results | "
            "annual_report: 10-K comprehensive | current_report: 8-K material events"
        )
    )

    start_date: str = Field(
        description="Start date (YYYY-MM-DD)."
    )

    end_date: str = Field(
        default_factory=default_end_date,
        description="End date (YYYY-MM-DD). Defaults to today."
    )

    limit: Literal[5, 10, 20] = Field(
        default=5,
        description="Number of results to return. Start with 5 for most queries."
    )

    class Config:
        json_schema_extra = {
            "examples": [
                {
                    "symbol": "MSFT",
                    "query": "forward capital expenditure guidance AI infrastructure spending 2025",
                    "document_types": ["earnings_transcript"],
                    "start_date": "2024-01-01",
                    "limit": 5
                },
                {
                    "symbol": "NVDA",
                    "query": "risk factors related to export controls China revenue restrictions",
                    "document_types": ["annual_report", "quarterly_report"],
                    "start_date": "2024-01-01",
                    "limit": 10
                },
                {
                    "symbol": "META",
                    "query": "accounting policy for capitalizing internal-use software development costs",
                    "document_types": ["annual_report"],
                    "start_date": "2023-01-01",
                    "limit": 5
                },
                {
                    "symbol": "BA",
                    "query": "material production halt announcement or delivery delays",
                    "document_types": ["current_report"],
                    "start_date": "2024-01-01",
                    "limit": 5
                }
            ]
        }
        extra = "forbid"


