import os
import logging
import voyageai
from typing import List, Literal
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from ..utils.rate_limiters.token_limiter import TokenRateLimiter
from ..utils.rate_limiters.request_limiter import RequestRateLimiter
from ..utils.voyage_limits import VoyageLimits


class VoyageEmbeddings:
    max_batch_size: int = 1000

    def __init__(self, model: str = "voyage-3.5-lite", dimensions: int = 512):
        self.provider = "voyageai"
        self.model = model
        self.dimensions = dimensions

        api_key = os.environ.get("VOYAGE_API_KEY")
        assert api_key is not None, "VOYAGE_API_KEY environment variable must be set. Get your API key from https://www.voyageai.com/"

        # Load model-specific limits
        self.limits = VoyageLimits(model)

        self.max_tokens_per_request = self.limits.max_tokens_per_request
        self.max_tokens_per_minute = self.limits.max_tokens_per_minute
        self.max_requests_per_minute = self.limits.max_requests_per_minute

        self.token_rate_limiter = TokenRateLimiter(max_tokens=self.max_tokens_per_minute, period=60)
        self.request_rate_limiter = RequestRateLimiter(max_requests=self.max_requests_per_minute, period=60)

        self.client = voyageai.Client(api_key=api_key)

    def query_vector(self, text: str) -> List[float]:
        """Generates a single query vector"""
        return self._embed(texts=[text], input_type="query")[0]

    def embed(self, texts: List[str]) -> List[List[float]]:
        """Generates a flat list of embeddings for all texts."""
        return [
            embedding
            for batch in self._batch_texts(texts=texts)
            for embedding in self._embed(batch, input_type="document")
        ]

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
    def _embed(self, texts: List[str], input_type: Literal['document', 'query']) -> List[List[float]]:
        """Embeds a batch of texts with the Voyage API"""
        estimated_tokens = self.client.count_tokens(texts, model=self.model)

        logging.debug(f"Rate limiting: {estimated_tokens} tokens, {len(texts)} texts")

        try:
            with self.request_rate_limiter.context():
                with self.token_rate_limiter.context(estimated_tokens) as update_func:
                    response = self.client.embed(
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

    def _batch_texts(self, texts: List[str]) -> List[List[str]]:
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
