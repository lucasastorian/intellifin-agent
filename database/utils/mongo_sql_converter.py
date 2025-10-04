from typing import Dict, Any, Optional, List, Union

from ..schema.schema import Schema
from ..schema.fields import Boolean


class MongoToSqlConverter:
    """
    Class to convert MongoDB-style find queries to SQL SELECT statements.

    Supports:
    - Basic filtering with comparison operators
    - Projection, sorting, limit
    """

    def __init__(self, schema: Schema):
        self.schema = schema
        self._fts_rank_exprs = []  # list of (field, expr_sql, param)
        self._order_rank_forced = False

    def _quote_identifier(self, identifier: str) -> str:
        """Quote SQL identifier with backticks for safety"""
        return f"`{identifier}`"

    def convert(self, mongo_obj: dict) -> str:
        """
        Convert MongoDB query to SQL SELECT
        """
        # Check if this is an insert operation
        if "insert" in mongo_obj:
            return self.convert_insert(mongo_obj)

        table = mongo_obj.get("collection")
        find_filter = mongo_obj.get("find", {})
        projection = mongo_obj.get("projection", {})
        sort_clause = mongo_obj.get("sort", [])
        limit_val = mongo_obj.get("limit")

        # Store current table for error messages
        self._current_table = table
        table_alias = "t"

        # Build columns
        main_columns = self._build_main_columns(projection, table_alias)

        # Build FROM clause
        from_clause = f"FROM {self._quote_identifier(table)} {table_alias}"

        # Build final query
        sql = f"SELECT {', '.join(main_columns)} {from_clause}"

        # Build WHERE clause with parameters
        where_sql, params = self.build_where_sql(find_filter, table_alias)
        self._last_select_params = params  # Store for get_select_params()
        if where_sql:
            sql += f" WHERE {where_sql}"

        # Add ORDER BY
        if sort_clause:
            order_sql = self.build_order_by_sql(sort_clause, table_alias)
            if order_sql:
                sql += f" ORDER BY {order_sql}"

        # Add LIMIT
        if limit_val is not None:
            limit_int = int(limit_val)
            if limit_int < 0:
                raise ValueError(f"LIMIT must be >= 0, got {limit_int}")
            sql += f" LIMIT {limit_int}"

        return sql + ";"

    def get_select_params(self, mongo_obj: dict) -> list:
        """Return parameters for the SELECT query"""
        return getattr(self, "_last_select_params", [])

    def convert_select(self, *, table: str, find: Dict[str, Any],
                       projection: Dict[str, int], sort: list, limit: Optional[int]) -> str:
        """Convert SELECT query with explicit parameters to SQL.

        Args:
            table: Table name
            find: Filter conditions
            projection: Field projection dict
            sort: Sort list [(field, direction), ...]
            limit: Maximum rows to return

        Returns:
            SQL SELECT statement
        """
        self._current_table = table
        self._fts_rank_exprs = []
        self._order_rank_forced = False
        table_alias = "t"

        # Build columns
        if projection:
            main_columns = [f"{table_alias}.`{f}`" for f in projection.keys()]
        else:
            main_columns = [f"{table_alias}.*"]

        sql = f"SELECT {', '.join(main_columns)} FROM `{table}` {table_alias}"

        # Build WHERE clause
        where_sql, params = self.build_where_sql(find, table_alias)
        self._last_select_params = params
        if where_sql:
            sql += f" WHERE {where_sql}"

        # Build ORDER BY (with BM25 ranking for FTS)
        if self._fts_rank_exprs:
            # Enforce ranking: use the last keyword search added
            _, rank_expr_sql, rank_param = self._fts_rank_exprs[-1]
            sql += f" ORDER BY {rank_expr_sql} ASC"
            self._order_rank_forced = True
            # Append bm25 param AFTER the WHERE params
            self._last_select_params.append(rank_param)
        elif sort:
            # Only apply ORDER BY when no keyword search exists
            order_sql = self.build_order_by_sql(sort, table_alias)
            if order_sql:
                sql += f" ORDER BY {order_sql}"

        # Add LIMIT
        if limit is not None:
            limit_int = int(limit)
            if limit_int < 0:
                raise ValueError(f"LIMIT must be >= 0, got {limit_int}")
            sql += f" LIMIT {limit_int}"

        return sql + ";"

    def get_last_select_params(self) -> list:
        """Return parameters for the last SELECT query"""
        return getattr(self, "_last_select_params", [])

    def _get_primary_key_field(self, table_name: str) -> str:
        """Get primary key field name for a table from schema"""
        table_cls = self.schema.get_table(table_name)

        # Find the primary key field
        for field_name, field_desc in table_cls.get_fields().items():
            if field_desc.primary_key:
                return field_name

        raise ValueError(f"No primary key field found for table '{table_name}'")

    def _build_main_columns(self, projection: Dict[str, int], table_alias: str) -> List[str]:
        """Build main table columns"""
        if projection:
            return [f"{table_alias}.`{field}`" for field in projection.keys()]
        else:
            return [f"{table_alias}.*"]

    def build_where_sql(self, find_filter: Dict[str, Any], table_alias: Optional[str] = None) -> tuple:
        """
        Convert a 'find' dict into parameterized SQL condition.
        Returns (sql_string, params_list)

        Supports:
          - direct equality: {field: value}
          - comparison operators: {field: {"$gt": val, ...}}
          - $in / $nin
          - $and / $or => combine subclauses
        """
        if not find_filter:
            return "", []

        if isinstance(find_filter, dict):
            if "$and" in find_filter:
                parts_params = [self.build_where_sql(sub, table_alias) for sub in find_filter["$and"]]
                parts = [p[0] for p in parts_params if p[0]]
                params = [p for pp in parts_params for p in pp[1]]
                sql = "(" + ") AND (".join(parts) + ")" if parts else ""
                return sql, params
            elif "$or" in find_filter:
                parts_params = [self.build_where_sql(sub, table_alias) for sub in find_filter["$or"]]
                parts = [p[0] for p in parts_params if p[0]]
                params = [p for pp in parts_params for p in pp[1]]
                sql = "(" + ") OR (".join(parts) + ")" if parts else ""
                return sql, params
            else:
                return self.build_basic_conditions(find_filter, table_alias)

        if isinstance(find_filter, list):
            parts_params = [self.build_where_sql(sub, table_alias) for sub in find_filter]
            parts = [p[0] for p in parts_params if p[0]]
            params = [p for pp in parts_params for p in pp[1]]
            sql = "(" + ") AND (".join(parts) + ")" if parts else ""
            return sql, params

        return "", []

    def build_basic_conditions(self, condition_dict: Dict[str, Any], table_alias: Optional[str] = None) -> tuple:
        """Build basic conditions with parameters. Returns (sql, params)"""
        clauses = []
        params = []

        for field, expr in condition_dict.items():
            sql_field = self._convert_field_name(field, table_alias)

            # Normalize boolean values for Boolean fields
            expr = self._normalize_boolean_value(field, expr)

            if isinstance(expr, dict):
                for op, val in expr.items():
                    if op == "$in":
                        if not val:
                            # Empty $in list matches nothing
                            clauses.append("1=0")
                        else:
                            placeholders = ", ".join("?" for _ in val)
                            clauses.append(f"{sql_field} IN ({placeholders})")
                            params.extend(val)

                    elif op == "$nin":
                        if not val:
                            # Empty $nin list matches everything
                            clauses.append("1=1")
                        else:
                            placeholders = ", ".join("?" for _ in val)
                            clauses.append(f"{sql_field} NOT IN ({placeholders})")
                            params.extend(val)

                    elif op == "$contains":
                        # Type-aware contains: arrays → check values, objects → check keys
                        if isinstance(val, (list, tuple)):
                            if not val:
                                # Empty list matches nothing
                                clauses.append("1=0")
                            else:
                                placeholders = ", ".join("?" for _ in val)

                                # ARRAY: check values
                                array_clause = (
                                    f"(json_type({sql_field}) = 'array' AND "
                                    f"EXISTS (SELECT 1 FROM json_each({sql_field}) WHERE value IN ({placeholders})))"
                                )

                                # OBJECT: check keys
                                object_clause = (
                                    f"(json_type({sql_field}) = 'object' AND "
                                    f"EXISTS (SELECT 1 FROM json_each({sql_field}) WHERE key IN ({placeholders})))"
                                )

                                clauses.append(f"({array_clause} OR {object_clause})")
                                params.extend(val)  # for array values
                                params.extend(val)  # for object keys
                        else:
                            # Scalar: check value for arrays, key for objects
                            array_clause = (
                                f"(json_type({sql_field}) = 'array' AND "
                                f"EXISTS (SELECT 1 FROM json_each({sql_field}) WHERE value = ?))"
                            )
                            object_clause = (
                                f"(json_type({sql_field}) = 'object' AND "
                                f"EXISTS (SELECT 1 FROM json_each({sql_field}) WHERE key = ?))"
                            )
                            clauses.append(f"({array_clause} OR {object_clause})")
                            params.append(val)  # for array value
                            params.append(val)  # for object key

                    elif op == "$ilike":
                        # Case-insensitive LIKE with COLLATE NOCASE (index-friendly for prefix queries)
                        clauses.append(f"{sql_field} LIKE ? COLLATE NOCASE")
                        params.append(val)

                    elif op == "$keyword":
                        # Validate FTS-enabled
                        tbl = self.schema.get_table(self._current_table)
                        fields = tbl.get_fields()
                        fd = fields.get(field)
                        if not getattr(fd, "fts", False):
                            raise ValueError(f"Field '{field}' is not FTS-enabled (fts=True) on '{tbl.__tablename__}'")

                        # Normalize to bag-of-words AND semantics
                        match = " ".join(str(val).split())
                        if not match:
                            # Empty query should match nothing
                            clauses.append("1=0")
                            continue

                        pk = self._get_primary_key_field(tbl.__tablename__)
                        fts_table_name = f"{tbl.__tablename__}__{field}__fts"
                        fts_table = f"`{fts_table_name}`"         # use quoting for FROM/WHERE
                        main = table_alias or "t"

                        # Filter via EXISTS — DO NOT alias in MATCH
                        clauses.append(
                            f"EXISTS (SELECT 1 FROM {fts_table} "
                            f"WHERE rowid = {main}.`{pk}` AND {fts_table} MATCH ?)"
                        )
                        params.append(match)

                        # ORDER BY rank — bm25() must receive the bare table name (no quotes, no alias)
                        rank_expr = (
                            f"(SELECT bm25({fts_table_name}) FROM {fts_table} "
                            f"WHERE rowid = {main}.`{pk}` AND {fts_table} MATCH ?)"
                        )
                        self._fts_rank_exprs.append((field, rank_expr, match))

                    elif op == "$regex":
                        pattern = str(val)
                        # Separate regex vs glob metacharacters
                        regex_meta = set('.^$()+{}|\\')
                        glob_meta = set('*?[]')
                        has_regex = any(ch in regex_meta for ch in pattern)
                        has_glob = any(ch in glob_meta for ch in pattern)

                        if has_regex:
                            # Use REGEXP
                            clauses.append(f"{sql_field} REGEXP ?")
                            params.append(pattern)
                        elif has_glob:
                            # Use GLOB for simple wildcards
                            clauses.append(f"{sql_field} GLOB ?")
                            params.append(pattern)
                        else:
                            # Fallback to LIKE with escaping
                            escaped = self._escape_like(pattern)
                            clauses.append(f"{sql_field} LIKE ? ESCAPE '\\' COLLATE NOCASE")
                            params.append(f"%{escaped}%")

                    elif op in {"$gt", "$gte", "$lt", "$lte", "$eq", "$ne"}:
                        cmp_map = {
                            "$gt": ">", "$gte": ">=", "$lt": "<",
                            "$lte": "<=", "$eq": "=", "$ne": "<>"
                        }
                        if val is None and op in {"$eq", "$ne"}:
                            clause = f"{sql_field} IS NULL" if op == "$eq" else f"{sql_field} IS NOT NULL"
                            clauses.append(clause)
                        else:
                            clauses.append(f"{sql_field} {cmp_map[op]} ?")
                            params.append(val)
            else:
                # Direct value
                if expr is None:
                    clauses.append(f"{sql_field} IS NULL")
                else:
                    clauses.append(f"{sql_field} = ?")
                    params.append(expr)

        return " AND ".join(clauses), params

    def _normalize_boolean_value(self, field: str, expr: Any) -> Any:
        """Convert Python booleans to 0/1 for Boolean fields"""
        if not hasattr(self, '_current_table'):
            return expr

        table_cls = self.schema.get_table(self._current_table)
        if not table_cls:
            return expr

        fields = table_cls.get_fields()
        field_desc = fields.get(field)

        # If this is a Boolean field, normalize the value
        if isinstance(field_desc, Boolean):
            if isinstance(expr, bool):
                return 1 if expr else 0
            elif isinstance(expr, dict):
                # Normalize values inside operator dicts
                normalized = {}
                for op, val in expr.items():
                    if isinstance(val, bool):
                        normalized[op] = 1 if val else 0
                    elif isinstance(val, (list, tuple)) and op in ('$in', '$nin'):
                        # Normalize lists for $in/$nin
                        normalized[op] = [1 if v is True else 0 if v is False else v for v in val]
                    else:
                        normalized[op] = val
                return normalized

        return expr

    def _convert_field_name(self, field: str, table_alias: Optional[str] = None) -> str:
        # Check if field already has an alias (contains a dot)
        if '.' in field:
            # Disallow dotted field names since joins are not supported
            table_context = getattr(self, '_current_table', 'unknown')
            raise ValueError(
                f"Dotted field '{field}' is not supported in table '{table_context}' "
                f"(joins have been removed from this database implementation)."
            )

        # Regular field - add table alias if provided
        if table_alias:
            return f"{table_alias}.`{field}`"
        else:
            return f"`{field}`"

    def build_order_by_sql(self, sort_list: List[tuple], table_alias: Optional[str] = None) -> str:
        if not sort_list:
            return ""
        order_parts = []
        for field, direction in sort_list:
            dir_sql = "ASC" if direction == 1 else "DESC"
            sql_field = self._convert_field_name(field, table_alias)
            order_parts.append(f"{sql_field} {dir_sql}")
        return ", ".join(order_parts)

    @staticmethod
    def escape_quotes(s: str) -> str:
        return s.replace("'", "''")

    @staticmethod
    def _escape_like(s: str) -> str:
        """Escape LIKE wildcards in pattern"""
        # Escape backslash first, then % and _
        return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

    def convert_insert(self, mongo_obj: dict) -> str:
        """
        Convert MongoDB-style insert to SQL INSERT statement
        
        Args:
            mongo_obj: MongoDB insert object with 'collection' and 'insert' keys
            
        Returns:
            SQL INSERT statement
        """
        table = mongo_obj.get("collection")
        insert_data = mongo_obj.get("insert")

        if not table or insert_data is None:
            raise ValueError("Insert operation requires 'collection' and 'insert' keys")

        if isinstance(insert_data, list):
            return self._convert_bulk_insert(table, insert_data)
        else:
            return self._convert_single_insert(table, insert_data)

    def _convert_single_insert(self, table: str, data: Dict[str, Any]) -> str:
        """
        Convert single item insert to SQL INSERT statement

        Args:
            table: Table name
            data: Dictionary of field-value pairs

        Returns:
            SQL INSERT statement with placeholders and RETURNING clause
        """
        if not data:
            raise ValueError("Insert data cannot be empty")

        columns = list(data.keys())
        placeholders = ['?' for _ in columns]

        columns_str = ', '.join(f'`{col}`' for col in columns)
        placeholders_str = ', '.join(placeholders)

        # Get primary key field for RETURNING clause
        pk_field = self._get_primary_key_field(table)

        return f"INSERT INTO {self._quote_identifier(table)} ({columns_str}) VALUES ({placeholders_str}) RETURNING `{pk_field}`"

    def _convert_bulk_insert(self, table: str, data_list: List[Dict[str, Any]]) -> str:
        """
        Convert bulk insert to SQL INSERT statement

        Args:
            table: Table name
            data_list: List of dictionaries with field-value pairs

        Returns:
            SQL INSERT statement with placeholders for bulk insert and RETURNING clause
        """
        if not data_list:
            raise ValueError("Bulk insert data cannot be empty")

        # Use first item to determine columns (assumes all items have same structure)
        first_item = data_list[0]
        columns = list(first_item.keys())

        # Validate all items have the same keys
        for i, item in enumerate(data_list):
            if set(item.keys()) != set(columns):
                raise ValueError(
                    f"Item {i} has different keys than first item. All items must have the same structure for bulk insert.")

        columns_str = ', '.join(f'`{col}`' for col in columns)

        # Create placeholders for each row
        single_row_placeholders = ', '.join(['?' for _ in columns])
        all_rows_placeholders = ', '.join([f'({single_row_placeholders})' for _ in data_list])

        # Get primary key field for RETURNING clause
        pk_field = self._get_primary_key_field(table)

        return f"INSERT INTO {self._quote_identifier(table)} ({columns_str}) VALUES {all_rows_placeholders} RETURNING `{pk_field}`"

    def get_insert_values(self, mongo_obj: dict) -> Union[tuple, List[tuple]]:
        """
        Extract values from MongoDB insert object in correct order for SQL execution
        
        Args:
            mongo_obj: MongoDB insert object
            
        Returns:
            Tuple of values for single insert, or list of tuples for bulk insert
        """
        insert_data = mongo_obj.get("insert")

        if isinstance(insert_data, list):
            # Bulk insert - return list of tuples
            if not insert_data:
                return []

            columns = list(insert_data[0].keys())
            return [tuple(item[col] for col in columns) for item in insert_data]
        else:
            # Single insert - return single tuple
            return tuple(insert_data.values())

    def convert_update(self, mongo_obj: dict) -> str:
        """
        Convert MongoDB-style update to SQL UPDATE statement

        Args:
            mongo_obj: MongoDB update object with 'collection', 'update', and 'find' keys

        Returns:
            SQL UPDATE statement with placeholders
        """
        table = mongo_obj.get("collection")
        update_data = mongo_obj.get("update")
        find_filter = mongo_obj.get("find", {})

        if not table or not update_data:
            raise ValueError("Update operation requires 'collection' and 'update' keys")

        # Build SET clause
        set_clauses = []
        for field in update_data.keys():
            set_clauses.append(f"`{field}` = ?")
        set_clause = ", ".join(set_clauses)

        # Build WHERE clause with parameters
        where_sql, where_params = self.build_where_sql(find_filter)
        self._last_update_params = where_params  # Store for get_update_params()

        sql = f"UPDATE {self._quote_identifier(table)} SET {set_clause}"
        if where_sql:
            sql += f" WHERE {where_sql}"

        return sql

    def get_update_params(self, mongo_obj: dict) -> list:
        """Return parameters for the UPDATE query (SET values + WHERE params)"""
        update_data = mongo_obj.get("update", {})
        update_values = list(update_data.values())
        where_params = getattr(self, "_last_update_params", [])
        return update_values + where_params

    def convert_delete(self, mongo_obj: dict) -> str:
        """
        Convert MongoDB-style delete to SQL DELETE statement

        Args:
            mongo_obj: MongoDB delete object with 'collection', 'delete', and 'find' keys

        Returns:
            SQL DELETE statement with placeholders
        """
        table = mongo_obj.get("collection")
        find_filter = mongo_obj.get("find", {})

        if not table:
            raise ValueError("Delete operation requires 'collection' key")

        # Build WHERE clause with parameters
        where_sql, where_params = self.build_where_sql(find_filter)
        self._last_delete_params = where_params  # Store for get_delete_params()

        sql = f"DELETE FROM {self._quote_identifier(table)}"
        if where_sql:
            sql += f" WHERE {where_sql}"

        return sql

    def get_delete_params(self, mongo_obj: dict) -> list:
        """Return parameters for the DELETE query"""
        return getattr(self, "_last_delete_params", [])

