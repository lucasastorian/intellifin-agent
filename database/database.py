import re
import json
import sqlite3
import threading
import numpy as np
from pathlib import Path
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Union, List, Dict, Any, Tuple
from .schema.schema import Schema
from .vector_store import VectorStore
from .clients.voyage_client import VoyageClient
from .context import txn_depth_var, emb_queue_var, atomic_vectors_var
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
        self._sp_counter = 0  # Savepoint counter for unique names

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

            # Warn if closing during an open transaction
            if txn_depth_var.get() > 0:
                import logging
                logging.warning(
                    f"Closing database connection with {txn_depth_var.get()} open transaction(s). "
                    "Rolling back uncommitted changes."
                )
                try:
                    self.conn.rollback()
                except Exception:
                    pass

                # Clear any dangling task-local queue to avoid state leaks
                if emb_queue_var.get() is not None:
                    try:
                        emb_queue_var.set(None)
                    except Exception:
                        pass

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

        # Always ensure vector_outbox table exists (for failed embedding retry)
        self._ensure_vector_outbox_table()

    def _ensure_vector_outbox_table(self):
        """Create vector_outbox table if it doesn't exist.

        The outbox stores failed embeddings from non-atomic transactions for later retry.
        Uses text_sha256 hash for idempotent inserts (prevents duplicate retries).
        """
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS vector_outbox (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                table_name TEXT NOT NULL,
                column_name TEXT NOT NULL,
                row_id INTEGER NOT NULL,
                text TEXT NOT NULL,
                text_sha256 TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        self.conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_vector_outbox
            ON vector_outbox(table_name, column_name, row_id, text_sha256)
        """)
        self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_vector_outbox_created_at
            ON vector_outbox(created_at)
        """)
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
        """Initialize Voyage client (embeddings + reranking, 512d, voyage-3.5-lite)

        If VOYAGE_API_KEY is not set, embedder will be None and only keyword search will work.
        """
        try:
            self.embedder = VoyageClient(model="voyage-3.5-lite", dimensions=512, rerank_model="rerank-2.5")
        except AssertionError:
            # API key not set - allow database to work without embedder for keyword-only search
            self.embedder = None

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

    def in_transaction(self) -> bool:
        """Check if currently inside a transaction context.

        Uses ContextVar to check per-task transaction depth.
        """
        return txn_depth_var.get() > 0

    @asynccontextmanager
    async def transaction(self, *, atomic_vectors: bool = False, use_savepoints: bool = True):
        """Async context manager for transactions with batched embeddings.

        Automatically batches all embeddings during the transaction and flushes on commit.
        Supports nesting via depth counters and SQLite SAVEPOINTs.

        Args:
            atomic_vectors: If True, embed first then commit (rollback possible if embed fails).
                           If False (default), commit first then embed (better availability).
            use_savepoints: Use SQLite SAVEPOINTs for nested transactions (default: True).

        Usage:
            async with db.transaction():
                await db.table("notes").upsert([...]).execute()
                await db.table("chunks").upsert([...]).execute()
            # ↑ All embeddings batched into one or a few Voyage API calls per (table, column)
        """
        if self._closed:
            raise DatabaseError("Cannot start transaction on closed database connection")

        # Bind atomic_vectors flag to this task
        token_atomic = atomic_vectors_var.set(atomic_vectors)

        # Manage nesting depth
        depth = txn_depth_var.get()
        outermost = (depth == 0)
        token_depth = txn_depth_var.set(depth + 1)

        # Generate unique savepoint name (thread-safe)
        savepoint_name = None
        if not outermost and use_savepoints:
            with self._lock:
                self._sp_counter += 1
                savepoint_name = f"sp_{self._sp_counter}"

        # Outermost: open transaction and create embedding queue
        if outermost:
            queue = {}
            token_queue = emb_queue_var.set(queue)
            with self._lock:
                self.conn.execute("BEGIN IMMEDIATE;")
        else:
            token_queue = None
            if use_savepoints:
                with self._lock:
                    self.conn.execute(f"SAVEPOINT {savepoint_name};")

        try:
            yield

            if outermost:
                if atomic_vectors_var.get():
                    # Atomic mode: embed first, THEN commit
                    # If embedding fails, we can rollback uncommitted SQL
                    failed_groups = await self._flush_embedding_queue(emb_queue_var.get() or {})
                    with self._lock:
                        self.conn.commit()

                    # Even in atomic mode, write failed vector store writes to outbox
                    # (SQL already committed, but some vector stores may have failed)
                    if failed_groups:
                        import logging
                        logging.warning(f"Some vector groups failed after atomic commit, writing to outbox")
                        await self._write_failed_groups_to_outbox(failed_groups)
                else:
                    # Non-atomic mode: commit first, THEN try to embed
                    # Better availability - SQL persists even if embeddings fail
                    with self._lock:
                        self.conn.commit()

                    failed_groups = await self._flush_embedding_queue(emb_queue_var.get() or {})
                    if failed_groups:
                        # SQL already committed - write failures to outbox for retry
                        import logging
                        logging.error(f"Embedding flush failed after commit, writing to outbox")
                        await self._write_failed_groups_to_outbox(failed_groups)
            else:
                # Nested transaction: release savepoint
                if use_savepoints:
                    with self._lock:
                        self.conn.execute(f"RELEASE SAVEPOINT {savepoint_name};")

        except Exception:
            if outermost:
                with self._lock:
                    self.conn.rollback()
            else:
                if use_savepoints:
                    with self._lock:
                        self.conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint_name};")
            raise
        finally:
            # Decrement depth and clean up outermost state
            try:
                prev_depth = txn_depth_var.get()
                txn_depth_var.set(max(prev_depth - 1, 0))
            except Exception:
                pass

            atomic_vectors_var.reset(token_atomic)
            if token_queue and outermost:
                emb_queue_var.reset(token_queue)

    async def _flush_embedding_queue(self, queue: Dict[Tuple[str, str], Dict[str, List]]):
        """Batch embed and store all accumulated embeddings from transaction.

        Processes each (table, column) group independently. Returns list of failed groups
        so only failures are written to outbox (avoids re-enqueueing successful writes).

        Args:
            queue: Dict mapping (table, column) -> {ids: [...], texts: [...]}

        Returns:
            List of failed groups as tuples: (table, column, ids, texts)
        """
        if not queue:
            return []

        if not self.embedder:
            raise DatabaseError(
                "Cannot generate embeddings without an embedder. "
                "Set VOYAGE_API_KEY environment variable or avoid using vector fields."
            )

        import logging
        failed_groups = []

        for (table, column), payload in queue.items():
            ids = payload["ids"]
            texts = payload["texts"]
            if not texts:
                continue

            try:
                # Call embedder - VoyageClient handles batching internally
                # May split by token limits, resulting in multiple API calls per group
                logging.debug(f"Embedding {len(texts)} texts for {table}.{column}")
                embeddings = await self.embedder.embed(texts)
                vectors = [np.array(emb, dtype=np.float32) for emb in embeddings]

                # Store in vector store (locked for thread safety)
                vector_store = self.get_or_create_vector_store(table, column)
                with self._lock:
                    vector_store.add_batch(ids, vectors)

                logging.debug(f"Successfully embedded {len(texts)} texts for {table}.{column}")

            except Exception as e:
                # Collect failure for outbox write - don't propagate to avoid rolling back other groups
                logging.error(f"Failed to embed/store group ({table}, {column}): {e}")
                failed_groups.append((table, column, ids, texts))

        return failed_groups

    async def _write_failed_groups_to_outbox(self, failed_groups: List[Tuple[str, str, List, List]]):
        """Write failed embedding groups to outbox table for later retry.

        Uses SHA256 hash for idempotent inserts (INSERT OR IGNORE prevents duplicates).

        Args:
            failed_groups: List of tuples (table, column, ids, texts)
        """
        if not failed_groups:
            return

        import logging
        import hashlib
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).isoformat()
        outbox_rows = []

        for table, column, ids, texts in failed_groups:
            for row_id, text in zip(ids, texts):
                text_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
                outbox_rows.append((table, column, row_id, text, text_sha256, now))

        if outbox_rows:
            try:
                # INSERT OR IGNORE ensures idempotent writes (duplicate sha256 hashes are skipped)
                with self._lock:
                    self.conn.executemany(
                        "INSERT OR IGNORE INTO vector_outbox "
                        "(table_name, column_name, row_id, text, text_sha256, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        outbox_rows
                    )
                    self.conn.commit()
                logging.info(f"Wrote {len(outbox_rows)} failed embeddings to vector_outbox")
            except Exception as e:
                logging.warning(f"Failed to write to vector_outbox: {e}")

    async def flush_vector_outbox(self):
        """Retry embedding generation for all items in the vector outbox.

        Call this periodically or on-demand to process failed embeddings from non-atomic mode.
        Processes groups independently - successful groups are deleted even if others fail.
        """
        if not self.embedder:
            import logging
            logging.warning("Cannot flush vector_outbox without embedder")
            return

        import logging

        # Fetch all outbox items
        with self._lock:
            rows = self._exec_unsafe(
                "SELECT id, table_name, column_name, row_id, text FROM vector_outbox ORDER BY created_at",
                []
            )

        if not rows:
            return

        logging.info(f"Flushing {len(rows)} items from vector_outbox")

        # Group by (table, column) for batch embedding
        grouped = {}
        for row in rows:
            key = (row["table_name"], row["column_name"])
            if key not in grouped:
                grouped[key] = {"ids": [], "texts": [], "outbox_ids": []}
            grouped[key]["ids"].append(row["row_id"])
            grouped[key]["texts"].append(row["text"])
            grouped[key]["outbox_ids"].append(row["id"])

        # Process each group independently (per-group error handling)
        succeeded_outbox_ids = []
        for (table, column), payload in grouped.items():
            try:
                logging.debug(f"Retrying {len(payload['texts'])} embeddings for ({table}, {column})")
                embeddings = await self.embedder.embed(payload["texts"])
                vectors = [np.array(emb, dtype=np.float32) for emb in embeddings]

                vector_store = self.get_or_create_vector_store(table, column)
                with self._lock:
                    vector_store.add_batch(payload["ids"], vectors)

                succeeded_outbox_ids.extend(payload["outbox_ids"])
                logging.debug(f"Successfully retried {len(payload['texts'])} embeddings for ({table}, {column})")
            except Exception as e:
                logging.error(f"Failed to flush outbox group ({table}, {column}): {e}")

        # Delete successfully processed items
        if succeeded_outbox_ids:
            placeholders = ",".join("?" for _ in succeeded_outbox_ids)
            with self._lock:
                self.conn.execute(
                    f"DELETE FROM vector_outbox WHERE id IN ({placeholders})",
                    succeeded_outbox_ids
                )
                self.conn.commit()
            logging.info(f"Removed {len(succeeded_outbox_ids)}/{len(rows)} processed items from vector_outbox")

    def _enqueue_embedding(self, table: str, column: str, ids: List, texts: List):
        """Enqueue embeddings for batch processing on transaction commit.

        Called by UpsertBuilder when inside a transaction context.

        Args:
            table: Table name
            column: Column name (vector field)
            ids: List of row IDs
            texts: List of texts to embed

        Raises:
            RuntimeError: If not inside a transaction
            DatabaseError: If embedder is None (can't process vector fields)
            ValueError: If ids and texts lengths don't match
        """
        # Validate inputs
        if len(ids) != len(texts):
            raise ValueError(
                f"Mismatch in _enqueue_embedding: {len(ids)} ids but {len(texts)} texts "
                f"for {table}.{column}"
            )

        if not self.embedder:
            raise DatabaseError(
                f"Cannot enqueue embeddings for {table}.{column} without an embedder. "
                "Set VOYAGE_API_KEY environment variable."
            )

        queue = emb_queue_var.get()
        if queue is None:
            raise RuntimeError(
                "No embedding queue bound. _enqueue_embedding() should only be called "
                "inside a transaction context."
            )

        key = (table, column)
        slot = queue.get(key)
        if slot is None:
            slot = {"ids": [], "texts": []}
            queue[key] = slot

        slot["ids"].extend(ids)
        slot["texts"].extend(texts)

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

        # Use sync _exec_unsafe under lock instead of async _exec
        with self._lock:
            rows = self._exec_unsafe(sql, ids)

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
