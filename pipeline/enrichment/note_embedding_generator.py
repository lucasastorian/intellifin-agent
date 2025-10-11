from typing import List
from pipeline.enrichment.base_embedding_generator import BaseEmbeddingGenerator


class NoteEmbeddingGenerator(BaseEmbeddingGenerator):
    """Generates embeddings for filing notes with contextual headers"""

    def _build_context_specific_header(self, **kwargs) -> str:
        """Build note-specific header with note title"""
        note_title = kwargs.get('note_title')
        if note_title:
            return f"\n## Note: {note_title}\n"
        return ""

    async def embed(self, note_title: str, note_content: str) -> List[dict]:
        """
        Generate embedding chunks for a note with contextual header

        Args:
            note_title: The title of the note
            note_content: The markdown content of the note

        Returns:
            List of chunk objects with embedding_text
        """
        return await super().embed(
            pages=[{"page": 0, "content": note_content}],
            note_title=note_title,
            fiscal_year=self.filing.get('fiscal_year'),
            fiscal_period=self.filing.get('fiscal_period')
        )
