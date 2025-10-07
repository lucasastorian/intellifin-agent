from typing import List


class Evaluator:

    def __init__(self, questions: List[str], answers: List[str], symbols: List[str]):
        self.questions = questions
        self.answers = answers

    async def run(self):
        """Preloads the symbols BEFORE actually running the eval"""
        pass

    async def evaluate_question(self, question: str, answer: str):
        """Runs a single eval job"""
        pass
