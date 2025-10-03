
from ..core.base import BaseQueryBuilder
from ..mixins.filter_mixin import FilterMixin


class DeleteQueryBuilder(BaseQueryBuilder, FilterMixin):
    """Builder specifically for DELETE queries"""

    def execute(self) -> int:
        """Execute DELETE query"""
        # Safety check - prevent accidental deletion of all records
        if not self.mongo_filters:
            raise ValueError("DELETE requires at least one filter to prevent accidental deletion of all records. "
                             "Use .eq(), .gt(), or other filter methods before .execute()")

        return self.database._delete(
            table=self.table.__tablename__,
            filters=self.mongo_filters
        )

