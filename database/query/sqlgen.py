"""Pure SQL + params generation from IR."""
from .ir import *
from typing import List, Tuple

# SQLite max variables per statement (typical default)
MAX_VARIABLES = 999
# Chunk IN predicates to stay under limit
IN_CHUNK_SIZE = 500


class SQLGen:
    """Stateful SQL generator with parameter collection."""

    def __init__(self, dialect, ctx=None):
        self.d = dialect
        self.params: List[Any] = []
        self.ctx = ctx or {}

    def col(self, c: Col, table_alias: str = "t") -> str:
        """Generate column reference."""
        if not table_alias:
            # Unqualified column (for UPDATE/DELETE without alias)
            return self.d.q(c.name)
        return f"{table_alias}.{self.d.q(c.name)}"

    def lit(self, v: Any) -> str:
        """Generate parameterized literal."""
        self.params.append(v)
        return "?"

    def pred(self, p: Pred, table_alias: str = "t") -> str:
        """Generate predicate SQL."""
        if isinstance(p, Eq):
            if p.val.value is None:
                return f"{self.col(p.col, table_alias)} IS NULL"
            return f"{self.col(p.col, table_alias)} = {self.lit(p.val.value)}"

        if isinstance(p, Ne):
            if p.val.value is None:
                return f"{self.col(p.col, table_alias)} IS NOT NULL"
            return f"{self.col(p.col, table_alias)} <> {self.lit(p.val.value)}"

        if isinstance(p, Gt):
            return f"{self.col(p.col, table_alias)} > {self.lit(p.val.value)}"
        if isinstance(p, Ge):
            return f"{self.col(p.col, table_alias)} >= {self.lit(p.val.value)}"
        if isinstance(p, Lt):
            return f"{self.col(p.col, table_alias)} < {self.lit(p.val.value)}"
        if isinstance(p, Le):
            return f"{self.col(p.col, table_alias)} <= {self.lit(p.val.value)}"

        if isinstance(p, In_):
            if not p.vals:
                return "1=0"

            # Chunk large IN lists to avoid SQLite variable limit
            if len(p.vals) > IN_CHUNK_SIZE:
                chunks = []
                for i in range(0, len(p.vals), IN_CHUNK_SIZE):
                    chunk_vals = p.vals[i:i + IN_CHUNK_SIZE]
                    qs = ", ".join(self.lit(v.value) for v in chunk_vals)
                    chunks.append(f"{self.col(p.col, table_alias)} IN ({qs})")
                return "(" + " OR ".join(chunks) + ")"

            qs = ", ".join(self.lit(v.value) for v in p.vals)
            return f"{self.col(p.col, table_alias)} IN ({qs})"

        if isinstance(p, Nin):
            if not p.vals:
                return "1=1"

            # Chunk large NOT IN lists to avoid SQLite variable limit
            if len(p.vals) > IN_CHUNK_SIZE:
                chunks = []
                for i in range(0, len(p.vals), IN_CHUNK_SIZE):
                    chunk_vals = p.vals[i:i + IN_CHUNK_SIZE]
                    qs = ", ".join(self.lit(v.value) for v in chunk_vals)
                    chunks.append(f"{self.col(p.col, table_alias)} NOT IN ({qs})")
                return "(" + " AND ".join(chunks) + ")"

            qs = ", ".join(self.lit(v.value) for v in p.vals)
            return f"{self.col(p.col, table_alias)} NOT IN ({qs})"

        if isinstance(p, Ilike):
            return f"{self.col(p.col, table_alias)} LIKE {self.lit(p.pattern)} {self.d.like_ci()}"

        if isinstance(p, Regex):
            return f"{self.col(p.col, table_alias)} {self.d.regexp_fn()} {self.lit(p.pattern)}"

        if isinstance(p, ContainsJSON):
            col_ref = self.col(p.col, table_alias)

            # Single value: fast path with direct comparison
            if not isinstance(p.vals, list):
                # Generate separate params for array and object checks since we can't reuse placeholders
                val_param_array = self.lit(p.vals.value)
                val_param_object = self.lit(p.vals.value)
                # For arrays: check if value exists
                # For objects: check if key exists
                array_clause = (
                    f"(json_type({col_ref})='array' AND EXISTS ("
                    f"SELECT 1 FROM json_each({col_ref}) WHERE value = {val_param_array}))"
                )
                object_clause = (
                    f"(json_type({col_ref})='object' AND EXISTS ("
                    f"SELECT 1 FROM json_each({col_ref}) WHERE key = {val_param_object}))"
                )
                return f"({array_clause} OR {object_clause})"

            # Multiple values: use JSON array param to avoid N*M correlated subqueries
            import json
            vals_json = json.dumps([v.value for v in p.vals])
            # Need separate params for array and object clauses
            vals_param_array = self.lit(vals_json)
            vals_param_object = self.lit(vals_json)

            # Use a single EXISTS with json_each on both the column AND the param
            # Check if ANY value from our param list exists in the column
            array_clause = (
                f"(json_type({col_ref})='array' AND EXISTS ("
                f"SELECT 1 FROM json_each({col_ref}) c, json_each({vals_param_array}) p "
                f"WHERE c.value = p.value))"
            )
            object_clause = (
                f"(json_type({col_ref})='object' AND EXISTS ("
                f"SELECT 1 FROM json_each({col_ref}) c, json_each({vals_param_object}) p "
                f"WHERE c.key = p.value))"
            )
            return f"({array_clause} OR {object_clause})"

        if isinstance(p, KeywordFTS):
            fts_table = self.ctx.get("fts_table")
            pk = self.ctx.get("pk")
            if not fts_table or not pk:
                raise ValueError("FTS predicate missing planned fts_table/pk")
            return (
                f"EXISTS (SELECT 1 FROM {self.d.q(fts_table)} "
                f"WHERE rowid = {table_alias}.{self.d.q(pk)} "
                f"AND {self.d.q(fts_table)} MATCH {self.lit(p.query)})"
            )

        if isinstance(p, And):
            parts = [self.pred(x, table_alias) for x in p.parts]
            return "(" + " AND ".join(parts) + ")"

        if isinstance(p, Or):
            parts = [self.pred(x, table_alias) for x in p.parts]
            return "(" + " OR ".join(parts) + ")"

        raise TypeError(f"Unhandled predicate {type(p)}")


