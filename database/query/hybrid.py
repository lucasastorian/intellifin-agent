"""Hybrid search: fuse vector + keyword retrieval with score normalization."""
import asyncio
from typing import Dict, List, Optional


def minmax(scores: Dict[int, float]) -> Dict[int, float]:
    """Min-max normalize scores to [0, 1] range.

    Args:
        scores: Dict mapping IDs to raw scores

    Returns:
        Dict mapping IDs to normalized scores in [0, 1]

    Note:
        Returns uniform 1.0 for all IDs if all scores are equal (avoids zeroing a leg).
    """
    if not scores:
        return {}

    vals = list(scores.values())
    lo, hi = min(vals), max(vals)

    # Edge case: all scores equal → return uniform to avoid zeroing
    if hi == lo:
        return {k: 1.0 for k in scores}

    rng = hi - lo
    return {k: (v - lo) / rng for k, v in scores.items()}


class HybridFusion:
    """Fuse vector and keyword search results with weighted score combination."""

    @staticmethod
    async def fuse(
        builder,
        vec_params: dict,
        kw_params: dict,
        topk: int = 50,
        overquery: int = 3,
        alpha: float = 0.7,
    ):
        """Fuse vector and keyword search with min-max normalization.

        Args:
            builder: SelectBuilder instance
            vec_params: Parameters for vector search (query, column, embedder, etc.)
            kw_params: Parameters for keyword search (query, column)
            topk: Final number of results to return
            overquery: Overquery factor (fetch overquery * topk from each leg)
            alpha: Weight for vector scores (1-alpha for keyword). Default 0.7.

        Returns:
            builder (modified with fused results, for chaining .execute())

        Algorithm:
            1. Prefetch filter IDs once (avoids lock contention)
            2. Run both legs in parallel with overquery
            3. Normalize scores via min-max to [0, 1]
            4. Fuse: score = alpha * vec_norm + (1-alpha) * kw_norm
            5. Gate to top-k and apply CASE ORDER BY
        """
        # 0. Prefetch filter IDs once before launching both legs
        filter_ids = await builder._prefetch_filter_ids()

        # 1. Launch both legs in parallel
        vec_topk = topk * overquery
        kw_topk = topk * overquery

        # Inject filter_ids into vector params
        vec_params = {**vec_params, 'filter_ids': filter_ids}

        async def run_vec():
            await builder._do_vector_search(topk=vec_topk, **vec_params)
            return list(builder._vector_scores.keys()), builder._vector_scores

        async def run_kw():
            return await builder._do_keyword_search(topk=kw_topk, **kw_params)

        (vec_ids, vec_scores), (kw_ids, kw_scores) = await asyncio.gather(run_vec(), run_kw())

        # Handle empty results from either leg
        if not vec_ids and not kw_ids:
            # No results from either leg - return empty
            builder._pred = None
            builder.in_("id", [])
            builder.order_by = []
            builder._fused_scores = {}
            builder._vector_scores = {}  # Backward compat for execute()
            # Clear deferred vector params to prevent double execution
            if hasattr(builder, "_vector_search_params"):
                delattr(builder, "_vector_search_params")
            return builder

        if not vec_ids:
            # Keyword-only
            alpha_effective = 0.0
        elif not kw_ids:
            # Vector-only
            alpha_effective = 1.0
        else:
            # Both legs have results
            alpha_effective = alpha

        # 2. Normalize scores (cosine is already [-1, 1], shift to [0, 1] then min-max)
        # Vector: cosine similarity [-1, 1] → [0, 1] via (x+1)/2
        vec_shifted = {i: (s + 1.0) / 2.0 for i, s in vec_scores.items()}
        vec_norm = minmax(vec_shifted)

        # Keyword: already negated BM25 (higher=better)
        kw_norm = minmax(kw_scores)

        # 3. Fuse scores
        all_ids = list(dict.fromkeys(vec_ids + kw_ids))  # Preserve order, dedupe
        fused = {
            id_: alpha_effective * vec_norm.get(id_, 0.0) + (1 - alpha_effective) * kw_norm.get(id_, 0.0)
            for id_ in all_ids
        }

        # 4. Gate to top-k
        final_ids = sorted(all_ids, key=lambda i: fused[i], reverse=True)[:topk]

        # 5. Apply CASE ORDER BY to preserve fused ranking
        if not final_ids:
            builder._pred = None
            builder.in_("id", [])
            builder.order_by = []
            builder._fused_scores = {}
            builder._vector_scores = {}  # Backward compat for execute()
        else:
            builder._pred = None
            builder.in_("id", final_ids)
            order_cases = " ".join(
                f"WHEN {int(id_)} THEN {rank}"
                for rank, id_ in enumerate(final_ids)
            )
            builder.order_by = [(f"CASE id {order_cases} END", False)]  # ASC
            # Store fused scores with clear naming
            builder._fused_scores = fused
            builder._vector_scores = fused  # Backward compat for execute() to attach _score

        # Clear deferred vector params to prevent double execution
        # Critical: Without this, calling .execute() after fusion would run vector search again
        if hasattr(builder, "_vector_search_params"):
            delattr(builder, "_vector_search_params")

        return builder
