
from typing import Dict, List, Union, Any, Optional

from ..crud.select_query_builder import SelectQueryBuilder
from ..crud.insert_query_builder import InsertQueryBuilder
from ..crud.update_query_builder import UpdateQueryBuilder
from ..crud.delete_query_builder import DeleteQueryBuilder
from ..crud.upsert_query_builder import UpsertQueryBuilder


class TableQueryBuilder:
    """Entry point for all query operations on a table or view"""

    def __init__(self, database, schema, table: str):
        self.database = database
        self.schema = schema
        self.table_name = table
        self.is_view = table in schema.views
        self.table = schema.view(table) if self.is_view else schema.table(table)

    def select(self, fields: str = '*') -> SelectQueryBuilder:
        """Start a SELECT query"""
        builder = SelectQueryBuilder(
            self.database, self.schema, self.table_name
        )
        return builder.select(fields)

    def insert(self, data: Union[Dict[str, Any], List[Dict[str, Any]]]) -> InsertQueryBuilder:
        """Start an INSERT query"""
        if self.is_view:
            raise ValueError(f"Cannot INSERT into view '{self.table_name}'.")
        return InsertQueryBuilder(
            self.database, self.schema, self.table_name, data=data
        )

    def update(self, data: Dict[str, Any]) -> UpdateQueryBuilder:
        """Start an UPDATE query"""
        if self.is_view:
            raise ValueError(f"Cannot UPDATE view '{self.table_name}'.")
        return UpdateQueryBuilder(
            self.database, self.schema, self.table_name, data
        )

    def delete(self) -> DeleteQueryBuilder:
        """Start a DELETE query"""
        if self.is_view:
            raise ValueError(f"Cannot DELETE from view '{self.table_name}'.")
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
        if self.is_view:
            raise ValueError(f"Cannot UPSERT into view '{self.table_name}'.")
        return UpsertQueryBuilder(
            self.database, self.schema, self.table_name,
            values=values,
            on_conflict=on_conflict,
            ignore_duplicates=ignore_duplicates,
            returning=returning,
            count=count,
            default_to_null=default_to_null,
        )

    def keyword_search(self, query: str, column: Optional[str] = None, returning: Optional[str] = None) -> SelectQueryBuilder:
        """Full-text search using FTS5 with BM25 ranking.

        Automatically detects the FTS-enabled column if only one exists.
        If multiple FTS columns exist, you must specify which one via 'column' parameter.

        Args:
            query: Search text (will be split into bag-of-words AND query)
            column: Optional column name to search (required if table has multiple FTS columns)
            returning: Optional column selection (e.g., "id,content" or "*"). Defaults to "*"

        Returns:
            SelectQueryBuilder for chaining

        Raises:
            ValueError: If no FTS columns exist, multiple FTS columns exist without specifying 'column',
                       or specified column is not FTS-enabled

        Examples:
            # Auto-detect FTS column (table has only one)
            db.table("filing_pages").keyword_search("risk factors").execute()

            # Specify column when multiple FTS columns exist
            db.table("notes").keyword_search("revenue", column="content").execute()

            # Control returned columns
            db.table("filing_pages").keyword_search("merger", returning="id,filing_id").execute()
        """
        # For views, resolve to underlying FTS-enabled fields
        if self.is_view:
            self.table._schema = self.schema
            type_map = self.table.type_map(self.schema)
            fts_columns = [alias for alias, fd in type_map.items() if getattr(fd, "fts", False)]
        else:
            # Find all FTS-enabled columns
            fields = self.table.get_fields()
            fts_columns = [name for name, field in fields.items() if getattr(field, "fts", False)]

        # Determine which column to search
        if column:
            # User specified a column - validate it's FTS-enabled
            if column not in fts_columns:
                if column in fields:
                    raise ValueError(
                        f"Column '{column}' on table '{self.table_name}' is not FTS-enabled. "
                        f"FTS columns: {fts_columns}"
                    )
                else:
                    raise ValueError(f"Column '{column}' does not exist on table '{self.table_name}'.")
            search_column = column
        else:
            # Auto-detect FTS column
            if not fts_columns:
                raise ValueError(f"Table '{self.table_name}' has no FTS-enabled columns.")
            elif len(fts_columns) > 1:
                raise ValueError(
                    f"Table '{self.table_name}' has multiple FTS-enabled columns: {fts_columns}. "
                    f"Please specify which column to search using the 'column' parameter."
                )
            search_column = fts_columns[0]

        # Create SelectQueryBuilder with column selection
        if returning:
            builder = self.select(returning)
        else:
            builder = self.select()

        builder._search_applied = True
        # Clear any pre-set ordering and disable future ordering
        builder._order_by = None
        builder._order_desc = False
        def _no_order(*args, **kwargs):
            raise ValueError("Ordering not supported for text search; results rank automatically.")
        builder.order = _no_order  # instance-level override
        builder.mongo_filters.setdefault(search_column, {}).update({"$keyword": query})
        return builder

    def regex_search(self, pattern: str, column: str, returning: Optional[str] = None) -> SelectQueryBuilder:
        """Pattern search using REGEXP, GLOB, or LIKE fallback.

        Args:
            pattern: Regular expression pattern
            column: Column name to search
            returning: Optional column selection (e.g., "id,content" or "*"). Defaults to "*"

        Returns:
            SelectQueryBuilder for chaining

        Raises:
            ValueError: If column doesn't exist

        Examples:
            db.table("filing_pages").regex_search("risk.*factor", column="content").execute()
            db.table("notes").regex_search("merger|acquisition", column="title", returning="id,title").execute()
        """
        # Allow regex on any TEXT column; no FTS requirement
        # Works on both tables and views
        if column not in self.table.get_fields():
            raise ValueError(f"Column '{column}' does not exist on {'view' if self.is_view else 'table'} '{self.table_name}'.")

        # Create SelectQueryBuilder with column selection
        if returning:
            builder = self.select(returning)
        else:
            builder = self.select()

        builder._search_applied = True
        # Clear any pre-set ordering and disable future ordering
        builder._order_by = None
        builder._order_desc = False
        def _no_order(*args, **kwargs):
            raise ValueError("Ordering not supported for text search; results rank automatically.")
        builder.order = _no_order  # instance-level override
        builder.mongo_filters.setdefault(column, {}).update({"$regex": pattern})
        return builder
