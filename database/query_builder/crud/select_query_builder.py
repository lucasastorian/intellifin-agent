
from typing import List, Dict, Any

from ..core.base import BaseQueryBuilder
from ..mixins.filter_mixin import FilterMixin
from ..mixins.order_limit_mixin import OrderLimitMixin
from ..mixins.selection_mixin import SelectionMixin


class SelectQueryBuilder(BaseQueryBuilder, FilterMixin, OrderLimitMixin, SelectionMixin):
    """Builder specifically for SELECT queries"""

    def __init__(self, database, schema, table: str):
        super().__init__(database, schema, table)
        self._search_applied = False

    def _assert_single_search(self):
        """Ensure only one text search per query and disable ordering"""
        if self._search_applied:
            raise ValueError("Only one text search per query (keyword_search or regex_search).")
        self._search_applied = True
        # Clear any pre-set ordering and disable future ordering
        self._order_by = None
        self._order_desc = False
        def _no_order(*args, **kwargs):
            raise ValueError("Ordering not supported for text search; results rank automatically.")
        self.order = _no_order  # instance-level override

    def keyword_search(self, field: str, text: str) -> "SelectQueryBuilder":
        """Full-text search using FTS5 with BM25 ranking.

        Args:
            field: Column name with fts=True
            text: Search text (will be split into bag-of-words AND query)

        Returns:
            Self for chaining

        Raises:
            ValueError: If field is not FTS-enabled or multiple searches attempted
        """
        # Validate field exists and FTS-enabled up front (clear error early)
        fields = self.table.get_fields()
        if field not in fields or not getattr(fields[field], "fts", False):
            raise ValueError(f"Column '{field}' on '{self.table.__tablename__}' is not FTS-enabled (fts=True).")
        self._assert_single_search()
        self.mongo_filters.setdefault(field, {}).update({"$keyword": text})
        return self

    def regex_search(self, field: str, pattern: str) -> "SelectQueryBuilder":
        """Pattern search using REGEXP, GLOB, or LIKE fallback.

        Args:
            field: Column name
            pattern: Regular expression pattern

        Returns:
            Self for chaining

        Raises:
            ValueError: If field doesn't exist or multiple searches attempted
        """
        # Allow regex on any TEXT column; no FTS requirement
        if field not in self.table.get_fields():
            raise ValueError(f"Unknown column '{field}' on '{self.table.__tablename__}'.")
        self._assert_single_search()
        self.mongo_filters.setdefault(field, {}).update({"$regex": pattern})
        return self

    def execute(self) -> List[Dict[str, Any]]:
        """Execute SELECT query"""
        projection = self._build_projection()
        sort_list = []
        if self._order_by is not None:
            sort_list = [(self._order_by, -1 if self._order_desc else 1)]

        return self.database._query(
            table=self.table.__tablename__,
            find=self.mongo_filters,
            projection=projection,
            sort=sort_list,
            limit=self._limit
        )

    def single(self) -> Dict[str, Any]:
        """Execute query and return a single row.

        Automatically applies LIMIT 1 and returns the first row.
        Raises LookupError if no rows are found.

        Returns:
            Single row as dictionary

        Raises:
            LookupError: If no rows match the query
        """
        rows = self.limit(1).execute()
        if not rows:
            raise LookupError(f"No rows found in table '{self.table.__tablename__}' matching the query")
        return rows[0]