def generate_select(ir: SelectIR, dialect) -> Tuple[str, List[Any]]:
    """Generate SELECT SQL + params."""
    import sys
    g = SQLGen(dialect, ctx={"fts_table": ir.fts_table, "pk": ir.pk})

    # If we project a correlated rank subquery, add its MATCH param first
    if ir.fts_rank_expr and ir.fts_query is not None:
        g.params.append(ir.fts_query)

    # Debug logging
    # if ir.fts_table:
    #     print(f"[FTS DEBUG] fts_table={ir.fts_table}, fts_rank_expr={ir.fts_rank_expr}, order={ir.order}", file=sys.stderr)

    if ir.columns is None:
        if ir.fts_rank_expr:
            cols = f"t.*, {ir.fts_rank_expr} AS _rank"
        else:
            cols = "t.*"
    else:
        # Handle raw SQL expressions (aggregates, functions) vs regular columns
        col_parts = []
        for c in ir.columns:
            # Raw SQL expressions: functions (contains "("), qualified columns, or already quoted
            if "(" in c or "." in c or "`" in c:
                col_parts.append(c)
            else:
                col_parts.append(f"t.{dialect.q(c)}")
        cols = ", ".join(col_parts)
        if ir.fts_rank_expr:
            cols += f", {ir.fts_rank_expr} AS _rank"

    sql = [f"SELECT {cols} FROM {dialect.q(ir.table)} t"]

    if ir.where:
        sql.append("WHERE " + g.pred(ir.where))

    if ir.order:
        parts = []
        for name, desc in ir.order:
            # Raw SQL expressions: functions, FTS, qualified columns, CASE statements
            if "(" in name or "__fts" in name or "." in name or "`" in name or name.upper().startswith("CASE "):
                parts.append(f"{name} {'DESC' if desc else 'ASC'}")
            else:
                parts.append(f"t.{dialect.q(name)} {'DESC' if desc else 'ASC'}")
        sql.append("ORDER BY " + ", ".join(parts))
    elif ir.fts_rank_expr:
        # Default FTS ordering: rank ascending, then PK
        pk_col = f"t.{dialect.q(ir.pk)}" if ir.pk else "t.rowid"
        sql.append(f"ORDER BY _rank ASC, {pk_col} ASC")

    if ir.limit is not None:
        sql.append(f"LIMIT {int(ir.limit)}")

    final_sql = " ".join(sql) + ";"

    # Debug logging
    # if ir.fts_table:
    #     print(f"[FTS DEBUG SQL] {final_sql}", file=sys.stderr)
    #     print(f"[FTS DEBUG PARAMS] {g.params}", file=sys.stderr)

    return final_sql, g.params


