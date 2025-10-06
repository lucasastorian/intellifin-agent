"""SELECT query builder."""
import re
from typing import Optional
from .mixins import PredMixin, SelectMixin
from ..ir import SelectIR, KeywordFTS, Col
from ..binder import bind_select
from ..planner import plan_select
from ..sqlgen import generate_select
from ..dialect.sqlite import SQLiteDialect
from ...results import Result

# Split at letter-digit boundaries (FY2025 -> FY 2025, Q4 -> Q 4)
ALNUM_SPLIT = re.compile(r"(?<=\D)(?=\d)|(?<=\d)(?=\D)")


class SelectBuilder(PredMixin, SelectMixin):
    """Fluent builder for SELECT queries."""

    def __init__(self, database, schema, table: str):
        self.db = database
        self.schema = schema
        self.table = table
        self.dialect = SQLiteDialect()
        self._pred = None
        self.selected = None
        self.order_by = []
        self.limit_n = None

    def keyword_search(self, query: str, column: str):
        """Build an FTS5 MATCH query for BM25 ranking.

        Uses OR logic with prefix matching for maximum recall.
        BM25 naturally ranks documents matching more terms higher.

        Args:
            query: Search query string
            column: Column to search
        """
        # Normalize: split at letter-digit boundaries, quote hyphenated words
        tokens = []
        for tok in query.strip().split():
            # Check if token contains hyphen - if so, quote it to keep as phrase
            if '-' in tok:
                # Escape internal quotes and quote the whole hyphenated word
                escaped = tok.strip('"').replace('"', '""')
                tokens.append(f'"{escaped}"')
            else:
                # Split at letter-digit boundaries for things like FY2025, Q4
                for sub in ALNUM_SPLIT.split(tok):
                    sub = sub.strip().strip('"').lower()
                    if not sub:
                        continue
                    # Add prefix wildcard for recall on longer tokens
                    if len(sub) >= 3 and '"' not in sub and "*" not in sub:
                        sub = f"{sub}*"
                    tokens.append(sub)

        if not tokens:
            tokens = ["*"]

        # Always use OR - BM25 handles relevance ranking
        fts_query = " OR ".join(tokens)

        self._and(KeywordFTS(Col(column), fts_query))
        self.order_by = []
        return self

    def execute(self):
        """Execute the SELECT query."""
        ir = SelectIR(
            table=self.table,
            columns=self.selected,
            where=self._pred,
            order=self.order_by,
            limit=self.limit_n,
        )
        bound = bind_select(ir, self.schema)
        planned, _ = plan_select(bound, self.schema, self.dialect)
        sql, params = generate_select(planned, self.dialect)
        rows = self.db._exec(sql, params)

        processed = []
        for row in rows:
            row = self.db._deserialize_json_fields(self.table, row)
            processed.append(row)

        return Result(processed)
