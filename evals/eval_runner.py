import asyncio
import json
import time
from datetime import datetime
from pathlib import Path
from typing import List
from pydantic import BaseModel

from agent.agent import Agent
from agent.clients.base_client import BaseClient
from agent.utils.pricing import calculate_cost
from evals.eval_dataset import EvalDataset, Question, EvalMode
from evals.answer_evaluator import Evaluator


class EvalResult(BaseModel):
    """Result for a single question"""
    id: str
    question: str
    ground_truth: str
    agent_answer: str
    correct: bool
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


class EvalRunner:
    """Orchestrates running evals, grading, and generating reports"""

    def __init__(
        self,
        edgar_user_agent: str,
        client: BaseClient,
        evaluator: Evaluator,
        max_iter: int = 20
    ):
        self.edgar_user_agent = edgar_user_agent
        self.client = client
        self.evaluator = evaluator
        self.max_iter = max_iter

    async def run_eval(
        self,
        dataset_path: str,
        output_dir: str = "evals/results",
        mode: EvalMode = EvalMode.STANDARD,
        limit: int = None,
        reasoning_effort: str = "low",
        temperature: float = 1.0,
        tier: str = "tier-3",
    ) -> EvalRun:
        """
        Run evaluation on a dataset.

        Args:
            dataset_path: Path to YAML dataset file
            output_dir: Directory to save results
            mode: EvalMode to use
                - ORIGINAL: As published - no changes
                - UPDATED: Fix only outdated answers (time-based changes)
                - STANDARD: Fix outdated + incorrect + structurally_invalid (all corrections) (default)
                - STRICT: Fix everything + revised Q for ambiguous
            limit: Limit evaluation to first N questions (None = all questions)

        Returns:
            EvalRun with all results and metrics
        """
        dataset = EvalDataset.from_yaml(dataset_path, mode=mode)

        questions = dataset.questions[:limit] if limit else dataset.questions

        print(f"📊 Running eval: {dataset.name}")
        print(f"   Mode: {mode.value.upper()}")
        print(f"   Questions: {len(questions)}" + (f" (limited from {len(dataset.questions)})" if limit else ""))

        client_name = self.client.__class__.__name__.lower()
        if "anthropic" in client_name:
            provider = "anthropic"
        elif "xai" in client_name:
            provider = "xai"
        else:
            provider = "openai"

        print("\n🤖 Running agent on all questions...")
        agent_results = await self._run_agent_parallel(questions, provider, self.client.model)

        agent_answers = [r["answer"] for r in agent_results]

        print("\n📝 Grading responses...")
        correctness = await self._grade_parallel(questions, agent_answers)

        results = []
        correct_count = 0
        total_cost = 0.0
        total_execution_time = 0.0
        total_input_tokens = 0
        total_output_tokens = 0

        for question, agent_result, is_correct in zip(questions, agent_results, correctness):
            if is_correct:
                correct_count += 1

            total_cost += agent_result["cost_usd"]
            total_execution_time += agent_result["execution_time_seconds"]
            total_input_tokens += agent_result["input_tokens"]
            total_output_tokens += agent_result["output_tokens"]

            results.append(EvalResult(
                id=question.id,
                question=question.question,
                ground_truth=question.ground_truth,
                agent_answer=agent_result["answer"],
                correct=is_correct,
                execution_time_seconds=agent_result["execution_time_seconds"],
                cost_usd=agent_result["cost_usd"],
                input_tokens=agent_result["input_tokens"],
                output_tokens=agent_result["output_tokens"],
                num_iter=agent_result["num_iter"]
            ))

        run_timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        avg_execution_time = total_execution_time / len(questions) if questions else 0

        eval_run = EvalRun(
            dataset_name=dataset.name,
            eval_mode=mode.value,
            run_timestamp=run_timestamp,
            model=self.client.model,
            provider=provider,
            temperature=temperature,
            tier=tier,
            reasoning_effort=reasoning_effort,
            max_iter=self.max_iter,
            grader_model=self.evaluator.model,
            total_questions=len(questions),
            correct_count=correct_count,
            accuracy=correct_count / len(questions) if questions else 0,
            results=results,
            total_cost_usd=total_cost,
            avg_execution_time_seconds=avg_execution_time,
            total_input_tokens=total_input_tokens,
            total_output_tokens=total_output_tokens
        )

        run_dir = Path(output_dir) / f"run_{run_timestamp}"
        run_dir.mkdir(parents=True, exist_ok=True)

        self._save_json(eval_run, run_dir / "results.json")
        self._save_markdown_report(eval_run, run_dir / "report.md")

        print(f"\n✅ Eval complete!")
        print(f"   Accuracy: {correct_count}/{len(questions)} ({eval_run.accuracy:.1%})")
        print(f"   Total Cost: ${eval_run.total_cost_usd:.2f}")
        print(f"   Avg Time: {eval_run.avg_execution_time_seconds:.1f}s per question")
        print(f"   Results saved to: {run_dir}")

        return eval_run

    async def _run_agent_parallel(self, questions: List[Question], provider: str, model: str) -> List[dict]:
        """
        Run agent on all questions in parallel.

        Returns:
            List of dicts with keys: answer, execution_time_seconds, cost_usd, input_tokens, output_tokens
        """
        async def run_single(question: Question) -> dict:
            agent = Agent(
                edgar_user_agent=self.edgar_user_agent,
                client=self.client,
                max_iter=self.max_iter,
                verbose=False
            )

            print(f"   [{question.id}] Running...")
            start_time = time.time()
            result = await agent.run(query=question.question)
            execution_time = time.time() - start_time

            token_usage = agent.get_token_usage()
            input_tokens = token_usage["total_input_tokens"]
            cached_input_tokens = token_usage["total_cached_input_tokens"]
            output_tokens = token_usage["total_output_tokens"] + token_usage["total_thinking_tokens"]

            # Calculate cost
            cost = calculate_cost(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cached_input_tokens=cached_input_tokens,
                provider=provider,
                model=model
            )

            print(f"   [{question.id}] ✓ Complete ({execution_time:.1f}s, ${cost:.4f})")

            return {
                "answer": result or "No response",
                "execution_time_seconds": execution_time,
                "cost_usd": cost,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "num_iter": agent.num_iter
            }

        results = await asyncio.gather(*[run_single(q) for q in questions])
        return results

    async def _grade_parallel(
        self,
        questions: List[Question],
        answers: List[str]
    ) -> List[bool]:
        """Grade all answers in parallel"""
        async def grade_single(question: Question, answer: str) -> bool:
            print(f"   [{question.id}] Grading...")
            is_correct = await self.evaluator.evaluate(
                question=question.question,
                provided_answer=answer,
                actual_answer=question.ground_truth
            )
            status = "✓" if is_correct else "✗"
            print(f"   [{question.id}] {status}")
            return is_correct

        grades = await asyncio.gather(*[
            grade_single(q, a) for q, a in zip(questions, answers)
        ])
        return grades

    def _save_json(self, eval_run: EvalRun, path: Path):
        """Save results as JSON"""
        with open(path, 'w') as f:
            json.dump(eval_run.model_dump(), f, indent=2)

    def _save_markdown_report(self, eval_run: EvalRun, path: Path):
        """Save human-readable markdown report"""
        # Build model config line
        model_info = f"**Model**: {eval_run.model} ({eval_run.provider})"
        if eval_run.reasoning_effort:
            model_info += f" | **Reasoning**: {eval_run.reasoning_effort}"
        model_info += f" | **Temp**: {eval_run.temperature} | **Tier**: {eval_run.tier} | **Max Iter**: {eval_run.max_iter}"

        # Build performance metrics line
        perf_info = f"**Total Cost**: ${eval_run.total_cost_usd:.2f} | **Avg Time**: {eval_run.avg_execution_time_seconds:.1f}s | **Tokens**: {eval_run.total_input_tokens:,} in / {eval_run.total_output_tokens:,} out"

        lines = [
            f"# {eval_run.dataset_name}",
            f"📊 **{eval_run.correct_count}/{eval_run.total_questions} PASSED** ({eval_run.accuracy:.1%}) | **Mode**: {eval_run.eval_mode.upper()} | **Run**: {eval_run.run_timestamp}",
            "",
            model_info,
            f"**Grader**: {eval_run.grader_model}",
            perf_info,
            "",
            "---",
            ""
        ]

        for result in eval_run.results:
            status = "✓" if result.correct else "✗"

            lines.extend([
                f"### {status} {result.id}",
                "",
                f"**Q**: {result.question}",
                "",
                f"**Expected**:",
                "```",
                result.ground_truth,
                "```",
                "",
                f"**Agent**:",
                "```",
                result.agent_answer,
                "```",
                "",
                f"**AI Grade**: {status} {'CORRECT' if result.correct else 'INCORRECT'}",
                "",
                f"**Performance**: {result.execution_time_seconds:.1f}s | ${result.cost_usd:.4f} | {result.input_tokens:,} in / {result.output_tokens:,} out | iter={result.num_iter}",
                "",
                f"**Human**: {result.human_override or '[ ] Override'}",
                "",
                "---",
                ""
            ])

        with open(path, 'w') as f:
            f.write('\n'.join(lines))
