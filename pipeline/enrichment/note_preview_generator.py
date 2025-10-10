from typing import Dict
from pydantic import BaseModel, Field
from pipeline.enrichment.base_client import BaseLLMClient


class NotePreview(BaseModel):
    """One-sentence preview for a filing note"""
    preview: str = Field(
        description="A single concise sentence (15-25 words) describing what this note covers, focusing on key topics, breakdowns, and material information"
    )


class NotePreviewGenerator:
    """Generates one-sentence previews for filing notes"""

    SYSTEM_PROMPT = """You are a financial analyst summarizing SEC filing notes.

Generate a single concise sentence describing what the note covers.

Guidelines:
- One sentence only (15-25 words)
- Focus on key topics, data breakdowns, and material information
- Be specific about what's disclosed (e.g., "Disaggregates revenue by product, geography, and customer type")
- Skip generic phrases like "This note discusses..." - just state what it covers"""

    def __init__(self, client: BaseLLMClient):
        self.client = client

    async def generate(self, note_title: str, note_content: str) -> str:
        """
        Generate one-sentence preview for a note

        Args:
            note_title: Original note title from filing (e.g., "Revenue")
            note_content: First ~2000 chars of note markdown content

        Returns:
            One-sentence preview string
        """
        # Limit content to first 2000 chars for efficiency
        content_preview = note_content[:2000] if len(note_content) > 2000 else note_content

        user_message = f"Note Title: {note_title}\n\nContent:\n{content_preview}"

        result = await self.client.parse(
            system_prompt=self.SYSTEM_PROMPT,
            user_message=user_message,
            response_model=NotePreview,
            reasoning_effort="none"
        )

        return result.preview
