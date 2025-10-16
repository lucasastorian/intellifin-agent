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

    def keyword_search(self, query: str, column: str, topk: int = 50):
        """Build an FTS5 MATCH query for BM25 ranking.

        Uses OR logic with prefix matching for maximum recall.
        BM25 naturally ranks documents matching more terms higher.

        Args:
            query: Search query string
            column: Column to search
            topk: Maximum number of results to return (default: 50)
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
        self.limit(topk)
        return self

    def fts(self, query: str, column: str, topk: int = 50):
        """Build an FTS5 MATCH query for exact phrase matching.

        Wraps the query in double quotes for exact phrase matching.
        Only returns results containing the exact phrase.

        Args:
            query: Exact phrase to search for
            column: Column to search
            topk: Maximum number of results to return (default: 50)
        """
        # Escape internal quotes and wrap in quotes for exact phrase match
        escaped_query = query.strip().replace('"', '""')
        fts_query = f'"{escaped_query}"'

        self._and(KeywordFTS(Col(column), fts_query))
        self.order_by = []
        self.limit(topk)
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

    def hybrid_search(
        self,
        query: str,
        vector_column: str = "embedding",
        keyword_column: str = "content",
        topk: int = 50,
        alpha: float = 0.7,
        overquery: int = 3
    ):
        """Hybrid search: fuse vector + keyword retrieval with single query.

        Executes vector search and keyword search in parallel, normalizes scores,
        and fuses with weighted combination. Much simpler than HybridFusion.fuse()
        for standard use cases.

        Args:
            query: Search query (used for both vector and keyword)
            vector_column: Column with embeddings (default: "embedding")
            keyword_column: Column with text for FTS (default: "content")
            topk: Final number of results to return (default: 50)
            alpha: Weight for vector scores (1-alpha for keyword). Default 0.7 (70% vector)
            overquery: Overquery factor - fetches topk*overquery from each leg (default: 3)

        Returns:
            self (for chaining .rerank() or .execute())

        Example:
            # Simple hybrid search
            result = await db.table("docs") \\
                .hybrid_search("revenue guidance", topk=10) \\
                .execute()

            # With custom alpha (more keyword weight)
            result = await db.table("docs") \\
                .hybrid_search("revenue guidance", topk=10, alpha=0.5) \\
                .execute()

            # With reranking
            result = await db.table("docs") \\
                .hybrid_search("revenue", topk=100, alpha=0.7) \\
                .rerank("revenue guidance forecast", content_column="content", topk=10) \\
                .execute()

            # With filters
            result = await db.table("filing_notes") \\
                .contains("company_symbols", "AAPL") \\
                .gte("filing_date", "2024-01-01") \\
                .hybrid_search("capex guidance", topk=20) \\
                .execute()

        Note:
            - For advanced use cases (different queries for vector/keyword),
              use HybridFusion.fuse() directly
            - Embedder must support query_vector() and rerank() (VoyageClient)
        """
        # Store parameters for deferred execution
        self._hybrid_search_params = {
            'query': query,
            'vector_column': vector_column,
            'keyword_column': keyword_column,
            'topk': topk,
            'alpha': alpha,
            'overquery': overquery
        }
        return self

    def rerank(self, query: str, content_column: str, topk: int, reranker=None, model: Optional[str] = None):
        """Rerank results using Voyage rerank API for precision refinement.

        Chainable after vector_search(), keyword_search(), or hybrid fusion.
        Reranking happens at execute() time: fetch candidates → extract content → rerank API → reorder.

        Args:
            query: Reranking query (can include instructions for rerank-2.5)
            content_column: Column containing text content to rerank on
            topk: Number of top results to return after reranking
            reranker: Reranker client (if None, uses db.embedder which is VoyageClient)
            model: Reranker model name (default: rerank-2.5 from embedder config)

        Returns:
            self (for chaining .execute())

        Example:
            # Vector search (100 candidates) → rerank to top 10
            result = await db.table("docs") \\
                .vector_search("revenue", "embedding", topk=100) \\
                .rerank("revenue guidance forecast", content_column="content", topk=10) \\
                .execute()

            # Keyword search → rerank
            result = await db.table("docs") \\
                .keyword_search("revenue", "content", limit=100) \\
                .rerank("revenue guidance", content_column="content", topk=10) \\
                .execute()

            # Hybrid → rerank
            builder = await HybridFusion.fuse(builder, vec_params, kw_params, topk=100)
            builder.rerank("revenue guidance", content_column="content", topk=10)
            result = await builder.execute()

        Note:
            - Max 1000 candidates supported by Voyage rerank API
            - Scores become relevance_score (higher=better, range varies)
            - Original _score preserved as _original_score if present
        """
        if topk > 1000:
            raise ValueError(f"Rerank API supports max 1000 results, got topk={topk}")

        # Store parameters for deferred execution
        self._rerank_params = {
            'query': query,
            'content_column': content_column,
            'topk': topk,
            'reranker': reranker,
            'model': model
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

        vector_table = self.table
        vector_column = column
        underlying_field = None

        if self.table in self.schema.views:
            # Resolve the view alias to the underlying table+column and field descriptor
            view_cls = self.schema.views[self.table]
            type_map = view_cls.type_map(self.schema)
            if column not in type_map:
                raise ValueError(f"Column '{column}' not found in view '{self.table}'")

            # Field as declared on the view (carries source mapping)
            view_field = view_cls.get_fields()[column]
            # Underlying field descriptor from the source table (carries flags like contextualized/vector)
            underlying_field = type_map[column]

            # Use the source table/column for vector store
            vector_table = getattr(view_field, '_view_src_table')
            vector_column = getattr(view_field, '_view_src_field')
        else:
            table_cls = self.schema.get_table(self.table)
            fields = table_cls.get_fields()
            if column not in fields:
                raise ValueError(f"Column '{column}' not found in table '{self.table}'")
            underlying_field = fields[column]

        # Create/load the vector store using the actual source table+column
        vector_store = self.db.get_or_create_vector_store(vector_table, vector_column)

        # Determine contextualization from the underlying field descriptor
        is_contextualized = getattr(underlying_field, 'contextualized', False)
        if is_contextualized:
            # print(f"Running contextualized vector search")
            query_embedding = await embedder.contextual_query_vector(query=query)
        else:
            # print(f"Running normal vector search")
            query_embedding = await embedder.query_vector(query=query)

        query_vec = np.array(query_embedding, dtype=np.float32)

        if filter_ids is None and self._pred is not None:
            filter_ids = await self._prefetch_filter_ids()

        ids, scores = vector_store.search(query_vec, topk=topk, filter_ids=filter_ids)

        self._vector_scores = dict(zip(ids.tolist(), scores.tolist()))
        self._return_scores = return_scores

        if len(ids) == 0:
            self._pred = None
            self.in_("id", [])
            self.order_by = []
        else:
            self._pred = None
            self.in_("id", ids.tolist())

            order_cases = " ".join(
                f"WHEN {int(id_)} THEN {rank}"
                for rank, id_ in enumerate(ids.tolist())
            )
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
        if hasattr(self, '_hybrid_search_params'):
            await self._do_hybrid_search(**self._hybrid_search_params)
        elif hasattr(self, '_vector_search_params'):
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

        # Perform reranking if requested (deferred from rerank() call)
        if hasattr(self, '_rerank_params'):
            processed = await self._do_rerank(processed, **self._rerank_params)

        return Result(processed)

    async def _do_rerank(self, rows: List[Dict], query: str, content_column: str, topk: int,
                         reranker=None, model: Optional[str] = None) -> List[Dict]:
        """Internal: Rerank rows using Voyage rerank API.

        Args:
            rows: List of row dicts from SQL query
            query: Reranking query
            content_column: Column containing text to rerank on
            topk: Number of top results to return
            reranker: Reranker client (if None, uses db.embedder)
            model: Reranker model name

        Returns:
            Reordered list of rows with relevance_score attached as _score
        """
        if not rows:
            return rows

        if reranker is None:
            reranker = getattr(self.db, 'embedder', None)
            if reranker is None:
                raise ValueError("No reranker available. Set db.embedder or pass reranker argument")

        # Check reranker has rerank method (should be VoyageClient)
        if not hasattr(reranker, 'rerank'):
            raise ValueError(f"Reranker {type(reranker).__name__} does not have rerank() method")

        # Extract documents from content_column
        documents = []
        for row in rows:
            if content_column not in row:
                raise ValueError(f"Column '{content_column}' not found in row. "
                               f"Available columns: {list(row.keys())}")
            doc = row[content_column]
            if doc is None:
                doc = ""  # Handle None values
            documents.append(str(doc))

        # Call rerank API
        rerank_results = await reranker.rerank(
            query=query,
            documents=documents,
            top_k=topk,
            model=model
        )

        # Build mapping from original index to reranked position
        reranked_rows = []
        for result in rerank_results:
            original_idx = result['index']
            row = rows[original_idx].copy()

            # Preserve original _score if present
            if '_score' in row:
                row['_original_score'] = row['_score']

            # Attach reranking relevance score
            row['_score'] = result['relevance_score']

            reranked_rows.append(row)

        return reranked_rows

    async def _do_hybrid_search(
        self,
        query: str,
        vector_column: str,
        keyword_column: str,
        topk: int,
        alpha: float,
        overquery: int
    ):
        """Internal: Execute hybrid search (vector + keyword fusion).

        Wraps HybridFusion.fuse() logic inline for simpler API.

        Args:
            query: Search query (used for both legs)
            vector_column: Column with embeddings
            keyword_column: Column with text for FTS
            topk: Final number of results
            alpha: Weight for vector scores (0-1)
            overquery: Overquery factor
        """
        # Check if embedder is available for vector search component
        embedder = getattr(self.db, 'embedder', None)
        if embedder is None:
            from ...errors import DatabaseError
            raise DatabaseError(
                "Cannot perform hybrid_search without an embedder. "
                "Set VOYAGE_API_KEY environment variable."
            )

        from ..hybrid import HybridFusion

        # Prefetch filter IDs once (avoids lock contention)
        filter_ids = await self._prefetch_filter_ids()

        # Build params for both legs
        vec_topk = topk * overquery
        kw_topk = topk * overquery

        vec_params = {
            'query': query,
            'column': vector_column,
            'embedder': getattr(self.db, 'embedder', None),
            'return_scores': True,
            'filter_ids': filter_ids
        }

        kw_params = {
            'query': query,
            'column': keyword_column
        }

        # Run both legs in parallel
        async def run_vec():
            await self._do_vector_search(topk=vec_topk, **vec_params)
            return list(self._vector_scores.keys()), self._vector_scores

        async def run_kw():
            return await self._do_keyword_search(topk=kw_topk, **kw_params)

        (vec_ids, vec_scores), (kw_ids, kw_scores) = await asyncio.gather(run_vec(), run_kw())

        # Handle empty results
        if not vec_ids and not kw_ids:
            self._pred = None
            self.in_("id", [])
            self.order_by = []
            self._fused_scores = {}
            self._vector_scores = {}
            return

        if not vec_ids:
            alpha_effective = 0.0  # Keyword-only
        elif not kw_ids:
            alpha_effective = 1.0  # Vector-only
        else:
            alpha_effective = alpha

        def minmax(scores: Dict[int, float]) -> Dict[int, float]:
            if not scores:
                return {}
            vals = list(scores.values())
            lo, hi = min(vals), max(vals)
            if hi == lo:
                return {k: 1.0 for k in scores}
            rng = hi - lo
            return {k: (v - lo) / rng for k, v in scores.items()}

        vec_shifted = {i: (s + 1.0) / 2.0 for i, s in vec_scores.items()}
        vec_norm = minmax(vec_shifted)

        kw_norm = minmax(kw_scores)

        # Fuse scores
        all_ids = list(dict.fromkeys(vec_ids + kw_ids))  # Preserve order, dedupe
        fused = {
            id_: alpha_effective * vec_norm.get(id_, 0.0) + (1 - alpha_effective) * kw_norm.get(id_, 0.0)
            for id_ in all_ids
        }

        final_ids = sorted(all_ids, key=lambda i: fused[i], reverse=True)[:topk]

        # Apply CASE ORDER BY
        if not final_ids:
            self._pred = None
            self.in_("id", [])
            self.order_by = []
            self._fused_scores = {}
            self._vector_scores = {}
        else:
            self._pred = None
            self.in_("id", final_ids)
            order_cases = " ".join(
                f"WHEN {int(id_)} THEN {rank}"
                for rank, id_ in enumerate(final_ids)
            )
            self.order_by = [(f"CASE id {order_cases} END", False)]  # ASC
            self._fused_scores = fused
            self._vector_scores = fused  # Backward compat for _score attachment

        # Clear deferred vector params if present (hybrid handles vector search)
        if hasattr(self, "_vector_search_params"):
            delattr(self, "_vector_search_params")
