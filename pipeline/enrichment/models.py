from pydantic import BaseModel, Field


class AttachmentSummary(BaseModel):
    """Structured summary for a filing attachment"""

    title: str = Field(
        description="A concise, descriptive title for this document (e.g., 'Series D Preferred Stock Purchase Agreement', 'Q4 2024 Earnings Press Release')"
    )
    summary: str = Field(
        description="A 2-3 sentence summary covering: (1) document type and parties involved, (2) key transaction details including amounts, dates, and material terms, (3) business purpose or rationale. Be specific with numbers, dates, and entities."
    )


class FilingSummary(BaseModel):
    """Structured summary for an 8-K or 6-K filing"""

    title: str = Field(
        description="A concise event title describing what happened (e.g., 'KKR Completes $750M Series D Preferred Stock Offering', 'Apple Announces CFO Transition')"
    )
    summary: str = Field(
        description="A 2-3 sentence summary synthesizing the filing event, including key details from all items and attachments. Focus on material business events, financial impacts, and timing."
    )
