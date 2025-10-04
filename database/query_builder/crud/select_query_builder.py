
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
        self._count_mode = False


    def count(self) -> "SelectQueryBuilder":
        """Return count of matching rows instead of the rows themselves.

        Returns:
            Self for chaining (call execute() to get the count)
        """
        self._count_mode = True
        return self

    def execute(self, *args, **kwargs):
        """Execute SELECT query

        Returns:
            List[Dict[str, Any]] if not in count mode, otherwise int (count)

        Raises:
            TypeError: If any arguments are passed
        """
        if args or kwargs:
            raise TypeError(
                "execute() takes no arguments. "
                "To specify columns, use .select('columns') before calling execute(). "
                "For keyword_search, use the 'returning' parameter: "
                ".keyword_search('query', returning='id,content').execute()"
            )

        if self._count_mode:
            # Count mode: return integer count
            return self.database._count(
                table=self.table.__tablename__,
                find=self.mongo_filters
            )

        # Normal mode: return rows
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
