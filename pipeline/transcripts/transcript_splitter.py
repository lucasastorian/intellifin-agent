from typing import List, Dict, Tuple
import re


class TranscriptSplitter:
    """
    Transcript segmentation with two failsafes:
    1. Splits long speaker sections (>512 tokens) by sentences
    2. Enforces max chunk size (~1024 tokens) even without operator splits

    First splits by Operator appearances (pre-split), then enforces token limits.
    """

    def __init__(self, max_chunk_tokens: int = 1024, max_section_tokens: int = 512):
        self.max_chunk_tokens = max_chunk_tokens
        self.max_section_tokens = max_section_tokens

    def split(self, sections: List[Dict]) -> List[List[Dict]]:
        """Splits the transcript with operator-based pre-split + token-based failsafes"""
        operator_segments = self._presplit_by_operator(sections=sections)

        final_chunks = []

        for segment in operator_segments:
            segment_chunks = self._chunk_segment(segment)
            final_chunks.extend(segment_chunks)

        return final_chunks

    @staticmethod
    def _presplit_by_operator(sections: List[Dict]) -> List[List[Dict]]:
        """Pre-splits transcript by Operator appearances."""
        segments = []
        current_segment = []

        for section in sections:
            if section['speaker'] == 'Operator' and current_segment:
                segments.append(current_segment)
                current_segment = [section]
            else:
                current_segment.append(section)

        if current_segment:
            segments.append(current_segment)

        return segments

    def _chunk_segment(self, segment: List[Dict]) -> List[List[Dict]]:
        """Chunk a segment with token limits"""
        chunks = []
        current_chunk = []
        current_tokens = 0

        for section in segment:
            section_tokens = self.num_tokens(section['content'])

            if section_tokens > self.max_section_tokens:
                current_chunk, current_tokens, chunks = self._process_large_section(
                    section, current_chunk, current_tokens, chunks
                )
            else:
                if current_tokens + section_tokens > self.max_chunk_tokens and current_chunk:
                    chunks.append(current_chunk)
                    current_chunk = [section]
                    current_tokens = section_tokens
                else:
                    current_chunk.append(section)
                    current_tokens += section_tokens

        if current_chunk:
            chunks.append(current_chunk)

        return chunks

    def _process_large_section(self, section: Dict, current_chunk: List[Dict],
                               current_tokens: int, chunks: List[List[Dict]]) -> Tuple[List[Dict], int, List[List[Dict]]]:
        """Process a section that exceeds max_section_tokens by splitting into sentences"""
        sentences = self._split_sentences(section['content'])
        accumulated_sentences = []
        accumulated_tokens = 0

        for sentence in sentences:
            sentence_tokens = self.num_tokens(sentence)

            if accumulated_tokens + sentence_tokens > self.max_section_tokens and accumulated_sentences:
                mini_section = {
                    'speaker': section['speaker'],
                    'content': ' '.join(accumulated_sentences)
                }
                mini_tokens = self.num_tokens(mini_section['content'])

                if current_tokens + mini_tokens > self.max_chunk_tokens and current_chunk:
                    chunks.append(current_chunk)
                    current_chunk = [mini_section]
                    current_tokens = mini_tokens
                else:
                    current_chunk.append(mini_section)
                    current_tokens += mini_tokens

                accumulated_sentences = [sentence]
                accumulated_tokens = sentence_tokens
            else:
                accumulated_sentences.append(sentence)
                accumulated_tokens += sentence_tokens

        if accumulated_sentences:
            mini_section = {
                'speaker': section['speaker'],
                'content': ' '.join(accumulated_sentences)
            }
            mini_tokens = self.num_tokens(mini_section['content'])

            if current_tokens + mini_tokens > self.max_chunk_tokens and current_chunk:
                chunks.append(current_chunk)
                current_chunk = [mini_section]
                current_tokens = mini_tokens
            else:
                current_chunk.append(mini_section)
                current_tokens += mini_tokens

        return current_chunk, current_tokens, chunks

    @staticmethod
    def _split_sentences(text: str) -> List[str]:
        """Split text into sentences using regex"""
        sentences = re.split(r'(?<=[.!?])\s+', text)
        return [s.strip() for s in sentences if s.strip()]

    @staticmethod
    def num_tokens(content: str) -> int:
        import tiktoken
        encoding = tiktoken.encoding_for_model("gpt-3.5-turbo")
        return len(encoding.encode(content))
