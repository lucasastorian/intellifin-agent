from typing import List
from pipeline.enrichment.base_embedding_generator import BaseEmbeddingGenerator


class AttachmentEmbeddingGenerator(BaseEmbeddingGenerator):
    """Generates embeddings for filing attachments with contextual headers"""

    def _build_context_specific_header(self, **kwargs) -> str:
        """Build attachment-specific header with exhibit info"""
        attachment_type = kwargs.get('attachment_type')
        exhibit_number = kwargs.get('exhibit_number')
        description = kwargs.get('description')

        if attachment_type and exhibit_number:
            attachment_display = attachment_type.replace('_', ' ').title()
            attachment_parts = [f"Exhibit {exhibit_number}", attachment_display]
            if description:
                attachment_parts.append(description)
            return f"\n## {' - '.join(attachment_parts)}\n"
        return ""

    async def embed(self, pages: List[dict], attachment_type: str, exhibit_number: str, description: str = None) -> List[dict]:
        """
        Generate embedding chunks for an attachment with contextual header

        Args:
            pages: List of page dicts with 'page' and 'content' keys
            attachment_type: Type of attachment (e.g., 'press_release', 'material_contract')
            exhibit_number: Exhibit number (e.g., '99.1', '10.2')
            description: Optional description of the attachment

        Returns:
            List of chunk objects with embedding_text
        """
        return await super().embed(
            pages=pages,
            attachment_type=attachment_type,
            exhibit_number=exhibit_number,
            description=description
        )
