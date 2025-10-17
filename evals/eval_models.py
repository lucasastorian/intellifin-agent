"""Pydantic models for eval results and runs."""
from typing import List
from pydantic import BaseModel


class AgentResult(BaseModel):
    """Result from running the agent on a single question"""
    answer: str
    execution_time_seconds: float
    cost_usd: float
    input_tokens: int
    output_tokens: int
    num_iter: int


class EvalResult(BaseModel):
    """Result for a single question"""
    id: str
    question: str
    ground_truth: str
    agent_answer: str
    correct: bool
    grader_notes: str = ""  # Notes from the AI grader
    human_override: str = ""  # Empty means no override yet

    # Performance metrics
    execution_time_seconds: float
    cost_usd: float
    input_tokens: int
    output_tokens: int
    num_iter: int


class EvalRun(BaseModel):
    """Complete eval run results"""
    dataset_name: str
    eval_mode: str
    run_timestamp: str

    # Model configuration
    model: str
    provider: str  # "openai", "anthropic", or "xai"
    temperature: float
    tier: str
    reasoning_effort: str
    max_iter: int
    grader_model: str

    # Results
    total_questions: int
    correct_count: int
    accuracy: float
    results: List[EvalResult]

    # Performance metrics
    total_cost_usd: float
    avg_execution_time_seconds: float
    total_input_tokens: int
    total_output_tokens: int
