import os
import logging
import asyncio
import voyageai
from typing import List, Literal, Optional, Dict
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from ..utils.rate_limiters.token_limiter import TokenRateLimiter
from ..utils.rate_limiters.request_limiter import RequestRateLimiter
from ..utils.voyage_limits import VoyageLimits
from ..embeddings.embedding_cache import EmbeddingCache

os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")


class VoyageClient:

    max_batch_size: int = 1000
    max_batch_tokens: int = 100_000

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

    async def query_vector(self, query: str) -> List[float]:
        """Generates a single query vector"""
        result = await self._embed(texts=[query], input_type="query")
        return result[0]

    async def contextual_query_vector(self, query: str) -> List[float]:
        """Generates a contextual query vector"""
        result = await self._contextualized_embed(inputs=[[query]], model="voyage-context-3",
                                                  input_type="query", output_dimension=self.dimensions)
        return result[0][0]

    async def embed(self, texts: List[str]) -> List[List[float]]:
        """Generates a flat list of embeddings for all texts."""
        # print(f"Generating {len(texts)} embeddings")
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

            self.cache.set_many(uncached_texts, new_embeddings)

        else:
            logging.debug(f"Cache hit: {len(texts)}/{len(texts)} texts")
            new_embeddings = []

        results = cached[:]
        for idx, emb in zip(uncached_indices, new_embeddings):
            results[idx] = emb

        return results

    def count_tokens(self, texts: List[str], model: str = "voyage-3.5-lite") -> int:
        """Returns the number of tokens"""
        return self.client.count_tokens(texts=texts, model=model)

    # @retry(
    #     stop=stop_after_attempt(3),
    #     wait=wait_exponential(multiplier=1, min=4, max=10),
    #     retry=retry_if_exception_type((
    #             voyageai.error.ServiceUnavailableError,
    #             voyageai.error.APIConnectionError,
    #             voyageai.error.RateLimitError,
    #             ConnectionError,
    #             TimeoutError
    #     ))
    # )
    async def _embed(self, texts: List[str], input_type: Literal['document', 'query']) -> List[List[float]]:
        """Embeds a batch of texts with the Voyage API"""
        estimated_tokens = self.client.count_tokens(texts, model=self.model)

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

    # @retry(
    #     stop=stop_after_attempt(3),
    #     wait=wait_exponential(multiplier=1, min=4, max=10),
    #     retry=retry_if_exception_type((
    #             voyageai.error.ServiceUnavailableError,
    #             voyageai.error.APIConnectionError,
    #             voyageai.error.RateLimitError,
    #             ConnectionError,
    #             TimeoutError
    #     ))
    # )
    async def rerank(self, query: str, documents: List[str], top_k: Optional[int] = None, model: Optional[str] = None,
                     truncation: bool = True) -> List[Dict]:
        """Rerank documents by relevance to query using Voyage rerank API."""
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

    # @retry(
    #     stop=stop_after_attempt(3),
    #     wait=wait_exponential(multiplier=1, min=4, max=10),
    #     retry=retry_if_exception_type((
    #             voyageai.error.ServiceUnavailableError,
    #             voyageai.error.APIConnectionError,
    #             voyageai.error.RateLimitError,
    #             ConnectionError,
    #             TimeoutError
    #     ))
    # )
    def _preprocess_contextualized_inputs(self, inputs: List[List[str]], max_tokens: int = 32000) -> tuple:
        """Split documents exceeding max_tokens into smaller sub-documents.

        Args:
            inputs: List of documents (each document is a list of chunk texts)
            max_tokens: Maximum tokens per document (default 32k for voyage-context-3)

        Returns:
            Tuple of (processed_inputs, doc_to_original) where:
            - processed_inputs: List with oversized documents split into sub-documents
            - doc_to_original: List mapping each processed doc index to original doc index
        """
        processed_inputs = []
        doc_to_original = []  # Maps each processed doc to its original index

        for i, document in enumerate(inputs):
            total_tokens = self.client.count_tokens(document, model="voyage-context-3")

            if total_tokens <= max_tokens:
                # Document fits, keep as-is
                processed_inputs.append(document)
                doc_to_original.append(i)
            else:
                # Split document by actively tracking tokens
                logging.info(f"Document {i} has {total_tokens} tokens, splitting into sub-documents")

                current_sub_doc = []
                current_tokens = 0

                for chunk in document:
                    chunk_tokens = self.client.count_tokens([chunk], model="voyage-context-3")

                    # Check if adding this chunk would exceed the limit
                    if current_tokens + chunk_tokens > max_tokens and current_sub_doc:
                        # Finalize current sub-document
                        processed_inputs.append(current_sub_doc)
                        doc_to_original.append(i)

                        # Start new sub-document with this chunk
                        current_sub_doc = [chunk]
                        current_tokens = chunk_tokens
                    else:
                        # Add chunk to current sub-document
                        current_sub_doc.append(chunk)
                        current_tokens += chunk_tokens

                # Don't forget the last sub-document
                if current_sub_doc:
                    processed_inputs.append(current_sub_doc)
                    doc_to_original.append(i)

        return processed_inputs, doc_to_original

    async def contextualized_embed(self, inputs: List[List[str]], model: str = "voyage-context-3",
                                   input_type: str = "document", output_dimension: int = 512) -> List[
        List[float]]:
        """Generate contextualized embeddings using voyage-context-3 model.

        Returns nested list of embeddings (one inner list per document).
        Each inner list (document) is cached independently.

        Args:
            inputs: List of documents, where each document is a list of chunk texts
            model: Contextualized embedding model to use
            input_type: "document" or "query" (optional)
            output_dimension: Embedding dimension

        Returns:
            Nested list of embeddings: List[List[float]] where each inner list
            contains embeddings for one document's chunks
        """
        # print(f"Generating contextual embeddings for {len(inputs)} docs")
        if not inputs:
            return []

        if len(inputs) > 1000:
            raise ValueError(f"voyage-context-3 supports max 1000 documents, got {len(inputs)}")

        processed_inputs, doc_to_original = self._preprocess_contextualized_inputs(inputs)

        for i, doc_chunks in enumerate(processed_inputs):
            if len(doc_chunks) > 1000:
                raise ValueError(
                    f"Processed document {i} has {len(doc_chunks)} chunks. "
                    f"voyage-context-3 supports max 1000 chunks per document."
                )

        if not self.cache:
            response = await self._contextualized_embed(processed_inputs, model, input_type, output_dimension)
            return self._merge_split_embeddings(response, doc_to_original, len(inputs))

        cached_results, uncached_inputs, uncached_indices = self._check_contextualized_cache(processed_inputs)

        if uncached_inputs:
            logging.debug(f"Contextualized cache miss: {len(uncached_inputs)}/{len(processed_inputs)} documents")

            new_embeddings = await self._contextualized_embed(
                uncached_inputs, model, input_type, output_dimension
            )

            self._store_contextualized_cache(uncached_inputs, uncached_indices, new_embeddings, cached_results)
        else:
            logging.debug(f"Contextualized cache hit: {len(processed_inputs)}/{len(processed_inputs)} documents")

        return self._merge_split_embeddings(cached_results, doc_to_original, len(inputs))

    @staticmethod
    def _merge_split_embeddings(embeddings: List[List[float]], doc_to_original: List[int],
                                num_original_docs: int) -> List[List[float]]:
        """Merge embeddings from split documents back into original structure.

        Args:
            embeddings: List of embedding lists (one per processed document)
            doc_to_original: Maps each processed doc index to its original doc index
            num_original_docs: Number of documents in the original input

        Returns:
            List of embedding lists matching the original input structure
        """
        # Group embeddings by original document index
        merged_results = [[] for _ in range(num_original_docs)]

        for processed_idx, original_idx in enumerate(doc_to_original):
            merged_results[original_idx].extend(embeddings[processed_idx])

        return merged_results

    @staticmethod
    def _compute_document_cache_key(document: List[str]) -> str:
        """Compute cache key for a single document (list of chunk texts)."""
        import hashlib
        import json

        canonical = json.dumps(document, sort_keys=True)
        return hashlib.sha256(canonical.encode()).hexdigest()

    def _check_contextualized_cache(self, inputs: List[List[str]]) -> tuple:
        """Check cache for each document independently."""
        cached_results = []
        uncached_inputs = []
        uncached_indices = []

        for i, document in enumerate(inputs):
            cache_key = self._compute_document_cache_key(document)
            cached_embeddings = self.cache.get(cache_key)

            if cached_embeddings is None:
                # Cache miss - need to embed this document
                uncached_inputs.append(document)
                uncached_indices.append(i)
                cached_results.append(None)  # Placeholder
            else:
                # Cache hit - use cached embeddings
                cached_results.append(cached_embeddings)

        return cached_results, uncached_inputs, uncached_indices

    def _store_contextualized_cache(self, documents: List[List[str]], indices: List[int],
                                    embeddings: List[List[float]], results: List) -> None:
        """Store newly generated embeddings to cache and insert into results."""
        for document, idx, doc_embeddings in zip(documents, indices, embeddings):
            cache_key = self._compute_document_cache_key(document)
            self.cache.set(cache_key, doc_embeddings)
            results[idx] = doc_embeddings

    # async def _contextualized_embed(self, inputs: List[List[str]], model: str,
    #                                 input_type: str = "document", output_dimension: int = 512,
    #                                 max_concurrent: int = 10) -> List[List[List[float]]]:
    #     """Make Voyage API calls for contextualized embeddings in parallel."""
    #
    #     batches = self._batch_contextualized_embed(inputs)
    #     semaphore = asyncio.Semaphore(max_concurrent)
    #     results = []
    #
    #     async def embed_batch(batch: List[List[str]]) -> List[List[float]]:
    #         async with semaphore:
    #             async with self.request_rate_limiter.context():
    #                 response = await self.client.contextualized_embed(
    #                     inputs=batch,
    #                     model=model,
    #                     input_type=input_type,
    #                     output_dimension=output_dimension,
    #                 )
    #             return [[embedding for embedding in doc_result.embeddings] for doc_result in response.results]
    #
    #     try:
    #         tasks = [asyncio.create_task(embed_batch(batch)) for batch in batches]
    #
    #         for future in asyncio.as_completed(tasks):
    #             try:
    #                 batch_output = await future
    #                 results.extend(batch_output)
    #
    #             except voyageai.error.RateLimitError as e:
    #                 logging.warning(f"Voyage API rate limit hit: {e}")
    #                 raise
    #
    #             except Exception as e:
    #                 if "rate limit" in str(e).lower():
    #                     logging.warning(f"Local rate limit hit during contextualized embed: {e}")
    #                 raise
    #
    #         return results
    #
    #     except Exception:
    #         logging.exception("Contextualized embedding failed")
    #         raise

    async def _contextualized_embed(
            self,
            inputs: List[List[str]],
            model: str,
            input_type: str = "document",
            output_dimension: int = 512,
    ) -> List[List[List[float]]]:
        """Make Voyage API calls for contextualized embeddings serially."""

        batches = self._batch_contextualized_embed(inputs)
        results = []

        try:
            for i, batch in enumerate(batches):
                async with self.request_rate_limiter.context():
                    response = await self.client.contextualized_embed(
                        inputs=batch,
                        model=model,
                        input_type=input_type,
                        output_dimension=output_dimension,
                    )

                batch_output = [[embedding for embedding in doc_result.embeddings]
                                for doc_result in response.results]
                results.extend(batch_output)

            return results

        except voyageai.error.RateLimitError as e:
            logging.warning(f"Voyage API rate limit hit: {e}")
            raise

        except Exception as e:
            if "rate limit" in str(e).lower():
                logging.warning(f"Local rate limit hit during contextualized embed: {e}")
            logging.exception("Contextualized embedding failed")
            raise

    def _batch_contextualized_embed(self, inputs: List[List[str]]) -> List[List[List[str]]]:
        """Creates batches for contextulaized embeddings"""
        batches = []
        batch = []

        num_tokens: int = 0
        for document in inputs:
            doc_tokens = self.count_tokens(texts=document, model="voyage-context-3")

            if num_tokens + doc_tokens > 64_000 and batch:
                batches.append(batch)
                num_tokens = 0
                batch = []

            batch.append(document)
            num_tokens += doc_tokens

        if batch:
            batches.append(batch)

        return batches
