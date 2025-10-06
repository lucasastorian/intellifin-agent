from typing import Any, Dict, List, Union, Optional
from ...results import DBResult
from ..core.base import BaseQueryBuilder


class UpsertQueryBuilder(BaseQueryBuilder):
    def __init__(
        self, database, schema, table: str,
        values: Union[Dict[str, Any], List[Dict[str, Any]]],
        on_conflict: Union[str, List[str]],
        ignore_duplicates: bool = False,
        returning: str = "representation",
        count: Optional[str] = None,
        default_to_null: bool = False,
    ):
        super().__init__(database, schema, table)
        self.values = values
        self.on_conflict = on_conflict
        self.ignore_duplicates = ignore_duplicates
        self.returning = returning
        self.count = count
        self.default_to_null = default_to_null

    def execute(self):
        out = self.database._upsert(
            table=self.table.__tablename__,
            values=self.values,
            on_conflict=self.on_conflict,
            ignore_duplicates=self.ignore_duplicates,
            returning=self.returning,
            count=self.count,
            default_to_null=self.default_to_null,
        )
        return DBResult[List[dict]](data=out.get("data") or [])
