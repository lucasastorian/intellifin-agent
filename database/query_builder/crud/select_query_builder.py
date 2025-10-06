
from typing import List, Dict, Any

from ...results import DBResult
from ..core.base import BaseQueryBuilder
from ..mixins.filter_mixin import FilterMixin
from ..mixins.order_limit_mixin import OrderLimitMixin
from ..mixins.selection_mixin import SelectionMixin


class SelectQueryBuilder(BaseQueryBuilder, FilterMixin, OrderLimitMixin, SelectionMixin):
    def __init__(self, database, schema, table: str):
        super().__init__(database, schema, table)
        self._search_applied = False

    def count(self) -> DBResult[int]:
        n = self.database._count(
            table=self.table.__tablename__,
            find=self.mongo_filters
        )
        return DBResult[int](data=n, score=None)

    def single(self) -> DBResult[dict]:
        projection = self._build_projection()
        sort_list = []
        if self._order_by is not None:
            sort_list = [(self._order_by, -1 if self._order_desc else 1)]

        rows = self.database._query(
            table=self.table.__tablename__,
            find=self.mongo_filters,
            projection=projection,
            sort=sort_list,
            limit=1
        )

        if not rows:
            raise LookupError(f"No rows found in table '{self.table.__tablename__}' matching the query")

        row = rows[0]
        score = float(row.pop("_score")) if "_score" in row else None
        return DBResult[dict](data=row, score=score)

    def execute(self, *args, **kwargs) -> DBResult[List[dict]]:
        if args or kwargs:
            raise TypeError(
                "execute() takes no arguments. "
                "To specify columns, use .select('columns') before calling execute(). "
                "For keyword_search, use the 'returning' parameter: "
                ".keyword_search('query', returning='id,content').execute()"
            )

        projection = self._build_projection()
        sort_list = []
        if self._order_by is not None:
            sort_list = [(self._order_by, -1 if self._order_desc else 1)]

        rows = self.database._query(
            table=self.table.__tablename__,
            find=self.mongo_filters,
            projection=projection,
            sort=sort_list,
            limit=self._limit
        )

        scores = None
        if rows and "_score" in rows[0]:
            scores = [float(r.pop("_score")) for r in rows]

        return DBResult[List[dict]](data=rows, score=scores)
