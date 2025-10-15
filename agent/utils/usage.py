from pydantic import BaseModel, Field


class Usage(BaseModel):
    cached_prompt_tokens: int = Field(description="The number of input tokens (cached)")
    uncached_prompt_tokens: int = Field(description="The number of uncached input tokens")
    thinking_tokens: int = Field(description="The number of thinking tokens")
    completion_tokens: int = Field(description="The number of completion tokens")

    @property
    def input_tokens(self) -> int:
        """Total number of input tokens"""
        return self.cached_prompt_tokens + self.uncached_prompt_tokens
