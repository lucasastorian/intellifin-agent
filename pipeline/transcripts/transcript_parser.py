import re
from typing import List


class TranscriptParser:
    """Uses Regex to clean and split the raw text transcript into sections by speaker"""

    def parse(self, content: str) -> List[dict]:
        """Splits the transcript by speaker and handles edge cases"""
        content = self._remove_executives_and_analyst_headers(text=content)

        # Split into sections by new speaker
        # Matches either:
        # 1. Start of string followed by word(s) and colon for first speaker (Operator:)
        # 2. Newline followed by 1-3 words and colon for subsequent speakers
        pattern = r'(?:^([A-Za-z \.]{2,50}):)|(?:\n([A-Za-z \.]{2,50}):)'

        sections = re.split(pattern, content)

        # Filter out None/empty values and combine speakers with their content
        results = []
        for i in range(0, len(sections) - 2, 3):
            # Get speaker (could be in either capture group)
            speaker = sections[i + 1] if sections[i + 1] else sections[i + 2]
            content = sections[i + 3].strip()

            if speaker and content:
                results.append({
                    "speaker": speaker.strip(),
                    "content": content
                })

        return results

    @staticmethod
    def _remove_executives_and_analyst_headers(text: str):
        """Removes the executives and analyst headers"""
        if text.startswith("Executives: "):
            sections = text.split("\n")
            text = "\n".join(sections[1:])

        if text.startswith("Analysts: "):
            sections = text.split("\n")
            text = "\n".join(sections[1:])

        if text.startswith("Company Representatives"):
            sections = text.split("\n")
            text = "\n".join(sections[1:])

        return text
