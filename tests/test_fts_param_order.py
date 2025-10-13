"""Test FTS parameter ordering for rank projection + EXISTS filter."""
import pytest
from database.query.ir import SelectIR, KeywordFTS, Col
from database.query.binder import bind_select
from database.query.planner import plan_select
from database.query.sqlgen import generate_select
from database.query.dialect.sqlite import SQLiteDialect
from database.schema.schema import Schema
from database.schema.table import Table
from database.schema.fields import Serial, Text


class TestTable(Table):
    __tablename__ = "test_docs"

    id = Serial()
    content = Text(nullable=False, fts=True)


def test_fts_param_order_with_rank_projection():
    """Verify MATCH params appear in correct order: rank subquery first, EXISTS second.

    This is critical because the planner pushes fts_query once for the rank projection,
    then SQLGen pushes it again for the EXISTS filter. Order must be deterministic.
    """
    # Setup schema
    schema = Schema()
    schema.register_table(TestTable)

    # Create IR with KeywordFTS predicate (will trigger both rank + EXISTS)
    ir = SelectIR(
        table="test_docs",
        columns=["id"],
        where=KeywordFTS(Col("content"), "test query"),
        order=[],
        limit=10,
    )

    # Bind (resolves FTS metadata)
    bound = bind_select(ir, schema)

    # Verify binder populated FTS fields
    assert bound.fts_table == "test_docs__content__fts"
    assert bound.pk == "id"
    assert bound.fts_query == "test query"

    # Plan (adds rank projection)
    dialect = SQLiteDialect()
    planned, _ = plan_select(bound, schema, dialect)

    # Verify planner added rank expression
    assert planned.fts_rank_expr is not None
    assert "bm25" in planned.fts_rank_expr or "rank" in planned.fts_rank_expr

    # Generate SQL
    sql, params = generate_select(planned, dialect)

    # Critical assertions:
    # 1. Two MATCH ? params (one for rank subquery, one for EXISTS)
    assert sql.count("MATCH ?") == 2, f"Expected 2 MATCH ? clauses, SQL: {sql}"

    # 2. Both params should be "test query" in correct order
    # Order: [fts_query for rank, fts_query for EXISTS]
    assert params == ["test query", "test query"], \
        f"Expected ['test query', 'test query'], got {params}"

    # 3. SQL structure check: rank subquery should appear before WHERE EXISTS
    rank_pos = sql.find("(SELECT ")
    exists_pos = sql.find("EXISTS")
    assert rank_pos < exists_pos, \
        "Rank subquery should appear in SELECT before EXISTS in WHERE"


def test_fts_param_order_without_rank():
    """Verify single MATCH param when rank projection not requested."""
    schema = Schema()
    schema.register_table(TestTable)

    # Create IR with KeywordFTS but no rank projection
    ir = SelectIR(
        table="test_docs",
        columns=["id"],
        where=KeywordFTS(Col("content"), "test query"),
        order=[],
        limit=10,
    )

    bound = bind_select(ir, schema)

    # Manually set fts_rank_expr to None to simulate no rank request
    bound = SelectIR(
        table=bound.table,
        columns=bound.columns,
        where=bound.where,
        order=bound.order,
        limit=bound.limit,
        fts_rank_expr=None,  # No rank projection
        pk=bound.pk,
        fts_table=bound.fts_table,
        fts_query=bound.fts_query,
    )

    dialect = SQLiteDialect()
    sql, params = generate_select(bound, dialect)

    # Should have only 1 MATCH param (for EXISTS filter)
    assert sql.count("MATCH ?") == 1, f"Expected 1 MATCH ? clause, SQL: {sql}"
    assert params == ["test query"], f"Expected ['test query'], got {params}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
