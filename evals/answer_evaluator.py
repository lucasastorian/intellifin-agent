import openai
from typing import Literal
from pydantic import BaseModel, Field


system_prompt = """
You are an expert evaluator for question-answering systems.

Your task: Determine if the provided answer is factually correct based on the actual answer.

Evaluation criteria:
- Mark as "Yes" if the answer conveys the same core factual information as the actual answer, even if phrased differently
- Mark as "No" if the answer contradicts the actual answer or states incorrect information
- Focus on factual correctness, not stylistic differences
- For numerical/quantitative questions, the specific values must match
- For comparative questions (e.g., "most", "least"), the comparison must be correct

Be strict but fair - minor wording differences are acceptable if the facts are correct.
"""


class Evaluation(BaseModel):
    answer_correct: Literal['Yes', 'No'] = Field(
        description="Whether the answer is factually correct"
    )


class Evaluator:

    def __init__(self, model: str = "gpt-5"):
        self.model = model

        self.client = openai.AsyncOpenAI()

    async def evaluate(self, question: str, provided_answer: str, actual_answer: str) -> bool:
        """pass"""
        message = f"""
        # Evaluation Task

        **Question:** {question}

        **Provided Answer:** {provided_answer}

        **Ground Truth:** {actual_answer}

        Evaluate whether the provided answer is factually correct.
        """

        response = await self.client.responses.parse(
            model=self.model,
            input=[
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": message
                },
            ],
            text_format=Evaluation
        )

        return response.parsed.answer_correct == 'Yes'
