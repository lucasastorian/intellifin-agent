import re
import json
import sqlite3
import threading
from pathlib import Path
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Union, List, Dict, Any
from .schema.schema import Schema
from .vector_store import VectorStore
from .embeddings.voyage_embeddings import VoyageEmbeddings
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
        self._lock = threading.RLock()  # Reentrant lock for nested acquisitions
        self._closed = False  # Track connection state

        Path(base_path).parent.mkdir(parents=True, exist_ok=True)

        self.conn = sqlite3.connect(self.base_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._max_vars = None

        version_str = self.conn.execute('SELECT sqlite_version()').fetchone()[0]
        version_tuple = tuple(int(x) for x in version_str.split('.'))
        if version_tuple < (3, 35, 0):
            raise RuntimeError(
                f"SQLite version {version_str} is too old. "
                f"This database requires SQLite ≥3.35.0 for RETURNING clause support."
            )

        try:
            self.conn.execute("SELECT json_valid('[]')").fetchone()
        except sqlite3.OperationalError as e:
            raise RuntimeError("SQLite was built without JSON1; required for JSON array contains filters.") from e

        def _sqlite_regexp(pattern, text):
            try:
                return 1 if re.search(pattern, text or "", re.IGNORECASE) else 0
            except re.error:
                return 0
        self.conn.create_function("REGEXP", 2, _sqlite_regexp)

        # Check BM25 availability for FTS5 ranking
        self._has_bm25 = self._check_bm25_support()

        self.conn.execute("PRAGMA foreign_keys = ON;")
        self.conn.execute("PRAGMA journal_mode = WAL;")
        self.conn.execute("PRAGMA synchronous = NORMAL;")
        self.conn.execute("PRAGMA busy_timeout = 5000;")

        self._provision_schema_if_needed()
        self._ensure_fts_objects()
        self._init_vector_stores()
        self._init_embedder()

    def close(self):
        """Close the database connection in a thread-safe manner.

        Acquires the lock to ensure no queries are in-flight during shutdown.
        Performs WAL checkpoint to merge WAL file into main database.
        """
        with self._lock:
            if self._closed:
                return
            try:
                # Checkpoint WAL to merge changes into main database file
                # TRUNCATE mode removes WAL file after successful checkpoint
                self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
                self.conn.commit()
            except Exception:
                # Don't fail close if checkpoint fails
                pass
            finally:
                # Close all vector stores
                if hasattr(self, 'vector_stores'):
                    for vs in self.vector_stores.values():
                        try:
                            vs.close()
                        except Exception:
                            pass

                # Close embedding cache LMDB connection
                if hasattr(self, 'embedder') and self.embedder and self.embedder.cache:
                    try:
                        self.embedder.cache.close()
                    except Exception:
                        pass
                self._closed = True
                self.conn.close()

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - close connection."""
        self.close()
        return False

    def _provision_schema_if_needed(self):
        """Automatically create tables if database is empty."""
        cursor = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
        existing_tables = [row[0] for row in cursor.fetchall()]

        if not existing_tables:
            create_sql = self.schema.generate_all_sql()
            self.conn.executescript(create_sql)
            self.conn.commit()

    def _ensure_fts_objects(self):
        """Idempotently create FTS virtual tables and triggers for all tables with fts=True fields."""
        for table_name, table_cls in self.schema.tables.items():
            fts_sql = table_cls._generate_fts_sql()
            if not fts_sql:
                continue
            # CREATE VIRTUAL TABLE IF NOT EXISTS and CREATE TRIGGER IF NOT EXISTS are idempotent
            self.conn.executescript(fts_sql)
        self.conn.commit()

    def _check_bm25_support(self) -> bool:
        """Check if SQLite FTS5 bm25() function is available.

        Some SQLite builds may not expose the bm25() function.
        Falls back to 'rank' or rank-only pseudo-scores if unavailable.

        Returns:
            True if bm25() is available, False otherwise.
        """
        try:
            # Create a temporary FTS5 table to test bm25()
            self.conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS _bm25_test USING fts5(content);")
            self.conn.execute("INSERT INTO _bm25_test(rowid, content) VALUES (1, 'test');")
            # Try to use bm25()
            self.conn.execute("SELECT bm25(_bm25_test) FROM _bm25_test WHERE _bm25_test MATCH 'test';").fetchone()
            # Clean up
            self.conn.execute("DROP TABLE _bm25_test;")
            self.conn.commit()
            return True
        except sqlite3.OperationalError:
            # bm25() not available - will use rank or pseudo-scores
            try:
                # Clean up test table if it was created
                self.conn.execute("DROP TABLE IF EXISTS _bm25_test;")
                self.conn.commit()
            except Exception:
                pass
            return False

    def _init_vector_stores(self):
        """Initialize vector stores registry (created on-demand)"""
        self.vector_stores = {}

    def _init_embedder(self):
        """Initialize Voyage embedder (512d, voyage-3.5-lite)"""
        self.embedder = VoyageEmbeddings(model="voyage-3.5-lite", dimensions=512)

    def get_or_create_vector_store(self, table: str, column: str) -> VectorStore:
        """Get or create a vector store for a table/column pair

        Args:
            table: Table name
            column: Column name (must have vector=True in schema)

        Raises:
            ValueError: If column doesn't have vector=True in schema
        """
        # Validate that column has vector=True in schema
        if table not in self.schema.tables:
            raise ValueError(f"Table '{table}' not found in schema")

        table_cls = self.schema.tables[table]
        fields = table_cls.get_fields()

        if column not in fields:
            raise ValueError(f"Column '{column}' not found in table '{table}'")

        field = fields[column]
        if not getattr(field, 'vector', False):
            raise ValueError(
                f"Column '{table}.{column}' does not have vector=True in schema. "
                f"Cannot create vector store for non-vector column."
            )

        key = (table, column)
        if key not in self.vector_stores:
            vector_dir = Path(self.base_path).parent / 'vectors'
            store_path = vector_dir / f'{table}__{column}'
            self.vector_stores[key] = VectorStore(str(store_path), dim=512)
        return self.vector_stores[key]

    @contextmanager
    def transaction(self):
        """Context manager for explicit transactions with automatic commit/rollback."""
        with self._lock:
            if self._closed:
                raise DatabaseError("Cannot start transaction on closed database connection")
            try:
                yield
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise

    def table(self, name: str) -> "TableQueryBuilder":
        """Return a query builder bound to a table or view."""
        from .query_builder.core.table_query_builder import TableQueryBuilder

        if name in self.schema.views:
            pass
        else:
            self.schema.get_table(name)

        return TableQueryBuilder(self, self.schema, name)

    def _now_iso(self) -> str:
        """Return current UTC timestamp in ISO 8601 format."""
        return datetime.now(tz=timezone.utc).isoformat()

    def _apply_runtime_defaults(self, table: str, item: Dict[str, Any]) -> Dict[str, Any]:
        """Fill in CURRENT_TIMESTAMP defaults for missing fields at runtime."""
        tbl = self.schema.get_table(table)
        out = dict(item)
        for fname, fdesc in tbl.get_fields().items():
            if fname not in out:
                if isinstance(fdesc.default, str) and fdesc.default.upper() == "CURRENT_TIMESTAMP":
                    out[fname] = self._now_iso()
        return out

    def _apply_auto_update(self, table: str, update: Dict[str, Any]) -> Dict[str, Any]:
        """Ensure auto_update fields are set on each UPDATE."""
        tbl = self.schema.get_table(table)
        out = dict(update)
        for fname, fdesc in tbl.get_fields().items():
            if getattr(fdesc, "auto_update", False):
                out[fname] = self._now_iso()
        return out

    def _get_max_vars(self) -> int:
        """Detect SQLite's maximum host parameter limit."""
        if self._max_vars is not None:
            return self._max_vars

        try:
            row = self.conn.execute("PRAGMA compile_options").fetchall()
            for (opt,) in row:
                if opt.startswith("MAX_VARIABLE_NUMBER="):
                    self._max_vars = int(opt.split("=")[1])
                    return self._max_vars
        except sqlite3.Error:
            pass

        self._max_vars = 999
        return self._max_vars

    def _exec_unsafe(self, sql: str, params: Union[List, tuple] = ()) -> List[Dict[str, Any]]:
        """Execute SQL without lock - caller must hold lock. Internal use only."""
        if self._closed:
            raise DatabaseError("Cannot execute query on closed database connection")
        try:
            cursor = self.conn.execute(sql, params)
            if cursor.description:
                cols = [c[0] for c in cursor.description]
                rows = [dict(zip(cols, r)) for r in cursor.fetchall()]
                return rows
            return []
        except sqlite3.IntegrityError as e:
            self.conn.rollback()
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
            self.conn.rollback()
            raise DatabaseError(str(e)) from e

    async def _exec(self, sql: str, params: Union[List, tuple] = ()) -> List[Dict[str, Any]]:
        """Execute SQL with error handling and return rows as dicts."""
        with self._lock:
            return self._exec_unsafe(sql, params)

    @classmethod
    def from_file(cls, path: str, schema: Schema):
        """Initialize database from file path."""
        return cls(schema, path)

    def fetch_rows_by_primary_key(self, table_name: str, ids: List[Union[int, str]]) -> Dict[Union[int, str], Dict[str, Any]]:
        """Fetch rows by primary key IDs."""
        if not ids:
            return {}

        table_cls = self.schema.get_table(table_name)
        primary_key_field = None
        for field_name, field_desc in table_cls.get_fields().items():
            if field_desc.primary_key:
                primary_key_field = field_name
                break

        if not primary_key_field:
            raise ValueError(f"No primary key field found for table '{table_name}'")

        placeholders = ",".join(["?" for _ in ids])
        sql = f"SELECT * FROM `{table_name}` WHERE `{primary_key_field}` IN ({placeholders})"

        rows = self._exec(sql, ids)

        result = {}
        for row_dict in rows:
            row_dict = self._deserialize_json_fields(table_name, row_dict)
            primary_key_value = row_dict[primary_key_field]
            result[primary_key_value] = row_dict

        return result

    def _serialize_json_fields(self, table: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Serialize JSON fields to strings for SQLite storage."""
        from .schema.fields import JSONField

        if table in self.schema.views:
            vcls = self.schema.views[table]
            tmap = vcls.type_map(self.schema)
            out = data.copy()
            for alias, val in list(out.items()):
                fd = tmap.get(alias)
                if fd is not None and isinstance(fd, JSONField):
                    if val is not None and not isinstance(val, str):
                        try:
                            out[alias] = json.dumps(val)
                        except Exception:
                            out[alias] = str(val)
            return out

        table_obj = self.schema.get_table(table)
        if not table_obj:
            return data

        fields = table_obj.get_fields()
        serialized_data = data.copy()

        for field_name, field_descriptor in fields.items():
            if field_name in data and field_descriptor.sql_type == 'TEXT':
                if isinstance(field_descriptor, JSONField):
                    value = data[field_name]
                    if value is not None and not isinstance(value, str):
                        try:
                            serialized_data[field_name] = json.dumps(value)
                        except (TypeError, ValueError):
                            serialized_data[field_name] = str(value)

        return serialized_data

    def _deserialize_json_fields(self, table: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Deserialize JSON fields from strings back to Python objects."""
        from .schema.fields import JSONField

        if table in self.schema.views:
            vcls = self.schema.views[table]
            tmap = vcls.type_map(self.schema)
            out = data.copy()
            for alias, fd in tmap.items():
                if alias in out and isinstance(fd, JSONField):
                    v = out[alias]
                    if v is not None and isinstance(v, str):
                        try:
                            out[alias] = json.loads(v)
                        except Exception:
                            pass
            return out

        table_obj = self.schema.get_table(table)
        if not table_obj:
            return data

        fields = table_obj.get_fields()
        deserialized_data = data.copy()

        for field_name, field_descriptor in fields.items():
            if field_name in data and field_descriptor.sql_type == 'TEXT':
                if isinstance(field_descriptor, JSONField):
                    value = data[field_name]
                    if value is not None and isinstance(value, str):
                        try:
                            deserialized_data[field_name] = json.loads(value)
                        except (json.JSONDecodeError, TypeError):
                            pass

        return deserialized_data

    def _validate_insert_data(self, table: str, data: Union[Dict[str, Any], List[Dict[str, Any]]]) -> Union[Dict[str, Any], List[Dict[str, Any]]]:
        """Validate insert data against schema."""
        return self.schema.validate_insert_data(table, data)

    def _validate_update_data(self, table: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Validate update data against schema."""
        return self.schema.validate_update_data(table, data)
