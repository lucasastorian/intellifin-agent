
from typing import List, Dict, Any

from ..core.base import BaseQueryBuilder
from ..mixins.filter_mixin import FilterMixin
from ..mixins.order_limit_mixin import OrderLimitMixin
from ..mixins.selection_mixin import SelectionMixin


class SelectQueryBuilder(BaseQueryBuilder, FilterMixin, OrderLimitMixin, SelectionMixin):
    """Builder specifically for SELECT queries"""

    def __init__(self, database, schema, table: str):
        super().__init__(database, schema, table)

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
