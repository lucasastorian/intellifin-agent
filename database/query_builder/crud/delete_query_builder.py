
from ...results import DBResult
from ..core.base import BaseQueryBuilder
from ..mixins.filter_mixin import FilterMixin


class DeleteQueryBuilder(BaseQueryBuilder, FilterMixin):
    def execute(self):
        if not self.mongo_filters:
            raise ValueError("DELETE requires at least one filter to prevent accidental deletion of all records. "
                             "Use .eq(), .gt(), or other filter methods before .execute()")

        affected = self.database._delete(
            table=self.table.__tablename__,
            filters=self.mongo_filters
        )
        return DBResult[int](data=affected)

