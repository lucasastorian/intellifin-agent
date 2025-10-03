from typing import Dict, Any, List, Union

from ..core.base import BaseQueryBuilder


class InsertQueryBuilder(BaseQueryBuilder):
    """Builder specifically for INSERT queries"""

    def __init__(self, database, schema, table: str, data: Union[Dict[str, Any], List[Dict[str, Any]]] = None):
        super().__init__(database, schema, table)
        self.data = data

    def execute(self) -> Union[int, List[int]]:
        """Execute INSERT query - database handles validation"""
        return self.database._insert(
            table=self.table.__tablename__,
            data=self.data
        )
