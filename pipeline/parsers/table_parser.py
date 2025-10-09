from __future__ import annotations

import re
import logging
import pandas as pd
from bs4 import Tag
from dataclasses import dataclass
from typing import List, Optional

logger = logging.getLogger(__name__)

BULLETS = {"•", "●", "◦", "–", "-", "—", "·", ""}

# Robust numeric pattern for SEC filings
# Matches: $1,234.56, (1,234), -1234, 12.5%, etc.
NUMERIC_RE = re.compile(r"""
    ^\s*
    [\(\[]?                      # optional opening paren/bracket
    [\-—–]?\s*                   # optional dash
    [$€£¥]?\s*                   # optional currency
    \d+(?:[.,]\d{3})*           # integer part (with or without thousands)
    (?:[.,]\d+)?                # decimals
    \s*%?                       # optional percent
    [\)\]]?\s*$                 # optional closing paren/bracket
""", re.X)


@dataclass
class Cell:
    """A single cell in a table, potentially containing XBRL data"""
    text: str
    rowspan: int = 1
    colspan: int = 1

    def __bool__(self) -> bool:
        return bool(self.text.strip())

    def __repr__(self) -> str:
        return f"Cell(text={self.text!r}, rowspan={self.rowspan}, colspan={self.colspan})"


class GridCell:
    """A cell in the final grid, possibly part of a spanning cell"""

    def __init__(self, cell: Cell, is_spanning: bool = False):
        self.cell = cell
        self.is_spanning = is_spanning

    @property
    def text(self) -> str:
        return self.cell.text if not self.is_spanning else ""

    def __bool__(self) -> bool:
        return bool(self.text.strip())

    def __repr__(self) -> str:
        return f"GridCell(cell={self.cell!r}, is_spanning={self.is_spanning})"


