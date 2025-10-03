
from typing import Dict, List, Union, Any, Optional

from ..crud.select_query_builder import SelectQueryBuilder
from ..crud.insert_query_builder import InsertQueryBuilder
from ..crud.update_query_builder import UpdateQueryBuilder
from ..crud.delete_query_builder import DeleteQueryBuilder
from ..crud.upsert_query_builder import UpsertQueryBuilder


class TableQueryBuilder:
    """Entry point for all query operations on a table"""

    def __init__(self, database, schema, table: str):
        self.database = database
        self.schema = schema
        self.table_name = table
        self.table = schema.table(table)

    def select(self, fields: str = '*') -> SelectQueryBuilder:
        """Start a SELECT query"""
        builder = SelectQueryBuilder(
            self.database, self.schema, self.table_name
        )
        return builder.select(fields)

    def insert(self, data: Union[Dict[str, Any], List[Dict[str, Any]]]) -> InsertQueryBuilder:
        """Start an INSERT query"""
        return InsertQueryBuilder(
            self.database, self.schema, self.table_name, data=data
        )

    def update(self, data: Dict[str, Any]) -> UpdateQueryBuilder:
        """Start an UPDATE query"""
        return UpdateQueryBuilder(
            self.database, self.schema, self.table_name, data
        )

    def delete(self) -> DeleteQueryBuilder:
        """Start a DELETE query"""
        return DeleteQueryBuilder(
            self.database, self.schema, self.table_name
        )

    def upsert(
        self,
        values: Union[Dict[str, Any], List[Dict[str, Any]]],
        on_conflict: Union[str, List[str]],
        ignore_duplicates: bool = False,
        returning: str = "representation",
        count: Optional[str] = None,
        default_to_null: bool = False,
    ) -> UpsertQueryBuilder:
        """Start an UPSERT query (INSERT ... ON CONFLICT ...)

        Args:
            values: Dict or list of dicts to insert/update
            on_conflict: Column name(s) with UNIQUE/PK constraint
            ignore_duplicates: True = DO NOTHING, False = DO UPDATE
            returning: "minimal" (no rows) or "representation" (full rows)
            count: "exact" to get affected row count
            default_to_null: For bulk, missing fields -> NULL vs DEFAULT
        """
        return UpsertQueryBuilder(
            self.database, self.schema, self.table_name,
            values=values,
            on_conflict=on_conflict,
            ignore_duplicates=ignore_duplicates,
            returning=returning,
            count=count,
            default_to_null=default_to_null,
        )

    # Filter pass-through methods for convenience (auto-creates SelectQueryBuilder)
    def eq(self, field: str, value: Any) -> SelectQueryBuilder:
        """Filter where field equals value (auto-starts SELECT)"""
        return self.select().eq(field, value)

    def neq(self, field: str, value: Any) -> SelectQueryBuilder:
        """Filter where field does not equal value (auto-starts SELECT)"""
        return self.select().neq(field, value)

    def gt(self, field: str, value: Union[int, float]) -> SelectQueryBuilder:
        """Filter where field is greater than value (auto-starts SELECT)"""
        return self.select().gt(field, value)

    def gte(self, field: str, value: Union[int, float]) -> SelectQueryBuilder:
        """Filter where field is >= value (auto-starts SELECT)"""
        return self.select().gte(field, value)

    def lt(self, field: str, value: Union[int, float]) -> SelectQueryBuilder:
        """Filter where field is less than value (auto-starts SELECT)"""
        return self.select().lt(field, value)

    def lte(self, field: str, value: Union[int, float]) -> SelectQueryBuilder:
        """Filter where field is <= value (auto-starts SELECT)"""
        return self.select().lte(field, value)

    def in_(self, field: str, values: List[Any]) -> SelectQueryBuilder:
        """Filter where field is in list (auto-starts SELECT)"""
        return self.select().in_(field, values)

    def not_in(self, field: str, values: List[Any]) -> SelectQueryBuilder:
        """Filter where field is not in list (auto-starts SELECT)"""
        return self.select().not_in(field, values)

    def contains(self, field: str, value: Any) -> SelectQueryBuilder:
        """Filter where JSON array contains value (auto-starts SELECT)"""
        return self.select().contains(field, value)

    def ilike(self, field: str, pattern: str) -> SelectQueryBuilder:
        """Case-insensitive LIKE filter (auto-starts SELECT)"""
        return self.select().ilike(field, pattern)

    def is_null(self, field: str) -> SelectQueryBuilder:
        """Filter where field is NULL (auto-starts SELECT)"""
        return self.select().is_null(field)

    def is_not_null(self, field: str) -> SelectQueryBuilder:
        """Filter where field is NOT NULL (auto-starts SELECT)"""
        return self.select().is_not_null(field)
