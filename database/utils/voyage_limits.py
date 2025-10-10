import os
import logging
from typing import Dict, Any


class VoyageLimits:
    """Manages Voyage API rate limits and model-specific constraints"""

    # Model-specific token limits per request and rate limits
    MODEL_LIMITS = {
        "voyage-3.5-lite": {"max_tokens_per_request": 1_000_000, "max_tokens_per_minute": 16_000_000,
                            "max_requests_per_minute": 2000},
        "voyage-3.5": {"max_tokens_per_request": 320_000, "max_tokens_per_minute": 8_000_000,
                       "max_requests_per_minute": 2000},
        "voyage-3-large": {"max_tokens_per_request": 120_000, "max_tokens_per_minute": 3_000_000,
                           "max_requests_per_minute": 2000},
        "voyage-code-3": {"max_tokens_per_request": 320_000, "max_tokens_per_minute": 3_000_000,
                          "max_requests_per_minute": 2000},
        "voyage-3": {"max_tokens_per_request": 120_000, "max_tokens_per_minute": 3_000_000,
                     "max_requests_per_minute": 2000},
        "voyage-2": {"max_tokens_per_request": 120_000, "max_tokens_per_minute": 3_000_000,
                     "max_requests_per_minute": 2000},
        "voyage-code-2": {"max_tokens_per_request": 120_000, "max_tokens_per_minute": 3_000_000,
                          "max_requests_per_minute": 2000},
    }

    # Conservative defaults for unknown models
    DEFAULT_LIMITS = {
        "max_tokens_per_request": 50_000,
        "max_tokens_per_minute": 1_000_000,
        "max_requests_per_minute": 100
    }

    def __init__(self, model: str):
        self.model = model
        self._load_limits()

    def _load_limits(self):
        """Load model-specific limits with environment variable overrides"""
        if self.model not in self.MODEL_LIMITS:
            logging.warning(
                f"Unsupported model: {self.model}. Using conservative defaults. Supported models: {list(self.MODEL_LIMITS.keys())}")
            base_limits = self.DEFAULT_LIMITS
        else:
            base_limits = self.MODEL_LIMITS[self.model]

        self.max_tokens_per_request = base_limits["max_tokens_per_request"]
        self.max_tokens_per_minute = base_limits["max_tokens_per_minute"]
        self.max_requests_per_minute = base_limits["max_requests_per_minute"]

    def get_limits(self) -> Dict[str, int]:
        """Get all limits as a dictionary"""
        return {
            "max_tokens_per_request": self.max_tokens_per_request,
            "max_tokens_per_minute": self.max_tokens_per_minute,
            "max_requests_per_minute": self.max_requests_per_minute
        }

    def is_supported_model(self) -> bool:
        """Check if the model is officially supported"""
        return self.model in self.MODEL_LIMITS
