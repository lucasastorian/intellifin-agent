"""Query planning: FTS ranking, default ordering."""
from .ir import SelectIR, KeywordFTS, And, Or
from typing import Tuple, Dict, Optional


def _has_fts(where) -> Optional[str]:
    """Check if predicate tree contains FTS, return FTS column name if found."""
    if where is None:
        return None
    if isinstance(where, KeywordFTS):
        return where.col.name
    if hasattr(where, "parts"):
        for p in where.parts:
            result = _has_fts(p)
            if result:
                return result
    return None


def _find_fts_table(where, table_name: str) -> Optional[str]:
    """Extract FTS table name from predicate tree."""
    col = _has_fts(where)
    if col:
        return f"{table_name}__{col}__fts"
    return None


def plan_select(ir: SelectIR, schema, dialect) -> Tuple[SelectIR, Dict]:
    """Plan SELECT: inject FTS ranking and default ordering."""
    fts_col = _has_fts(ir.where)
    if not fts_col:
        return ir, {}

    if not ir.fts_table:
        raise ValueError("Planner: missing fts_table (binder must provide it)")

    # Create correlated subquery for rank that brings FTS table into scope
    # SELECT (SELECT bm25(fts) FROM fts WHERE rowid = t.pk AND fts MATCH ?) AS _rank
    rank_expr = (
        f"(SELECT {dialect.bm25(ir.fts_table)} "
        f"FROM {dialect.q(ir.fts_table)} "
        f"WHERE rowid = t.{dialect.q(ir.pk)} "
        f"AND {dialect.q(ir.fts_table)} MATCH ?)"
    )

    planned = SelectIR(
        table=ir.table,
        columns=ir.columns,
        where=ir.where,
        order=ir.order,  # Keep user-specified order if any
        limit=ir.limit,
        fts_rank_expr=rank_expr,
        pk=ir.pk,
        fts_table=ir.fts_table,
        fts_query=ir.fts_query,
    )
    return planned, {"fts_table": ir.fts_table, "fts_col": fts_col}