class TableParser:
    """A table within a filing document"""

    def __init__(self, table_element: Tag):
        """
        Initialize table from a BS4 table tag

        Args:
            table_element: The specific table BS4 tag
        """
        if not isinstance(table_element, Tag) or table_element.name != 'table':
            raise ValueError("table_element must be a table tag")

        self.table_element = table_element

        self.cells = self._extract_cells()
        self.grid = self._create_grid()

    def _extract_cells(self) -> List[List[Cell]]:
        rows = []
        for tr in self.table_element.find_all('tr'):
            row = []
            for td in tr.find_all(['td', 'th']):
                text = td.get_text(separator=" ", strip=True).replace('\xa0', ' ')
                if not text:
                    if td.find('img'):
                        text = '●'  # or '•' depending on your BULLETS set
                rowspan = self._safe_parse_int(td.get('rowspan'))
                colspan = self._safe_parse_int(td.get('colspan'))
                row.append(Cell(text=text, rowspan=rowspan, colspan=colspan))
            if row:
                rows.append(row)
        return rows or [[Cell(text="")]]

    @staticmethod
    def _safe_parse_int(value: str, default: int = 1) -> int:
        """Safely parse an integer value, returning default if parsing fails"""
        try:
            if not value or not isinstance(value, str):
                return default
            cleaned = ''.join(c for c in value if c.isdigit())
            return int(cleaned) if cleaned else default
        except (ValueError, TypeError):
            return default

    def _create_grid(self) -> List[List[GridCell]]:
        """Create grid with spanning cells handled"""
        if not self.cells:
            return []

        # Calculate grid dimensions
        max_cols = max(sum(cell.colspan for cell in row) for row in self.cells)
        grid = [[None for _ in range(max_cols)] for _ in range(len(self.cells))]

        for i, row in enumerate(self.cells):
            col = 0
            for cell in row:
                # Find next empty cell
                while col < max_cols and grid[i][col] is not None:
                    col += 1

                if col >= max_cols:
                    break

                grid[i][col] = GridCell(cell)

                for r in range(cell.rowspan):
                    for c in range(cell.colspan):
                        if r == 0 and c == 0:  # Skip main cell
                            continue
                        ri, ci = i + r, col + c
                        if ri < len(grid) and ci < max_cols:
                            grid[ri][ci] = GridCell(cell, is_spanning=True)

                col += cell.colspan

        grid = self._clean_grid(grid)
        grid = self._merge_grid(grid)

        return grid

    def _should_merge_cells(self, val1: Optional[GridCell], val2: Optional[GridCell]) -> bool:
        """Check if two cells should be merged based on the rules"""
        # Handle empty cells
        if not val1 or not val2:
            return True

        s1 = val1.text.strip()
        s2 = val2.text.strip()

        if not s1 or not s2:
            return True

        if self.is_footnote(s2):
            return True

        if s1 == '$':
            return True

        if s2 == '%':
            return True

        return False

    @staticmethod
    def is_footnote(text: str) -> bool:
        """Check if string is a number or letter within square brackets and nothing else, e.g., [1], [b]"""
        pattern = r'^\[[a-zA-Z0-9]+\]$'
        return bool(re.match(pattern, text))

    @staticmethod
    def _clean_grid(grid: List[List[GridCell]]) -> List[List[GridCell]]:
        """Drop rows and columns that contain only empty cells (no text and no XBRL data)"""
        if not grid:
            return grid

        rows_to_keep = [
            i for i, row in enumerate(grid)
            if any(
                cell is not None and cell.text.strip()
                for cell in row
            )
        ]

        columns_to_keep = [
            j for j in range(len(grid[0]))
            if any(
                grid[i][j] is not None and
                (grid[i][j].text.strip())
                for i in range(len(grid))
            )
        ]

        filtered_grid = [
            [grid[i][j] for j in columns_to_keep]
            for i in rows_to_keep
        ]

        return filtered_grid

    def _merge_grid(self, grid: List[List[GridCell]]) -> List[List[GridCell]]:
        """Merge columns in one clean pass"""
        if not grid or not grid[0]:
            return grid

        result = []
        current_col = None

        for col_idx in range(len(grid[0])):
            col = [row[col_idx] for row in grid]

            if current_col is None:
                current_col = col
                continue

            cell_pairs = list(zip(current_col[1:], col[1:]))
            should_merge = all(self._should_merge_cells(c1, c2) for c1, c2 in cell_pairs)

            if should_merge:
                merged = [current_col[0]]  # Keep header
                for c1, c2 in cell_pairs:
                    if not c1:
                        merged.append(c2)
                    elif not c2:
                        merged.append(c1)
                    else:
                        text = f"{c1.text} {c2.text}".strip()
                        merged_cell = Cell(text=text)
                        merged.append(GridCell(merged_cell))
                current_col = merged
            else:
                result.append(current_col)
                current_col = col

        if current_col is not None:
            result.append(current_col)

        return list(map(list, zip(*result)))

    def to_matrix(self) -> List[List[str]]:
        """Convert grid to text matrix"""
        return [[cell.text if cell else "" for cell in row] for row in self.grid]

    def to_dataframe(self) -> Optional[pd.DataFrame]:
        matrix = self.to_matrix()
        df = pd.DataFrame(matrix)
        if df.empty:
            return None

        # Helper to normalize strings while preserving deliberate blanks
        def norm(x):
            if x is None:
                return ""
            s = str(x).replace("\xa0", " ").strip()
            return s

        nrows, ncols = df.shape

        if nrows >= 2:
            row0 = [norm(v) for v in df.iloc[0]]
            # Treat blanks as intentional blanks
            header = [v if v != "" else "" for v in row0]

            # Heuristic: if many header cells are blank and the next row is non-empty,
            # treat row 1 as the "detail header" (e.g., dates) and fuse it.
            row1 = [norm(v) for v in df.iloc[1]]
            nonempty_row1 = sum(1 for v in row1 if v)
            many_blanks_in_row0 = sum(1 for v in header if v == "") >= max(2, ncols // 2)

            if nonempty_row1 >= max(2, ncols // 2) and many_blanks_in_row0:
                fused = []
                for j in range(ncols):
                    top = header[j]
                    bot = row1[j]
                    if top and bot:
                        fused.append(f"{top} — {bot}")
                    elif top:
                        fused.append(top)
                    elif bot:
                        fused.append(bot)
                    else:
                        fused.append("")
                header = fused
                df = df.iloc[2:].reset_index(drop=True)
            else:
                df = df.iloc[1:].reset_index(drop=True)

            df.columns = header
        else:
            df.columns = ["" for _ in range(ncols)]

        if df.shape[1] >= 1:
            stub_col = df.columns[0]
            df = df.set_index(stub_col, drop=True)

        df = df.dropna(how='all', axis=0)
        df = df.loc[:, ~(df.astype(str).apply(lambda col: col.str.strip()).eq("").all())]

        return df if not df.empty else None

    def _looks_like_list_table(self) -> bool:
        """Special case - some quirky files format lists as tables"""
        if len(self.cells) != 1:
            return False
        row = self.cells[0]
        texts = [c.text.strip() for c in row]
        has_bullet = any(t in BULLETS for t in texts)
        has_payload = any(t for t in texts[1:])
        return has_bullet and has_payload

    def md(self) -> str:
        # Special-case list tables
        if self._looks_like_list_table():
            row = self.cells[0]
            payload = ""
            for c in reversed(row):
                t = c.text.strip()
                if t and t not in BULLETS:
                    payload = t
                    break
            return f"- {payload}" if payload else ""
        df = self.to_dataframe()
        return "" if df is None else df.to_markdown()
