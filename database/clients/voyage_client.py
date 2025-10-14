import os
import logging
import voyageai
from typing import List, Literal, Optional, Dict
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from ..utils.rate_limiters.token_limiter import TokenRateLimiter
from ..utils.rate_limiters.request_limiter import RequestRateLimiter
from ..utils.voyage_limits import VoyageLimits
from ..embeddings.embedding_cache import EmbeddingCache


class VoyageClient:
    max_batch_size: int = 1000

    def __init__(self, model: str = "voyage-3.5-lite", dimensions: int = 512, cache: bool = True,
                 rerank_model: str = "rerank-2.5"):
        self.provider = "voyageai"
        self.model = model
        self.dimensions = dimensions
        self.rerank_model = rerank_model

        api_key = os.environ.get("VOYAGE_API_KEY")
        assert api_key is not None, "VOYAGE_API_KEY environment variable must be set. Get your API key from https://www.voyageai.com/"

        self.limits = VoyageLimits(model)

        self.max_tokens_per_request = self.limits.max_tokens_per_request
        self.max_tokens_per_minute = self.limits.max_tokens_per_minute
        self.max_requests_per_minute = self.limits.max_requests_per_minute

        self.token_rate_limiter = TokenRateLimiter(max_tokens=self.max_tokens_per_minute, period=60)
        self.request_rate_limiter = RequestRateLimiter(max_requests=self.max_requests_per_minute, period=60)

        self.client = voyageai.AsyncClient(api_key=api_key)

        self.cache = EmbeddingCache(model=model, dimensions=dimensions) if cache else None

    async def query_vector(self, text: str) -> List[float]:
        """Generates a single query vector"""
        result = await self._embed(texts=[text], input_type="query")
        return result[0]

    async def embed(self, texts: List[str]) -> List[List[float]]:
        """Generates a flat list of embeddings for all texts."""
        if not self.cache:
            all_embeddings = []
            for batch in await self._batch_texts(texts=texts):
                batch_embeddings = await self._embed(batch, input_type="document")
                all_embeddings.extend(batch_embeddings)
            return all_embeddings

        cached = self.cache.get_many(texts)

        uncached_texts = []
        uncached_indices = []
        for i, (text, cached_emb) in enumerate(zip(texts, cached)):
            if cached_emb is None:
                uncached_texts.append(text)
                uncached_indices.append(i)

        if uncached_texts:
            logging.debug(f"Cache miss: {len(uncached_texts)}/{len(texts)} texts")
            new_embeddings = []
            for batch in await self._batch_texts(texts=uncached_texts):
                batch_embeddings = await self._embed(batch, input_type="document")
                new_embeddings.extend(batch_embeddings)
            # Cache new embeddings
            self.cache.set_many(uncached_texts, new_embeddings)
        else:
            logging.debug(f"Cache hit: {len(texts)}/{len(texts)} texts")
            new_embeddings = []

        # Reconstruct full list with cached + new embeddings
        results = cached[:]
        for idx, emb in zip(uncached_indices, new_embeddings):
            results[idx] = emb

        return results

    def count_tokens(self, texts: List[str]) -> int:
        """Returns the number of tokens"""
        return self.client.count_tokens(texts, model=self.model)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_exception_type((
                voyageai.error.ServiceUnavailableError,
                voyageai.error.APIConnectionError,
                voyageai.error.RateLimitError,
                ConnectionError,
                TimeoutError
        ))
    )
    async def _embed(self, texts: List[str], input_type: Literal['document', 'query']) -> List[List[float]]:
        """Embeds a batch of texts with the Voyage API"""
        estimated_tokens = self.client.count_tokens(texts, model=self.model)

        logging.debug(f"Rate limiting: {estimated_tokens} tokens, {len(texts)} texts")

        try:
            async with self.request_rate_limiter.context():
                async with self.token_rate_limiter.context(estimated_tokens) as update_func:
                    response = await self.client.embed(
                        texts=texts,
                        model=self.model,
                        input_type=input_type,
                        output_dimension=self.dimensions
                    )

                    actual_tokens = response.total_tokens
                    update_func(actual_tokens)

            return [embedding for embedding in response.embeddings]

        except voyageai.error.RateLimitError as e:
            logging.warning(f"Voyage API rate limit hit: {e}")
            raise
        except Exception as e:
            if "rate limit" in str(e).lower():
                logging.warning(f"Local rate limit hit: {e}")
            raise

    async def _batch_texts(self, texts: List[str]) -> List[List[str]]:
        """Split a list of texts into batches respecting Voyage API limits."""
        batches = []
        current_batch = []
        current_tokens = 0

        for text in texts:
            text_tokens = self.client.count_tokens([text], model=self.model)

            batch_length_hit = len(current_batch) >= self.max_batch_size
            token_limit_hit = current_batch and current_tokens + text_tokens > self.max_tokens_per_request

            if batch_length_hit or token_limit_hit:
                if current_batch:
                    batches.append(current_batch)
                current_batch = [text]
                current_tokens = text_tokens
            else:
                current_batch.append(text)
                current_tokens += text_tokens

        if current_batch:
            batches.append(current_batch)

        return batches

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_exception_type((
                voyageai.error.ServiceUnavailableError,
                voyageai.error.APIConnectionError,
                voyageai.error.RateLimitError,
                ConnectionError,
                TimeoutError
        ))
    )
    async def rerank(
        self,
        query: str,
        documents: List[str],
        top_k: Optional[int] = None,
        model: Optional[str] = None,
        truncation: bool = True
    ) -> List[Dict]:
        """Rerank documents by relevance to query using Voyage rerank API.

        Args:
            query: The search query (max 8000 tokens for rerank-2.5)
            documents: List of documents to rerank (max 1000 documents)
            top_k: Number of top results to return (default: return all)
            model: Reranker model name (default: self.rerank_model)
            truncation: Whether to truncate long docs (default: True)

        Returns:
            List of dicts with keys: index, document, relevance_score
            Sorted by descending relevance_score

        Limits:
            - Max 1000 documents
            - Query: max 8000 tokens (rerank-2.5/2.5-lite)
            - Query + each doc: max 32,000 tokens (rerank-2.5/2.5-lite)
            - Total tokens: query_tokens × num_docs + sum(doc_tokens) ≤ 600K
        """
        if not documents:
            return []

        if len(documents) > 1000:
            raise ValueError(f"Rerank API supports max 1000 documents, got {len(documents)}")

        model = model or self.rerank_model

        logging.debug(f"Reranking {len(documents)} documents with model {model}")

        try:
            async with self.request_rate_limiter.context():
                response = await self.client.rerank(
                    query=query,
                    documents=documents,
                    model=model,
                    top_k=top_k,
                    truncation=truncation
                )

            # Convert RerankingResult objects to dicts
            results = []
            for r in response.results:
                results.append({
                    "index": r.index,
                    "document": r.document,
                    "relevance_score": r.relevance_score
                })

            return results

        except voyageai.error.RateLimitError as e:
            logging.warning(f"Voyage rerank API rate limit hit: {e}")
            raise
        except Exception as e:
            if "rate limit" in str(e).lower():
                logging.warning(f"Local rate limit hit during rerank: {e}")
            raise

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_exception_type((
                voyageai.error.ServiceUnavailableError,
                voyageai.error.APIConnectionError,
                voyageai.error.RateLimitError,
                ConnectionError,
                TimeoutError
        ))
    )
    async def contextualized_embed(
        self,
        inputs: List[List[str]],
        model: str = "voyage-context-3",
        input_type: Optional[str] = None,
        output_dimension: Optional[int] = 1024,
        truncation: bool = True
    ) -> List[List[List[float]]]:
        """Generate contextualized embeddings using voyage-context-3 model.

        Each chunk is embedded with awareness of its full document context.
        This captures better semantic understanding than isolated chunk embeddings.

        Args:
            inputs: Nested list where each inner list contains chunks from THE SAME document.
                    Example: [
                        ["doc1_chunk1", "doc1_chunk2"],  # Document 1
                        ["doc2_chunk1", "doc2_chunk2", "doc2_chunk3"]  # Document 2
                    ]
            model: Model name (default: "voyage-context-3")
            input_type: Optional input type hint ("document" or "query")
            output_dimension: Embedding dimension (default: 1024 for voyage-context-3)
            truncation: Whether to truncate long chunks (default: True)

        Returns:
            Nested list of embeddings with same structure as input:
            [
                [[emb1_1, ...], [emb1_2, ...]],  # Document 1 embeddings
                [[emb2_1, ...], [emb2_2, ...], [emb2_3, ...]]  # Document 2 embeddings
            ]

        Limits:
            - Max 128 documents per request
            - Max 50 chunks per document
            - Each chunk: max 32,000 tokens (voyage-context-3)

        Note:
            CRITICAL: Each inner list MUST contain chunks from the SAME document.
            Mixing chunks from different documents will produce incorrect embeddings.
        """
        if not inputs:
            return []

        if len(inputs) > 128:
            raise ValueError(f"voyage-context-3 supports max 128 documents, got {len(inputs)}")

        for i, doc_chunks in enumerate(inputs):
            if len(doc_chunks) > 50:
                raise ValueError(
                    f"Document {i} has {len(doc_chunks)} chunks. "
                    f"voyage-context-3 supports max 50 chunks per document."
                )

        logging.debug(f"Contextualized embedding: {len(inputs)} documents, "
                     f"{sum(len(doc) for doc in inputs)} total chunks")

        try:
            async with self.request_rate_limiter.context():
                response = await self.client.embed(
                    texts=inputs,
                    model=model,
                    input_type=input_type,
                    output_dimension=output_dimension,
                    truncation=truncation
                )

            # Response structure matches input structure (nested lists)
            return [doc_embeddings for doc_embeddings in response.embeddings]

        except voyageai.error.RateLimitError as e:
            logging.warning(f"Voyage contextualized embed API rate limit hit: {e}")
            raise
        except Exception as e:
            if "rate limit" in str(e).lower():
                logging.warning(f"Local rate limit hit during contextualized embed: {e}")
            raise
