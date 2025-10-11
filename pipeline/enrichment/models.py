from pydantic import BaseModel


class AttachmentSummary(BaseModel):
    """Structured summary for a filing attachment"""
    title: str
    summary: str


class FilingSummary(BaseModel):
    """Structured summary for an 8-K or 6-K filing"""
    title: str
    summary: str
