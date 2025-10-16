import asyncio
import json
import time
from datetime import datetime
from pathlib import Path
from typing import List

from agent.agent import Agent
from agent.clients.base_client import BaseClient
from agent.utils.pricing import calculate_cost
from evals.eval_dataset import EvalDataset, Question, EvalMode
from evals.answer_evaluator import Evaluator
from evals.eval_models import AgentResult, EvalResult, EvalRun
from database import Database


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
        serial: bool = False,
        verbose: bool = False
    ) -> EvalRun:
        dataset = EvalDataset.from_yaml(dataset_path, mode=mode)

        questions = dataset.questions[:limit] if limit else dataset.questions

        from schema import schema
        database = Database(schema=schema, base_path="./data/intellifin.db")

        if serial:
            agent_results = await self._run_agent_serial(
                questions=questions,
                database=database,
                skip_sync=True,
                verbose=verbose
            )
        else:
            agent_results = await self._run_agent_parallel(
                questions=questions,
                database=database,
                skip_sync=True,
                verbose=verbose
            )

        agent_answers = [r.answer for r in agent_results]
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

            total_cost += agent_result.cost_usd
            total_execution_time += agent_result.execution_time_seconds
            total_input_tokens += agent_result.input_tokens
            total_output_tokens += agent_result.output_tokens

            results.append(
                EvalResult(
                id=question.id,
                question=question.question,
                ground_truth=question.ground_truth,
                agent_answer=agent_result.answer,
                correct=is_correct,
                execution_time_seconds=agent_result.execution_time_seconds,
                cost_usd=agent_result.cost_usd,
                input_tokens=agent_result.input_tokens,
                output_tokens=agent_result.output_tokens,
                num_iter=agent_result.num_iter
            ))

        run_timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        avg_execution_time = total_execution_time / len(questions) if questions else 0

        eval_run = EvalRun(
            dataset_name=dataset.name,
            eval_mode=mode.value,
            run_timestamp=run_timestamp,
            model=self.client.model,
            provider=self.client.provider,
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

        # Print final summary
        if verbose or serial:
            self._print_final_summary(eval_run)

        return eval_run

    def _print_final_summary(self, eval_run: EvalRun):
        """Print a comprehensive summary of the eval run"""
        print(f"\n{'='*80}")
        print(f"FINAL EVAL SUMMARY")
        print(f"{'='*80}\n")

        print(f"Dataset: {eval_run.dataset_name}")
        print(f"Mode: {eval_run.eval_mode.upper()}")
        print(f"Run Timestamp: {eval_run.run_timestamp}\n")

        print(f"Model Configuration:")
        print(f"  • Model: {eval_run.model} ({eval_run.provider})")
        print(f"  • Temperature: {eval_run.temperature}")
        print(f"  • Reasoning Effort: {eval_run.reasoning_effort}")
        print(f"  • Max Iterations: {eval_run.max_iter}")
        print(f"  • Tier: {eval_run.tier}\n")

        print(f"Results:")
        print(f"  • Accuracy: {eval_run.correct_count}/{eval_run.total_questions} ({eval_run.accuracy:.1%})")
        print(f"  • Passed: {eval_run.correct_count}")
        print(f"  • Failed: {eval_run.total_questions - eval_run.correct_count}\n")

        print(f"Performance:")
        print(f"  • Total Cost: ${eval_run.total_cost_usd:.4f}")
        print(f"  • Avg Cost per Question: ${eval_run.total_cost_usd / eval_run.total_questions:.4f}")
        print(f"  • Avg Execution Time: {eval_run.avg_execution_time_seconds:.2f}s")
        print(f"  • Total Input Tokens: {eval_run.total_input_tokens:,}")
        print(f"  • Total Output Tokens: {eval_run.total_output_tokens:,}")
        print(f"  • Total Tokens: {eval_run.total_input_tokens + eval_run.total_output_tokens:,}\n")

        print(f"Grader: {eval_run.grader_model}\n")

        print(f"{'='*80}\n")

    async def _run_agent_serial(
        self,
        questions: List[Question],
        database: Database,
        skip_sync: bool = False,
        verbose: bool = False
    ) -> List[AgentResult]:
        """Run agent on all questions serially (one at a time) with real-time output."""
        results = []
        total_cost = 0.0
        correct_count = 0

        print(f"\n{'='*80}")
        print(f"SERIAL EVAL: Running {len(questions)} questions one at a time")
        print(f"{'='*80}\n")

        for i, question in enumerate(questions, 1):
            print(f"\n{'─'*80}")
            print(f"[{i}/{len(questions)}] Question: {question.id}")
            print(f"{'─'*80}")
            print(f"Q: {question.question}\n")

            agent = Agent(
                database=database,
                edgar_user_agent=self.edgar_user_agent,
                client=self.client,
                max_iter=self.max_iter,
                verbose=verbose,
                skip_sync=skip_sync
            )

            start_time = time.time()
            result = await agent.run(query=question.question)
            execution_time = time.time() - start_time

            uncached_input_tokens = agent.usage.uncached_prompt_tokens
            cached_input_tokens = agent.usage.cached_prompt_tokens
            output_tokens = agent.usage.completion_tokens + agent.usage.thinking_tokens

            cost = calculate_cost(
                uncached_input_tokens=uncached_input_tokens,
                cached_input_tokens=cached_input_tokens,
                output_tokens=output_tokens,
                model=self.client.model,
                provider=self.client.provider
            )

            total_cost += cost

            agent_result = AgentResult(
                answer=result or "No response",
                execution_time_seconds=execution_time,
                cost_usd=cost,
                input_tokens=agent.usage.input_tokens,
                output_tokens=output_tokens,
                num_iter=agent.num_iter
            )

            results.append(agent_result)

            # Grade immediately after answer
            print(f"\n{'─'*80}")
            print(f"✓ Completed [{i}/{len(questions)}]")
            print(f"Answer: {result}")
            print(f"Time: {execution_time:.2f}s | Cost: ${cost:.4f} | Tokens: {agent.usage.input_tokens:,} in / {output_tokens:,} out | Iterations: {agent.num_iter}")

            # Evaluate answer in real-time
            print(f"\n🔍 Grading answer...")
            is_correct, notes = await self.evaluator.evaluate(
                question=question.question,
                provided_answer=result or "No response",
                actual_answer=question.ground_truth
            )

            if is_correct:
                correct_count += 1
                print(f"✅ CORRECT")
            else:
                print(f"❌ INCORRECT")

            print(f"Expected: {question.ground_truth}")
            print(f"Notes: {notes}")

            print(f"\nRunning Score: {correct_count}/{i} ({correct_count/i:.1%})")
            print(f"Running Total Cost: ${total_cost:.4f}")
            print(f"{'─'*80}\n")

        print(f"\n{'='*80}")
        print(f"SERIAL EVAL COMPLETE")
        print(f"Total Questions: {len(questions)}")
        print(f"Final Score: {correct_count}/{len(questions)} ({correct_count/len(questions):.1%})")
        print(f"Total Cost: ${total_cost:.4f}")
        print(f"{'='*80}\n")

        return results

    async def _run_agent_parallel(
        self,
        questions: List[Question],
        database: Database,
        skip_sync: bool = False,
        verbose: bool = False
    ) -> List[AgentResult]:
        """Run agent on all questions in parallel."""
        async def run_single(question: Question) -> AgentResult:
            agent = Agent(
                database=database,
                edgar_user_agent=self.edgar_user_agent,
                client=self.client,
                max_iter=self.max_iter,
                verbose=verbose,
                skip_sync=skip_sync
            )

            start_time = time.time()
            result = await agent.run(query=question.question)
            execution_time = time.time() - start_time

            uncached_input_tokens = agent.usage.uncached_prompt_tokens
            cached_input_tokens = agent.usage.cached_prompt_tokens
            output_tokens = agent.usage.completion_tokens + agent.usage.thinking_tokens

            cost = calculate_cost(
                uncached_input_tokens=uncached_input_tokens,
                cached_input_tokens=cached_input_tokens,
                output_tokens=output_tokens,
                model=self.client.model,
                provider=self.client.provider
            )

            return AgentResult(
                answer=result or "No response",
                execution_time_seconds=execution_time,
                cost_usd=cost,
                input_tokens=agent.usage.input_tokens,
                output_tokens=output_tokens,
                num_iter=agent.num_iter
            )

        results = await asyncio.gather(*[run_single(q) for q in questions])

        return results

    async def _grade_parallel(
        self,
        questions: List[Question],
        answers: List[str]
    ) -> List[bool]:
        """Grade all answers in parallel"""
        async def grade_single(question: Question, answer: str) -> bool:
            is_correct, _ = await self.evaluator.evaluate(
                question=question.question,
                provided_answer=answer,
                actual_answer=question.ground_truth
            )
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
