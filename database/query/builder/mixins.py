"""Fluent builder mixins for query construction."""
from typing import List, Any, Optional
from ..ir import *


class PredMixin:
    """Mixin providing predicate builder methods."""

    _pred: Optional[Pred] = None

    def _and(self, p: Pred):
        """Add a predicate with AND logic."""
        self._pred = p if self._pred is None else And([self._pred, p])
        return self

    def eq(self, field: str, value: Any):
        """field = value"""
        return self._and(Eq(Col(field), Lit(value)))

    def neq(self, field: str, value: Any):
        """field != value"""
        return self._and(Ne(Col(field), Lit(value)))

    def gt(self, field: str, value: Any):
        """field > value"""
        if value is None:
            return self
        return self._and(Gt(Col(field), Lit(value)))

    def gte(self, field: str, value: Any):
        """field >= value"""
        if value is None:
            return self
        return self._and(Ge(Col(field), Lit(value)))

    def lt(self, field: str, value: Any):
        """field < value"""
        if value is None:
            return self
        return self._and(Lt(Col(field), Lit(value)))

    def lte(self, field: str, value: Any):
        """field <= value"""
        if value is None:
            return self
        return self._and(Le(Col(field), Lit(value)))

    def in_(self, field: str, values: List[Any]):
        """field IN (values)"""
        return self._and(In_(Col(field), [Lit(x) for x in values]))

    def not_in(self, field: str, values: List[Any]):
        """field NOT IN (values)"""
        return self._and(Nin(Col(field), [Lit(x) for x in values]))

    def is_null(self, field: str):
        """field IS NULL"""
        return self._and(Eq(Col(field), Lit(None)))

    def is_not_null(self, field: str):
        """field IS NOT NULL"""
        return self._and(Ne(Col(field), Lit(None)))

    def between(self, field: str, lower: Any, upper: Any):
        """field BETWEEN lower AND upper (accepts open-ended ranges with None)"""
        preds = []
        if lower is not None:
            preds.append(Ge(Col(field), Lit(lower)))
        if upper is not None:
            preds.append(Le(Col(field), Lit(upper)))
        if not preds:
            return self
        return self._and(And(preds) if len(preds) > 1 else preds[0])

    def contains(self, field: str, value: Any):
        """JSON array contains value or JSON object has key"""
        if isinstance(value, (list, tuple)):
            return self._and(ContainsJSON(Col(field), [Lit(x) for x in value]))
        return self._and(ContainsJSON(Col(field), Lit(value)))

    def ilike(self, field: str, pattern: str):
        """Case-insensitive LIKE"""
        if not any(ch in pattern for ch in "%_"):
            pattern = f"%{pattern}%"
        return self._and(Ilike(Col(field), pattern))

    def regex(self, field: str, pattern: str):
        """REGEXP pattern matching"""
        return self._and(Regex(Col(field), pattern))


class SelectMixin:
    """Mixin providing SELECT-specific builder methods."""

    selected: Optional[List[str]] = None
    order_by: List[tuple] = []
    limit_n: Optional[int] = None

    def select(self, csv: str):
        """Specify columns to select (comma-separated)."""
        self.selected = [s.strip() for s in csv.split(",")]
        return self

    def order(self, field: str, desc: bool = False):
        """Add ordering by field."""
        self.order_by.append((field, desc))
        return self

    def limit(self, n: int):
        """Limit result count."""
        self.limit_n = n
        return self