def generate_insert(ir: InsertIR, dialect) -> Tuple[str, List[Any]]:
    """Generate INSERT SQL + params."""
    if not ir.rows:
        raise ValueError("Cannot INSERT zero rows")

    g = SQLGen(dialect)
    cols = list(ir.rows[0].keys())
    col_list = ", ".join(dialect.q(c) for c in cols)

    values_clauses = []
    for row in ir.rows:
        placeholders = ", ".join(g.lit(row[c]) for c in cols)
        values_clauses.append(f"({placeholders})")

    sql = f"INSERT INTO {dialect.q(ir.table)} ({col_list}) VALUES {', '.join(values_clauses)} RETURNING *;"
    return sql, g.params


def generate_update(ir: UpdateIR, dialect) -> Tuple[str, List[Any]]:
    """Generate UPDATE SQL + params.

    Note: SQLite UPDATE doesn't support table aliases in basic syntax.
    Use unqualified column names in WHERE clause.
    """
    g = SQLGen(dialect)

    set_clauses = [f"{dialect.q(col)} = {g.lit(val)}" for col, val in ir.assign.items()]
    sql = [f"UPDATE {dialect.q(ir.table)} SET {', '.join(set_clauses)}"]

    if ir.where:
        # Don't qualify columns - UPDATE doesn't have table alias
        sql.append("WHERE " + g.pred(ir.where, table_alias=""))

    sql.append("RETURNING *")

    return " ".join(sql) + ";", g.params


def generate_delete(ir: DeleteIR, dialect) -> Tuple[str, List[Any]]:
    """Generate DELETE SQL + params.

    Note: SQLite DELETE doesn't support table aliases in basic syntax.
    Use unqualified column names in WHERE clause.
    """
    g = SQLGen(dialect)
    sql = [f"DELETE FROM {dialect.q(ir.table)}"]

    if ir.where:
        # Don't qualify columns - DELETE doesn't have table alias
        sql.append("WHERE " + g.pred(ir.where, table_alias=""))

    sql.append("RETURNING *")

    return " ".join(sql) + ";", g.params


def generate_upsert(ir: UpsertIR, dialect) -> Tuple[str, List[Any]]:
    """Generate UPSERT (INSERT ... ON CONFLICT) SQL + params."""
    if not ir.rows:
        raise ValueError("Cannot UPSERT zero rows")

    g = SQLGen(dialect)
    cols = list(ir.rows[0].keys())
    col_list = ", ".join(dialect.q(c) for c in cols)

    values_clauses = []
    for row in ir.rows:
        placeholders = ", ".join(g.lit(row[c]) for c in cols)
        values_clauses.append(f"({placeholders})")

    sql = [f"INSERT INTO {dialect.q(ir.table)} ({col_list}) VALUES {', '.join(values_clauses)}"]

    conflict_cols = ", ".join(dialect.q(c) for c in ir.on_conflict)
    sql.append(f"ON CONFLICT ({conflict_cols})")

    if ir.do_nothing:
        sql.append("DO NOTHING")
    else:
        update_clauses = [
            f"{dialect.q(c)} = excluded.{dialect.q(c)}"
            for c in cols
            if c not in ir.on_conflict
        ]
        if update_clauses:
            sql.append(f"DO UPDATE SET {', '.join(update_clauses)}")
        else:
            sql.append("DO NOTHING")

    if ir.returning_all:
        sql.append("RETURNING *")

    return " ".join(sql) + ";", g.params
