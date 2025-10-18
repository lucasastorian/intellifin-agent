import yaml
from enum import Enum
from typing import List, Optional
from pydantic import BaseModel


class EvalMode(Enum):
    """Evaluation mode for handling dataset issues"""
    ORIGINAL = "original"      # As published - no changes
    UPDATED = "updated"        # Fix outdated + added_precision (time-based changes + evaluator precision)
    STANDARD = "standard"      # Fix outdated + incorrect + structurally_invalid (all corrections)
    STRICT = "strict"          # Fix everything + revised Q for ambiguous


class Question(BaseModel):
    """Single eval question with ground truth"""
    id: str
    question: str
    ground_truth: str
    ticker: Optional[str] = None
    issue_type: Optional[str] = None


class EvalDataset(BaseModel):
    """Evaluation dataset loaded from YAML"""
    name: str
    questions: List[Question]

    @classmethod
    def from_yaml(cls, path: str, mode: EvalMode = EvalMode.STANDARD) -> 'EvalDataset':
        """
        Load eval dataset from YAML file with specified mode.

        Args:
            path: Path to YAML dataset file
            mode: EvalMode determining which questions/answers to use
                - ORIGINAL: As published - no changes
                - UPDATED: Fix outdated + added_precision (time-based changes + evaluator precision issues)
                - STANDARD: Fix outdated + incorrect + structurally_invalid (all corrections) (default)
                - STRICT: Fix everything + revised Q for ambiguous

        Returns:
            EvalDataset with questions processed according to mode
        """
        with open(path, 'r') as f:
            data = yaml.safe_load(f)

        processed_questions = []

        for item in data['questions']:
            question_id = item['id']
            ticker = item.get('ticker', '')
            issue_type = item.get('issue_type', 'none')

            if mode == EvalMode.ORIGINAL:
                question = item['question']
                ground_truth = item.get('original_ground_truth', item['ground_truth'])

            elif mode == EvalMode.UPDATED:
                question = item['question']
                if issue_type in ['outdated', 'added_precision']:
                    ground_truth = item['ground_truth']
                else:
                    ground_truth = item.get('original_ground_truth', item['ground_truth'])

            elif mode == EvalMode.STANDARD:
                if issue_type == 'structurally_invalid':
                    question = item.get('revised_question', item['question'])
                else:
                    question = item['question']

                if issue_type in ['outdated', 'incorrect_answer', 'structurally_invalid']:
                    ground_truth = item['ground_truth']
                else:
                    ground_truth = item.get('original_ground_truth', item['ground_truth'])

            elif mode == EvalMode.STRICT:
                question = item.get('revised_question', item['question'])
                ground_truth = item['ground_truth']

            else:
                raise ValueError(f"Did not recognize eval mode {mode}")

            processed_questions.append(Question(
                id=question_id,
                question=question,
                ground_truth=ground_truth,
                ticker=ticker,
                issue_type=issue_type
            ))

        return cls(name=data['name'], questions=processed_questions)

    def get_unique_tickers(self) -> set[str]:
        """Extract all unique ticker symbols from the dataset"""
        tickers = set()

        for question in self.questions:
            if not question.ticker:
                continue

            for ticker in question.ticker.split(','):
                ticker = ticker.strip().upper()
                if ticker:
                    tickers.add(ticker)

        return tickers
