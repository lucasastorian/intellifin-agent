"""SELECT query builder."""
import asyncio
import re
import numpy as np
from typing import Optional, List, Tuple, Dict
from .mixins import PredMixin, SelectMixin
from ..ir import SelectIR, KeywordFTS, Col, And
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

    def keyword_search(self, query: str, column: str, limit: int = 50):
        """Build an FTS5 MATCH query for BM25 ranking.

        Uses OR logic with prefix matching for maximum recall.
        BM25 naturally ranks documents matching more terms higher.

        Args:
            query: Search query string
            column: Column to search
            limit: Maximum number of results to return (default: 50)
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
        self.limit(limit)
        return self

    def fts(self, query: str, column: str, limit: int = 50):
        """Build an FTS5 MATCH query for exact phrase matching.

        Wraps the query in double quotes for exact phrase matching.
        Only returns results containing the exact phrase.

        Args:
            query: Exact phrase to search for
            column: Column to search
            limit: Maximum number of results to return (default: 50)
        """
        # Escape internal quotes and wrap in quotes for exact phrase match
        escaped_query = query.strip().replace('"', '""')
        fts_query = f'"{escaped_query}"'

        self._and(KeywordFTS(Col(column), fts_query))
        self.order_by = []
        self.limit(limit)
        return self

    def vector_search(self, query: str, column: str, topk: int = 50, embedder=None, return_scores: bool = False):
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
            self (for chaining .execute())

        Note:
            - Embedding happens at execute() time, so this method is synchronous and chainable
            - Any WHERE predicates added before vector_search() filter the candidate set
            - Results are ordered by similarity score, not by any subsequent .order() calls
            - Use .limit() to further restrict results beyond topk
        """
        # Store parameters for deferred execution
        self._vector_search_params = {
            'query': query,
            'column': column,
            'topk': topk,
            'embedder': embedder,
            'return_scores': return_scores
        }
        return self

    async def _prefetch_filter_ids(self) -> Optional[List[int]]:
        """Prefetch IDs from current WHERE predicates for filtering vector/keyword searches.

        This avoids lock contention when running parallel searches by executing the
        prefilter query once before launching both legs.

        Returns:
            List of IDs matching current predicates, or None if no predicates set.
        """
        if self._pred is None:
            return None

        temp_ir = SelectIR(
            table=self.table,
            columns=["id"],
            where=self._pred,
            order=[],
            limit=None,
        )
        bound = bind_select(temp_ir, self.schema)
        planned, _ = plan_select(bound, self.schema, self.dialect)
        sql, params = generate_select(planned, self.dialect)
        rows = await self.db._exec(sql, params)
        return [row['id'] for row in rows]

    async def _do_vector_search(self, query: str, column: str, topk: int, embedder, return_scores: bool,
                                filter_ids: Optional[List[int]] = None):
        """Internal: Perform the actual vector search with embedding.

        Args:
            query: Query text to embed
            column: Column name for vector search
            topk: Number of results to return
            embedder: Embedder instance
            return_scores: Whether to return scores (deprecated, always True)
            filter_ids: Pre-fetched IDs to filter by. If None and self._pred exists,
                       will query for filter IDs (not recommended for parallel searches).
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

        # Use provided filter_ids or query for them if not provided (backward compat)
        if filter_ids is None and self._pred is not None:
            filter_ids = await self._prefetch_filter_ids()

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

    async def _do_keyword_search(self, query: str, column: str, topk: int) -> Tuple[List[int], Dict[int, float]]:
        """Internal: Perform keyword search and return (ids, scores).

        Scores are negated BM25 values (higher=better) for normalization.
        Falls back to rank-only pseudo-scores if BM25 unavailable.

        Args:
            query: FTS query string (already tokenized/normalized)
            column: Column to search
            topk: Number of results to return

        Returns:
            Tuple of (ids, scores_dict) where scores are higher-is-better.

        Note:
            Respects existing predicates by combining them with FTS via AND.
            Example: .contains("company_symbols", "AAPL")._do_keyword_search(...)
            will search only within AAPL documents.
        """
        # Combine existing predicates with FTS predicate
        base_pred = self._pred
        fts_pred = KeywordFTS(Col(column), query)
        where_pred = fts_pred if base_pred is None else And([base_pred, fts_pred])

        # Build minimal IR that projects id + _rank
        ir = SelectIR(
            table=self.table,
            columns=["id"],
            where=where_pred,
            order=[],  # Let FTS default order apply (rank ASC)
            limit=topk,
        )

        # Bind, plan, generate
        bound = bind_select(ir, self.schema)
        planned, _ = plan_select(bound, self.schema, self.dialect)
        sql, params = generate_select(planned, self.dialect)

        # Execute
        rows = await self.db._exec(sql, params)

        ids = [r["id"] for r in rows]

        # Extract scores (planner adds _rank column if FTS is present)
        if rows and "_rank" in rows[0]:
            # BM25 is lower-is-better → negate for higher-is-better
            scores = {r["id"]: -float(r["_rank"]) for r in rows}
        else:
            # Fallback: rank-only pseudo-scores (descending by position)
            n = len(ids)
            if n == 0:
                scores = {}
            elif n == 1:
                scores = {ids[0]: 1.0}
            else:
                scores = {id_: 1.0 - (idx / (n - 1)) for idx, id_ in enumerate(ids)}

        return ids, scores

    async def count(self) -> int:
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
        rows = await self.db._exec(sql, params)

        return rows[0]['count'] if rows else 0

    async def execute(self):
        """Execute the SELECT query."""
        # Perform vector search if requested (deferred from vector_search() call)
        if hasattr(self, '_vector_search_params'):
            await self._do_vector_search(**self._vector_search_params)

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
        rows = await self.db._exec(sql, params)

        processed = []
        for row in rows:
            row = self.db._deserialize_json_fields(self.table, row)
            # Attach vector score if available
            if hasattr(self, '_vector_scores') and self._vector_scores and 'id' in row:
                row['_score'] = self._vector_scores.get(row['id'], 0.0)
            processed.append(row)

        return Result(processed)
