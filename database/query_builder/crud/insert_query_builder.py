from typing import Dict, Any, List, Union

from ...results import DBResult
from ..core.base import BaseQueryBuilder


class InsertQueryBuilder(BaseQueryBuilder):
    def __init__(self, database, schema, table: str, data: Union[Dict[str, Any], List[Dict[str, Any]]] = None):
        super().__init__(database, schema, table)
        self.data = data

    def execute(self):
        ids = self.database._insert(
            table=self.table.__tablename__,
            data=self.data
        )
        return DBResult[List[int]](data=ids if isinstance(ids, list) else [ids])
