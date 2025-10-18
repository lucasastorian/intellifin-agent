import re
import voyageai
from abc import ABC
from typing import List, Optional

_voyage_client: Optional[voyageai.Client] = None
_voyage_model = "voyage-3.5-lite"


def _get_voyage_client() -> voyageai.Client:
    """Get or create shared Voyage client for token counting"""
    global _voyage_client
    if _voyage_client is None:
        import os
        api_key = os.environ.get("VOYAGE_API_KEY")
        if not api_key:
            raise ValueError("VOYAGE_API_KEY environment variable must be set")
        _voyage_client = voyageai.Client(api_key=api_key)
    return _voyage_client


def split_sentences(text: str) -> List[str]:
    """Simple regex-based sentence splitter"""
    # Split on .!? followed by whitespace and capital letter or end of string
    # Handles common abbreviations like Mr., Dr., Inc., etc.
    sentences = re.split(r'(?<=[.!?])\s+(?=[A-Z])', text)
    return [s.strip() for s in sentences if s.strip()]


class BaseBlock(ABC):
    block_type: str

    def __init__(self, content: str, page: int):
        self.content = content
        self.page = page

    @property
    def tokens(self) -> int:
        client = _get_voyage_client()
        return client.count_tokens([self.content], model=_voyage_model)


class Sentence:

    def __init__(self, content: str):
        self.content = content

    @property
    def tokens(self) -> int:
        client = _get_voyage_client()
        return client.count_tokens([self.content], model=_voyage_model)


class TextBlock(BaseBlock):
    block_type: str = 'Text'

    def __init__(self, content: str, page: int):
        super().__init__(content=content, page=page)

    @property
    def sentences(self) -> List[Sentence]:
        """Returns the text block sentences"""
        return [Sentence(content=content) for content in split_sentences(self.content)]

    @classmethod
    def from_sentences(cls, sentences: List[Sentence], page: int):
        content = " ".join([sentence.content for sentence in sentences])
        return cls(content=content, page=page)


class AudioParagraphBlock(BaseBlock):
    block_type: str = "Text"

    def __init__(self, content: str, page: int, paragraph_id: int, audio_start: float, audio_end: float):
        super().__init__(content=content, page=page)
        self.paragraph_id = paragraph_id
        self.audio_start = audio_start
        self.audio_end = audio_end

    @property
    def sentences(self) -> List[Sentence]:
        """Returns the text block sentences"""
        return [Sentence(content=content) for content in split_sentences(self.content)]

    def format(self) -> dict:
        """Formats the audio paragraphs"""
        return {"id": self.paragraph_id, "content": self.content, "start": self.audio_start, "end": self.audio_end}


class TableBlock(BaseBlock):
    block_type: str = 'Table'

    def __init__(self, content: str, page: int):
        super().__init__(content=content, page=page)
        self.content = self._to_minified_markdown()

    def _to_minified_markdown(self) -> str:
        """Returns the table in a Minified Markdown format"""
        lines = self.content.split('\n')
        cleaned_lines = []

        for i, line in enumerate(lines):
            if not line.strip():
                continue

            parts = line.split('|')
            cleaned_parts = [re.sub(r'\s+', ' ', part.strip()) for part in parts]
            cleaned_line = '|'.join(cleaned_parts)

            if i == 1:
                num_cols = len(cleaned_parts) - 1
                separator = '|' + '|'.join(['---'] * num_cols) + '|'
                cleaned_lines.append(separator)
            else:
                cleaned_lines.append(cleaned_line)

        return '\n'.join(cleaned_lines)


class HeaderBlock(BaseBlock):
    block_type = 'Header'

    def __init__(self, content: str, page: int):
        super().__init__(content=content, page=page)
