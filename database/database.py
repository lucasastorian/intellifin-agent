import sqlite3
import json
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Union, List, Dict, Any, Optional
from .schema.schema import Schema
from .utils.mongo_sql_converter import MongoToSqlConverter
from .errors import (
    DatabaseError, ConstraintError, ForeignKeyError,
    UniqueConstraintError, NotNullViolation, CheckConstraintError
)


class Database:

    def __init__(self, schema: Schema, base_path: str):
        if not base_path or base_path == ":memory:":
            raise ValueError("Provide a filesystem path for SQLite (no in-memory DBs).")
        self.schema = schema
        self.base_path = base_path
        self.converter = MongoToSqlConverter(schema)
        self.conn = sqlite3.connect(self.base_path)
        self.conn.row_factory = sqlite3.Row

        # Check SQLite version (require ≥3.35 for RETURNING support)
        version_str = self.conn.execute('SELECT sqlite_version()').fetchone()[0]
        version_tuple = tuple(int(x) for x in version_str.split('.'))
        if version_tuple < (3, 35, 0):
            raise RuntimeError(
                f"SQLite version {version_str} is too old. "
                f"This database requires SQLite ≥3.35.0 for RETURNING clause support."
            )

        # Check JSON1 extension is available
        try:
            self.conn.execute("SELECT json_valid('[]')").fetchone()
        except sqlite3.OperationalError as e:
            raise RuntimeError(
                "SQLite was built without JSON1; required for JSON array contains filters."
            ) from e

        # Register REGEXP function for regex_search (case-insensitive by default)
        import re
        def _sqlite_regexp(pattern, text):
            try:
                return 1 if re.search(pattern, text or "", re.IGNORECASE) else 0
            except re.error:
                return 0
        self.conn.create_function("REGEXP", 2, _sqlite_regexp)

        # Enable FK constraints and performance/reliability settings
        self.conn.execute("PRAGMA foreign_keys = ON;")
        self.conn.execute("PRAGMA journal_mode = WAL;")
        self.conn.execute("PRAGMA synchronous = NORMAL;")

        # Auto-provision schema if database is empty
        self._provision_schema_if_needed()

    def close(self):
        """Close the database connection"""
        self.conn.close()

    def _provision_schema_if_needed(self):
        """Automatically create tables if database is empty"""
        # Check if any tables exist (excluding sqlite internal tables)
        cursor = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
        existing_tables = [row[0] for row in cursor.fetchall()]

        # If database is empty, provision the schema
        if not existing_tables:
            create_sql = self.schema.generate_all_sql()
            self.conn.executescript(create_sql)
            self.conn.commit()

    @contextmanager
    def transaction(self):
        """Context manager for explicit transactions with automatic commit/rollback.

        Usage:
            with db.transaction():
                db.insert("companies", {...})
                db.insert("filings", {...})
        """
        try:
            yield
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def table(self, name: str) -> "TableQueryBuilder":
        """Return a query builder bound to a table (Supabase-style).

        Args:
            name: Table name

        Returns:
            TableQueryBuilder instance for chaining operations
        """
        from .query_builder.core.table_query_builder import TableQueryBuilder
        # Validate table exists
        self.schema.get_table(name)
        return TableQueryBuilder(self, self.schema, name)

    def rebuild_fts(self, table: str, column: str):
        """Rebuild FTS index for a specific column.

        Args:
            table: Table name
            column: Column name with fts=True

        Note:
            Currently a stub for future implementation. FTS tables are created
            via schema migrations and maintained automatically via triggers.
        """
        # TODO: Implement manual FTS rebuild
        # Could be used to rebuild after adding fts=True to existing populated table
        pass

    def _now_iso(self) -> str:
        """Return current UTC timestamp in ISO 8601 format"""
        return datetime.now(tz=timezone.utc).isoformat()

    def _apply_runtime_defaults(self, table: str, item: Dict[str, Any]) -> Dict[str, Any]:
        """Fill in CURRENT_TIMESTAMP defaults for missing fields at runtime"""
        tbl = self.schema.get_table(table)
        out = dict(item)
        for fname, fdesc in tbl.get_fields().items():
            if fname not in out:
                if isinstance(fdesc.default, str) and fdesc.default.upper() == "CURRENT_TIMESTAMP":
                    out[fname] = self._now_iso()
        return out

    def _apply_auto_update(self, table: str, update: Dict[str, Any]) -> Dict[str, Any]:
        """Ensure auto_update fields (e.g., updated_at) are set on each UPDATE"""
        tbl = self.schema.get_table(table)
        out = dict(update)
        for fname, fdesc in tbl.get_fields().items():
            if getattr(fdesc, "auto_update", False):
                out[fname] = self._now_iso()  # always refresh; override if present
        return out

    def _exec(self, sql: str, params: Union[List, tuple] = ()):
        """Execute SQL with error handling and exception mapping"""
        try:
            return self.conn.execute(sql, params)
        except sqlite3.IntegrityError as e:
            self.conn.rollback()  # Roll back failed transaction

            # Use error codes (Python 3.11+) if available, fallback to string matching
            error_name = getattr(e, "sqlite_errorname", "")
            msg = str(e)

            if error_name == "SQLITE_CONSTRAINT_FOREIGNKEY" or "FOREIGN KEY constraint failed" in msg:
                raise ForeignKeyError(msg) from e
            if error_name == "SQLITE_CONSTRAINT_UNIQUE" or "UNIQUE constraint failed" in msg:
                raise UniqueConstraintError(msg) from e
            if error_name == "SQLITE_CONSTRAINT_NOTNULL" or "NOT NULL constraint failed" in msg:
                raise NotNullViolation(msg) from e
            if error_name == "SQLITE_CONSTRAINT_CHECK" or "CHECK constraint failed" in msg:
                raise CheckConstraintError(msg) from e
            raise ConstraintError(msg) from e
        except sqlite3.Error as e:
            self.conn.rollback()  # Roll back on any database error
            raise DatabaseError(str(e)) from e

    def _count(self, *, table: str, find: Dict[str, Any]) -> int:
        """Execute a COUNT query on specified table (internal use only).

        Args:
            table: Table name
            find: Filter conditions (MongoDB-style)

        Returns:
            Integer count of matching rows
        """
        # Convert filters to WHERE clause
        sql = self.converter.convert_select(table=table, find=find, projection={}, sort=[], limit=None)
        params = self.converter.get_last_select_params()

        # Replace SELECT ... with SELECT COUNT(*)
        # The converter generates: SELECT ... FROM table WHERE ...
        # We want: SELECT COUNT(*) FROM table WHERE ...
        if sql.upper().startswith("SELECT "):
            from_pos = sql.upper().find(" FROM ")
            if from_pos != -1:
                sql = "SELECT COUNT(*)" + sql[from_pos:]

        cursor = self._exec(sql, params)
        result = cursor.fetchone()
        return result[0] if result else 0

    def _query(self, *, table: str, find: Dict[str, Any], projection: Optional[Dict[str, int]] = None,
               sort: Optional[list] = None, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Execute a query on specified table with explicit parameters (internal use only).

        Args:
            table: Table name
            find: Filter conditions (MongoDB-style)
            projection: Field projection dict (e.g., {"id": 1, "name": 1})
            sort: Sort list (e.g., [("field", 1)] for ASC, [("field", -1)] for DESC)
            limit: Maximum number of rows to return

        Returns:
            List of matching rows as dictionaries
        """
        projection = projection or {}
        sort = sort or []

        sql = self.converter.convert_select(table=table, find=find, projection=projection,
                                            sort=sort, limit=limit)
        params = self.converter.get_last_select_params()

        cursor = self._exec(sql, params)
        rows = [dict(row) for row in cursor.fetchall()]

        processed = []
        for row in rows:
            row = self._deserialize_json_fields(table, row)
            # Pad missing fields (projection) with None
            if projection:
                for fld in projection.keys():
                    row.setdefault(fld, None)
            else:
                for fld in self.schema.get_table(table).get_fields().keys():
                    row.setdefault(fld, None)
            processed.append(row)
        return processed

    def _insert(self, table: str, data: Union[Dict[str, Any], List[Dict[str, Any]]]) -> Union[int, List[int]]:
        """Execute insert operation with validation (internal use only)"""
        # Validate data against schema
        validated_data = self._validate_insert_data(table, data)

        # Ensure data is a list for consistent processing
        if isinstance(validated_data, dict):
            validated_data = [validated_data]

        # Fill CURRENT_TIMESTAMP defaults before serialization
        with_defaults = [self._apply_runtime_defaults(table, item) for item in validated_data]

        # Serialize JSON fields before inserting
        serialized_data = [self._serialize_json_fields(table, item) for item in with_defaults]

        mongo_obj = {"collection": table, "insert": serialized_data}
        sql = self.converter.convert(mongo_obj)

        # Use converter's get_insert_values to maintain correct column order
        values = self.converter.get_insert_values(mongo_obj)
        if isinstance(values, list):
            # Bulk insert - flatten list of tuples
            all_values = [v for row in values for v in row]
        else:
            # Single insert
            all_values = list(values)

        cursor = self._exec(sql, all_values)

        # Get inserted row IDs from RETURNING clause (before commit)
        inserted_ids = [row[0] for row in cursor.fetchall()]

        self.conn.commit()

        # Return the appropriate format
        if isinstance(data, dict):
            return inserted_ids[0]
        else:
            return inserted_ids

    def _update(self, table: str, data: Dict[str, Any], filters: Dict[str, Any]) -> int:
        """Execute update operation with validation (internal use only)"""
        if not data:
            raise ValueError("Update data cannot be empty")

        # Inject auto_update values *before* validation so the validator sees them
        data_with_auto = self._apply_auto_update(table, data)

        # Validate data against schema
        validated_data = self._validate_update_data(table, data_with_auto)

        mongo_obj = {
            "collection": table,
            "update": validated_data,
            "find": filters
        }

        sql = self.converter.convert_update(mongo_obj)
        params = self.converter.get_update_params(mongo_obj)

        cursor = self._exec(sql, params)
        self.conn.commit()

        return cursor.rowcount

    def _delete(self, table: str, filters: Dict[str, Any]) -> int:
        """Execute delete operation and return number of affected rows (internal use only)"""
        mongo_obj = {
            "collection": table,
            "delete": True,
            "find": filters
        }

        sql = self.converter.convert_delete(mongo_obj)
        params = self.converter.get_delete_params(mongo_obj)

        cursor = self._exec(sql, params)
        self.conn.commit()
        return cursor.rowcount

    def _upsert(
        self,
        *,
        table: str,
        values: Union[Dict[str, Any], List[Dict[str, Any]]],
        on_conflict: Union[str, List[str]],
        ignore_duplicates: bool = False,
        returning: str = "representation",
        count: Optional[str] = None,
        default_to_null: bool = False,
    ) -> Dict[str, Any]:
        """
        Supabase-style UPSERT for SQLite (internal use only).

        Args:
            table: Table name
            values: Dict or list of dicts to insert/update
            on_conflict: Column name(s) with UNIQUE/PK constraint.
                        Can be string ("col1" or "col1,col2") or list (["col1", "col2"]).
                        Must match an existing UNIQUE constraint or PRIMARY KEY in SQLite.
            ignore_duplicates: True => DO NOTHING, False => DO UPDATE SET
            returning: "minimal" (no rows) or "representation" (full rows)
            count: "exact" to get affected row count in result
            default_to_null: For bulk, missing fields -> NULL (True) vs DEFAULT (False)

        Returns:
            Dict with "data" (list of rows or None) and optionally "count"

        Note:
            SQLite requires ON CONFLICT to reference actual columns with a UNIQUE constraint
            or PRIMARY KEY, not constraint names like Postgres. Ensure your schema defines
            the appropriate UNIQUE constraint for the columns specified.
        """
        if isinstance(values, dict):
            rows = [values]
        else:
            rows = list(values)

        if not rows:
            return {"data": [], "count": 0}

        # Normalize on_conflict: support comma-separated strings
        if isinstance(on_conflict, str):
            conflict_cols = [c.strip() for c in on_conflict.split(",") if c.strip()]
        else:
            conflict_cols = list(on_conflict)

        # Validate conflict columns exist and are UNIQUE/PK
        tbl = self.schema.get_table(table)
        fields = tbl.get_fields()
        for c in conflict_cols:
            if c not in fields:
                raise ValueError(f"on_conflict column '{c}' does not exist in table '{table}'")

        # Validate constraint exists
        if len(conflict_cols) == 1:
            # Single column: check it has unique=True or primary_key=True
            fd = fields[conflict_cols[0]]
            if not (fd.primary_key or fd.unique):
                raise ValueError(
                    f"on_conflict column '{conflict_cols[0]}' must have UNIQUE constraint or be PRIMARY KEY. "
                    f"Define unique=True on the field or use __uniques__ for composite constraints."
                )
        else:
            # Multi-column: check it exists in __uniques__
            declared_uniques = [tuple(u) for u in getattr(tbl, '__uniques__', ())]
            if tuple(conflict_cols) not in declared_uniques:
                raise ValueError(
                    f"on_conflict {tuple(conflict_cols)} does not match any declared composite UNIQUE "
                    f"on table '{table}'. Declare it via __uniques__ = [{tuple(conflict_cols)}] or adjust column order."
                )

        # Validate + runtime defaults + JSON serialization per row
        validated_rows = []
        for row in rows:
            v = self._validate_insert_data(table, row)
            v = self._apply_runtime_defaults(table, v)
            v = self._serialize_json_fields(table, v)
            validated_rows.append(v)

        # Column set must be uniform for VALUES(...),(...).
        # Take the union of keys across rows.
        all_cols: List[str] = []
        seen = set()
        for r in validated_rows:
            for k in r.keys():
                if k not in seen:
                    seen.add(k)
                    all_cols.append(k)

        # Primary key (for RETURNING)
        pk = self.converter._get_primary_key_field(table)

        # Build VALUES matrix with '?' or 'DEFAULT' per cell
        def row_tokens_and_params(r: Dict[str, Any]):
            tokens = []
            params = []
            for c in all_cols:
                if c in r:
                    tokens.append("?")
                    params.append(r[c])
                else:
                    if default_to_null:
                        tokens.append("?")
                        params.append(None)
                    else:
                        tokens.append("DEFAULT")
            return tokens, params

        rows_tokens: List[str] = []
        params: List[Any] = []
        for r in validated_rows:
            toks, prms = row_tokens_and_params(r)
            rows_tokens.append("(" + ", ".join(toks) + ")")
            params.extend(prms)

        cols_sql = ", ".join(f"`{c}`" for c in all_cols)
        values_sql = ", ".join(rows_tokens)
        conflict_sql = ", ".join(f"`{c}`" for c in conflict_cols)

        # Build DO NOTHING or DO UPDATE SET
        if ignore_duplicates:
            action_sql = "DO NOTHING"
        else:
            # Update all non-PK, non-conflict columns from excluded; also refresh auto_update fields.
            tbl = self.schema.get_table(table)
            updates = []
            for c, fdesc in tbl.get_fields().items():
                if c in conflict_cols:
                    continue
                if fdesc.primary_key:
                    continue
                if c in all_cols:
                    updates.append(f"`{c}` = excluded.`{c}`")

            # auto_update fields: force updated timestamp now (override excluded)
            now_updates = []
            now_vals = []
            for c, fdesc in tbl.get_fields().items():
                if getattr(fdesc, "auto_update", False):
                    now_updates.append(f"`{c}` = ?")
                    now_vals.append(self._now_iso())

            set_sql = ", ".join(updates + now_updates) or f"`{pk}`=`{pk}`"
            action_sql = f"DO UPDATE SET {set_sql}"
            params.extend(now_vals)

        # RETURNING
        if returning == "representation":
            returning_sql = "RETURNING *"
        else:
            returning_sql = ""

        sql = (
            f"INSERT INTO `{table}` ({cols_sql}) VALUES {values_sql} "
            f"ON CONFLICT ({conflict_sql}) {action_sql} "
            f"{returning_sql}"
        ).strip()

        # Track changes for accurate count
        before_changes = self.conn.total_changes
        cursor = self._exec(sql, params)

        data = []
        affected = 0
        if returning == "representation":
            rows_out = [dict(r) for r in cursor.fetchall()]
            # Deserialize JSON fields + pad:
            for row in rows_out:
                row = self._deserialize_json_fields(table, row)
                for fld in self.schema.get_table(table).get_fields().keys():
                    row.setdefault(fld, None)
                data.append(row)
            affected = len(data)
            self.conn.commit()
        else:
            # minimal: use total_changes for deterministic count
            self.conn.commit()
            after_changes = self.conn.total_changes
            affected = after_changes - before_changes

        result = {"data": data if returning == "representation" else None}
        if count == "exact":
            result["count"] = affected
        return result

    @classmethod
    def from_file(cls, path: str, schema: Schema):
        """Initialize database from file path"""
        return cls(schema, path)

    def fetch_rows_by_primary_key(self, table_name: str, ids: List[Union[int, str]]) -> Dict[Union[int, str], Dict[str, Any]]:
        """Fetch rows by primary key IDs"""
        if not ids:
            return {}

        # Get primary key field name
        table_cls = self.schema.get_table(table_name)
        primary_key_field = None
        for field_name, field_desc in table_cls.get_fields().items():
            if field_desc.primary_key:
                primary_key_field = field_name
                break

        if not primary_key_field:
            raise ValueError(f"No primary key field found for table '{table_name}'")

        # Build parameterized query
        placeholders = ",".join(["?" for _ in ids])
        sql = f"SELECT * FROM `{table_name}` WHERE `{primary_key_field}` IN ({placeholders})"

        cursor = self._exec(sql, ids)

        # Build result dictionary
        columns = [desc[0] for desc in cursor.description]
        rows = cursor.fetchall()

        result = {}
        for row in rows:
            row_dict = dict(zip(columns, row))
            # Deserialize JSON fields
            row_dict = self._deserialize_json_fields(table_name, row_dict)
            primary_key_value = row_dict[primary_key_field]
            result[primary_key_value] = row_dict

        return result

    def _serialize_json_fields(self, table: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Serialize JSON fields to strings for SQLite storage"""
        # Get table schema
        table_obj = self.schema.get_table(table)
        if not table_obj:
            return data

        fields = table_obj.get_fields()
        serialized_data = data.copy()

        for field_name, field_descriptor in fields.items():
            if field_name in data and field_descriptor.sql_type == 'TEXT':
                # Check if this is a JSONField by checking the class type
                from .schema.fields import JSONField
                if isinstance(field_descriptor, JSONField):
                    value = data[field_name]
                    if value is not None and not isinstance(value, str):
                        try:
                            serialized_data[field_name] = json.dumps(value)
                        except (TypeError, ValueError):
                            serialized_data[field_name] = str(value)

        return serialized_data

    def _deserialize_json_fields(self, table: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Deserialize JSON fields from strings back to Python objects"""
        # Get table schema
        table_obj = self.schema.get_table(table)
        if not table_obj:
            return data

        fields = table_obj.get_fields()
        deserialized_data = data.copy()

        for field_name, field_descriptor in fields.items():
            if field_name in data and field_descriptor.sql_type == 'TEXT':
                # Check if this is a JSONField
                from .schema.fields import JSONField
                if isinstance(field_descriptor, JSONField):
                    value = data[field_name]
                    if value is not None and isinstance(value, str):
                        try:
                            deserialized_data[field_name] = json.loads(value)
                        except (json.JSONDecodeError, TypeError):
                            pass

        return deserialized_data

    def _validate_insert_data(self, table: str, data: Union[Dict[str, Any], List[Dict[str, Any]]]) -> Union[Dict[str, Any], List[Dict[str, Any]]]:
        """Validate insert data against schema"""
        return self.schema.validate_insert_data(table, data)

    def _validate_update_data(self, table: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Validate update data against schema"""
        return self.schema.validate_update_data(table, data)
