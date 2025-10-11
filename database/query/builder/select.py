"""SELECT query builder."""
import asyncio
import re
import numpy as np
from typing import Optional, List
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

    def fts(self, query: str, column: str):
        """Build an FTS5 MATCH query for exact phrase matching.

        Wraps the query in double quotes for exact phrase matching.
        Only returns results containing the exact phrase.

        Args:
            query: Exact phrase to search for
            column: Column to search
        """
        # Escape internal quotes and wrap in quotes for exact phrase match
        escaped_query = query.strip().replace('"', '""')
        fts_query = f'"{escaped_query}"'

        self._and(KeywordFTS(Col(column), fts_query))
        self.order_by = []
        return self

    async def vector_search(self, query: str, column: str, topk: int = 50, embedder=None, return_scores: bool = False):
        """Vector similarity search using brute-force cosine similarity.

        Results are automatically ordered by similarity score (descending) using SQL CASE ORDER BY.
        Scores are attached to each result row as '_score' field.

        Args:
            query: Query text to embed and search
            column: Column name (must have associated vector store)
            topk: Number of results to return from vector search
            embedder: Embedder instance (if None, uses db.embedder)
            return_scores: If True, _score field is guaranteed present (deprecated - always True)

        Returns:
            self (for chaining .select().execute())

        Note:
            - Any WHERE predicates added before vector_search() filter the candidate set
            - Results are ordered by similarity score, not by any subsequent .order() calls
            - Use .limit() to further restrict results beyond topk
        """
        if embedder is None:
            embedder = getattr(self.db, 'embedder', None)
            if embedder is None:
                raise ValueError("No embedder available. Pass embedder argument or set db.embedder")

        # Resolve view to underlying table for vector store lookup
        vector_table = self.table
        if self.table in self.schema.views:
            view_cls = self.schema.views[self.table]
            type_map = view_cls.type_map(self.schema)
            if column not in type_map:
                raise ValueError(f"Column '{column}' not found in view '{self.table}'")
            # Get the source table for this column
            view_field = view_cls.get_fields()[column]
            vector_table = view_field._view_src_table

        # Get or create vector store for the underlying table/column
        vector_store = self.db.get_or_create_vector_store(vector_table, column)

        # Embed query
        query_embedding = await embedder.query_vector(query)
        query_vec = np.array(query_embedding, dtype=np.float32)

        # Collect any existing WHERE clause IDs for filtering
        filter_ids = None
        if self._pred is not None:
            # Execute current predicates to get candidate IDs
            temp_ir = SelectIR(
                table=self.table,
                columns=["id"],
                where=self._pred,
                order=[],
                limit=None,
            )
            from ..binder import bind_select
            from ..planner import plan_select
            from ..sqlgen import generate_select
            bound = bind_select(temp_ir, self.schema)
            planned, _ = plan_select(bound, self.schema, self.dialect)
            sql, params = generate_select(planned, self.dialect)
            rows = await asyncio.to_thread(self.db._exec, sql, params)
            filter_ids = [row['id'] for row in rows]

        # Search vector store
        ids, scores = vector_store.search(query_vec, topk=topk, filter_ids=filter_ids)

        # Store scores for later retrieval (always store, not just when return_scores=True)
        self._vector_scores = dict(zip(ids.tolist(), scores.tolist()))
        self._return_scores = return_scores

        # Filter by returned IDs and preserve ranking with SQL ORDER BY
        if len(ids) == 0:
            # No results - add impossible predicate
            self._pred = None
            self.in_("id", [])
            self.order_by = []
        else:
            # Reset predicate and filter by vector search results
            self._pred = None
            self.in_("id", ids.tolist())

            # Build CASE ORDER BY to preserve vector ranking
            # CASE id WHEN <id1> THEN 0 WHEN <id2> THEN 1 ... END
            # Lower rank = better match (ASC order)
            order_cases = " ".join(
                f"WHEN {int(id_)} THEN {rank}"
                for rank, id_ in enumerate(ids.tolist())
            )
            # order_by expects list of tuples: (expression, desc_bool)
            self.order_by = [(f"CASE id {order_cases} END", False)]  # False = ASC

        return self

    def count(self) -> int:
        """Execute COUNT(*) query and return the integer count directly."""
        ir = SelectIR(
            table=self.table,
            columns=["COUNT(*) as count"],
            where=self._pred,
            order=[],
            limit=None,
        )
        bound = bind_select(ir, self.schema)
        planned, _ = plan_select(bound, self.schema, self.dialect)
        sql, params = generate_select(planned, self.dialect)
        rows = self.db._exec(sql, params)

        return rows[0]['count'] if rows else 0

    async def execute(self):
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
        rows = await asyncio.to_thread(self.db._exec, sql, params)

        processed = []
        for row in rows:
            row = self.db._deserialize_json_fields(self.table, row)
            # Attach vector score if available
            if hasattr(self, '_vector_scores') and self._vector_scores and 'id' in row:
                row['_score'] = self._vector_scores.get(row['id'], 0.0)
            processed.append(row)

        return Result(processed)
