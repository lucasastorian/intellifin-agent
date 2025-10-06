"""Schema-aware query binding and normalization."""
from .ir import *
from typing import Dict, Tuple


def _field_meta(schema, table: str, col: str) -> Dict:
    """Resolve field metadata, including view field resolution."""
    is_view = table in schema.views
    t = schema.view(table) if is_view else schema.table(table)

    if is_view:
        t._schema = schema
        type_map = t.type_map(schema)
        fd = type_map.get(col)
        if not fd:
            raise ValueError(f"Unknown column '{col}' on view '{table}'")

        view_field = t.get_fields()[col]
        base_table = getattr(view_field, "_view_src_table", table)
        base_col = getattr(view_field, "_view_src_field", col)

        base_table_cls = schema.get_table(base_table)
        pk = None
        for name, f in base_table_cls.get_fields().items():
            if getattr(f, "primary_key", False):
                pk = name
                break
    else:
        fields = t.get_fields()
        fd = fields.get(col)
        if not fd:
            raise ValueError(f"Unknown column '{col}' on table '{table}'")
        base_table = table
        base_col = col

        pk = None
        for name, f in t.get_fields().items():
            if getattr(f, "primary_key", False):
                pk = name
                break

    return {
        "is_boolean": getattr(fd, "python_type", None) is bool or getattr(fd, "boolean", False),
        "is_json": getattr(fd, "json", False),
        "is_fts": getattr(fd, "fts", False),
        "pk": pk,
        "base_table": base_table,
        "base_col": base_col,
        "fd": fd,
    }


def _normalize_bool(meta: Dict, lit: Lit) -> Lit:
    """Normalize boolean literals to 0/1 for SQLite."""
    if meta["is_boolean"]:
        v = lit.value
        if isinstance(v, bool):
            return Lit(1 if v else 0)
        if v is None:
            return lit
    return lit


def _merge_fts_info(dicts: tuple) -> Dict:
    """Merge FTS metadata from multiple predicates."""
    out = {}
    for d in dicts:
        out.update(d)
    return out


def bind_pred(schema, table: str, pred: Pred) -> Tuple[Pred, Dict]:
    """Bind a predicate: resolve columns, normalize values, extract FTS metadata."""
    if isinstance(pred, And):
        results = [bind_pred(schema, table, p) for p in pred.parts]
        parts, fts_infos = zip(*results) if results else ([], [])
        return And(list(parts)), _merge_fts_info(fts_infos)

    if isinstance(pred, Or):
        results = [bind_pred(schema, table, p) for p in pred.parts]
        parts, fts_infos = zip(*results) if results else ([], [])
        return Or(list(parts)), _merge_fts_info(fts_infos)

    col = pred.col.name
    meta = _field_meta(schema, table, col)
    base_col = meta["base_col"]
    is_view = table in schema.views
    # For views, keep the view column name; for tables, use base_col
    resolved_col = col if is_view else base_col

    if isinstance(pred, (Eq, Ne, Gt, Ge, Lt, Le)):
        lit = _normalize_bool(meta, pred.val)
        return type(pred)(Col(resolved_col), lit), {}

    elif isinstance(pred, (In_, Nin)):
        vals = [_normalize_bool(meta, v) for v in pred.vals]
        return type(pred)(Col(resolved_col), vals), {}

    elif isinstance(pred, Ilike):
        return Ilike(Col(resolved_col), pred.pattern), {}

    elif isinstance(pred, Regex):
        return Regex(Col(resolved_col), pred.pattern), {}

    elif isinstance(pred, ContainsJSON):
        if not meta["is_json"]:
            raise ValueError(f"Column '{col}' is not JSON-enabled on '{table}'")
        return ContainsJSON(Col(resolved_col), pred.vals), {}

    elif isinstance(pred, KeywordFTS):
        if not meta["is_fts"]:
            raise ValueError(f"Column '{col}' is not FTS-enabled on '{table}'")
        return KeywordFTS(Col(resolved_col), pred.query), {
            "fts": True,
            "fts_table": f"{meta['base_table']}__{base_col}__fts",
            "fts_col": base_col,
            "pk": meta["pk"],
            "fts_query": pred.query,
        }

    return pred, {}


def bind_select(ir: SelectIR, schema) -> SelectIR:
    """Bind a SELECT IR: resolve fields, normalize predicates."""
    if ir.where is None:
        return ir

    bound_where, fts_info = bind_pred(schema, ir.table, ir.where)

    return SelectIR(
        table=ir.table,
        columns=ir.columns,
        where=bound_where,
        order=ir.order,
        limit=ir.limit,
        fts_rank_expr=None,
        pk=fts_info.get("pk"),
        fts_table=fts_info.get("fts_table"),
        fts_query=fts_info.get("fts_query"),
    )


def bind_update(ir: UpdateIR, schema) -> UpdateIR:
    """Bind an UPDATE IR."""
    if ir.where is None:
        return ir
    bound_where, _ = bind_pred(schema, ir.table, ir.where)
    return UpdateIR(table=ir.table, assign=ir.assign, where=bound_where)


def bind_delete(ir: DeleteIR, schema) -> DeleteIR:
    """Bind a DELETE IR."""
    if ir.where is None:
        return ir
    bound_where, _ = bind_pred(schema, ir.table, ir.where)
    return DeleteIR(table=ir.table, where=bound_where)
