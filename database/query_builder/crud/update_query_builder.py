
from typing import Dict, Any

from ...results import DBResult
from ..core.base import BaseQueryBuilder
from ..mixins.filter_mixin import FilterMixin


class UpdateQueryBuilder(BaseQueryBuilder, FilterMixin):
    def __init__(self, database, schema, table: str, data: Dict[str, Any] = None):
        super().__init__(database, schema, table)
        self.data = data

    def execute(self):
        affected = self.database._update(
            table=self.table.__tablename__,
            data=self.data,
            filters=self.mongo_filters
        )
        return DBResult[int](data=affected)
