import logging
from typing import Union, Tuple, List, Dict

from pipeline.chunker.markdown_chunk import MarkdownChunk
from pipeline.chunker.markdown_blocks import BaseBlock, TextBlock, TableBlock, HeaderBlock

logger = logging.getLogger(__name__)


class MarkdownChunker:
    """Splits markdown content into chunks"""

    def __init__(self, chunk_size: int = 512, chunk_overlap: int = 128):
        """
        Initialize chunker with parameters and optional metadata
        
        Args:
            chunk_size: Maximum token size for each chunk
            chunk_overlap: Number of tokens to overlap between chunks
            metadata: Optional metadata to add to all chunks
        """
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    def split(self, pages: List[Dict[str, Union[int, str]]]):
        """Split the pages into chunks"""
        blocks = self._split_into_blocks(pages=pages)
        return self._chunk_blocks(blocks=blocks)

    @staticmethod
    def _split_into_blocks(pages: List[Dict[str, Union[int, str]]]):
        """Splits the page into blocks"""
        blocks = []
        table_content = ""

        for page in pages:
            for line in page['content'].split('\n'):
                if table_content and "|" not in line:
                    block = TableBlock(content=table_content, page=page['page'])
                    blocks.append(block)
                    table_content = ""

                if line.startswith("#"):
                    block = HeaderBlock(content=line, page=page['page'])
                    blocks.append(block)

                elif "|" in line:
                    table_content += f"{line}\n"

                else:
                    block = TextBlock(content=line, page=page['page'])
                    blocks.append(block)

        return blocks

    def _chunk_blocks(self, blocks: List[BaseBlock]):
        """Converts the blocks to chunks"""
        chunks = []
        chunk_blocks = []
        num_tokens = 0

        for block in blocks:
            if block.block_type == 'Text':
                chunk_blocks, num_tokens, chunks = self._process_text_block(
                    block, chunk_blocks, num_tokens, chunks
                )

            else:
                chunk_blocks, num_tokens, chunks = self._process_header_table_block(
                    block, chunk_blocks, num_tokens, chunks
                )

        if chunk_blocks:
            chunks.append(MarkdownChunk(blocks=chunk_blocks))

        return chunks

    def _process_text_block(self, block: TextBlock, chunk_blocks: List[BaseBlock], num_tokens: int,
                            chunks: List[MarkdownChunk]):
        """Process a text block by breaking it into sentences if needed"""
        sentences = []

        for sentence in block.sentences:
            if num_tokens + sentence.tokens > self.chunk_size:
                if sentences:
                    new_block = TextBlock.from_sentences(sentences=sentences, page=block.page)
                    chunk_blocks.append(new_block)

                chunks, chunk_blocks, num_tokens = self._create_chunk(chunks=chunks, blocks=chunk_blocks)

                sentences = [sentence]
                num_tokens += sentence.tokens

            else:
                sentences.append(sentence)
                num_tokens += sentence.tokens

        if sentences:
            new_block = TextBlock.from_sentences(sentences=sentences, page=block.page)
            chunk_blocks.append(new_block)
            num_tokens += new_block.tokens

        return chunk_blocks, num_tokens, chunks

    def _process_header_table_block(self, block: BaseBlock, chunk_blocks: List[BaseBlock], num_tokens: int,
                                    chunks: List[MarkdownChunk]):
        """Process a header or table block"""
        if not chunk_blocks:
            chunk_blocks.append(block)
            num_tokens += block.tokens
            return chunk_blocks, num_tokens, chunks

        if num_tokens + block.tokens > self.chunk_size:
            chunks, chunk_blocks, num_tokens = self._create_chunk(chunks=chunks, blocks=chunk_blocks)
            chunk_blocks.append(block)
            num_tokens += block.tokens

        else:
            chunk_blocks.append(block)
            num_tokens += block.tokens

        return chunk_blocks, num_tokens, chunks

    def _create_chunk(self, chunks: List[MarkdownChunk], blocks: List[BaseBlock]) -> Tuple[
        List[MarkdownChunk], List[BaseBlock], int]:
        """Creates a chunk, and return a new list of blocks that """
        chunks.append(MarkdownChunk(blocks=blocks, metadata=self.metadata))

        if not self.chunk_overlap:
            return chunks, [], 0

        overlap_tokens = 0
        overlap_blocks = []

        for block in reversed(blocks):
            if block.block_type == "Text":
                sentences = []

                for sentence in reversed(block.sentences):

                    if overlap_tokens + sentence.tokens > self.chunk_overlap:
                        text_block = TextBlock.from_sentences(sentences=sentences, page=block.page)
                        overlap_blocks.insert(0, text_block)
                        return chunks, overlap_blocks, overlap_tokens

                    else:
                        sentences.insert(0, sentence)
                        overlap_tokens += sentence.tokens

            else:
                if overlap_tokens + block.tokens > self.chunk_overlap:
                    return chunks, overlap_blocks, overlap_tokens

                else:
                    overlap_blocks.insert(0, block)
                    overlap_tokens += block.tokens

        return chunks, [], 0
