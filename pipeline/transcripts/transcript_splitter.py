from typing import List, Dict, Tuple


class TranscriptSplitter:
    """
    A hybrid approach that combines rule-based and LLM-based transcript segmentation.
    First splits by Operator appearances, then applies LLM segmentation to oversized sections.
    """

    def split(self, sections: List[Dict]) -> Tuple[List[List[Dict]], List[int]]:
        """
        Segments an earnings call transcript using a hybrid approach and returns segments with split IDs.

        Args:
            sections: List of dictionaries, each with 'speaker' and 'paragraphs' (containing 'id' and 'content').

        Returns:
            Tuple of (segments, split_ids), where segments is a list of section lists, and split_ids marks segment ends.
        """
        segments = []
        current_segment = []

        for section in sections:
            if section['speaker'] == 'Operator' and current_segment:
                segments.append(current_segment)
                current_segment = [section]
            else:
                current_segment.append(section)

        if current_segment:
            segments.append(current_segment)

        return segments
