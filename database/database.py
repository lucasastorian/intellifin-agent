import re
import asyncio
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
from .context import emb_queue_var
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

            # Warn if closing during active embedding batch
            if emb_queue_var.get() is not None:
                import logging
                logging.warning(
                    "Closing database connection with active embedding batch. "
                    "Queued embeddings will be lost."
                )
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

    @asynccontextmanager
    async def batch_embeddings(self):
        """Batch all embedding generation within this context.

        Defers embedding generation and vector store writes until context exit,
        allowing hundreds/thousands of texts to be embedded in a single Voyage API call.

        SQL operations run normally (no transaction management). Only embeddings are batched.

        ASYNCIO SAFETY:
        ✓ Safe with: await, asyncio.as_completed(), asyncio.gather(), asyncio.create_task()
        ✓ ContextVars propagate to child tasks - all share the same embedding queue

        Without this context:
        - Each upsert with vector fields generates embeddings immediately
        - Example: 100 filings → 100+ Voyage API calls

        With this context:
        - All upserts queue embeddings, flush at exit
        - Example: 100 filings → ~8 Voyage API calls (batches of 128)

        Example:
            # Batch embeddings across all filings
            async with db.batch_embeddings():
                tasks = [filing.upsert() for filing in filings]
                for coro in asyncio.as_completed(tasks):
                    await coro
            # Exit: generate all embeddings in minimal API calls

        Failed embeddings are written to the outbox for later retry via
        flush_vector_outbox().
        """
        if self._closed:
            raise DatabaseError("Cannot start embedding batch on closed database connection")

        # Create queue for this context
        queue = {}
        token = emb_queue_var.set(queue)

        try:
            yield

            # Flush: generate embeddings and write to vector stores
            # No shield - let cancellations propagate
            failures = await self._flush_embedding_queue(queue)

            # Write failed embeddings to outbox for retry
            if failures:
                await self._write_failed_groups_to_outbox(failures)

        finally:
            # Clean up
            emb_queue_var.reset(token)

    async def _flush_embedding_queue(self, queue: Dict[Tuple[str, str], List[Dict[str, List]]]):
        """Embed and store all queued texts, choosing API based on schema.

        For contextualized fields: uses voyage-context-3 with preserved document boundaries
        For standard fields: flattens and uses standard embedding API

        Args:
            queue: Maps (table, column) -> List of documents, where each document is {"ids": [...], "texts": [...]}

        Returns:
            List of failed groups for outbox retry
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

        for (table, column), documents in queue.items():
            if not documents:
                continue

            # Check schema for contextualized flag
            field = self.schema.get_table(table).get_fields()[column]
            is_contextualized = getattr(field, 'contextualized', False)

            try:
                if is_contextualized:
                    # Contextualized embeddings - preserve document boundaries
                    inputs = [doc["texts"] for doc in documents]
                    total_chunks = sum(len(doc["texts"]) for doc in documents)

                    logging.debug(
                        f"Contextualized embed {table}.{column}: "
                        f"{len(documents)} documents, {total_chunks} chunks"
                    )

                    # Returns nested list: List[List[float]] (one inner list per document)
                    nested_embeddings = await self.embedder.contextualized_embed(
                        inputs=inputs,
                        model="voyage-context-3",
                        input_type="document",
                        output_dimension=self.embedder.dimensions
                    )

                    # Flatten nested embeddings for vector store write
                    embeddings = [emb for doc_embs in nested_embeddings for emb in doc_embs]

                else:
                    # Standard embeddings - flatten all documents
                    flat_texts = [text for doc in documents for text in doc["texts"]]

                    logging.debug(
                        f"Standard embed {table}.{column}: "
                        f"{len(documents)} documents, {len(flat_texts)} chunks"
                    )

                    embeddings = await self.embedder.embed(flat_texts)

                # Collect all IDs and convert embeddings to numpy
                all_ids = [row_id for doc in documents for row_id in doc["ids"]]
                vectors = [np.array(emb, dtype=np.float32) for emb in embeddings]

                if len(vectors) != len(all_ids):
                    raise ValueError(
                        f"Embedding count mismatch: got {len(vectors)} embeddings "
                        f"for {len(all_ids)} IDs in {table}.{column}"
                    )

                # Write to vector store
                vs = self.get_or_create_vector_store(table, column)
                with self._lock:
                    vs.add_batch(all_ids, vectors)

            except Exception as e:
                logging.error(f"Embedding failed for {table}.{column}: {e}")
                # Collect texts for outbox (flattened)
                all_ids = [row_id for doc in documents for row_id in doc["ids"]]
                all_texts = [text for doc in documents for text in doc["texts"]]
                failed_groups.append((table, column, all_ids, all_texts))

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
        """Enqueue embeddings for batch processing.

        Each call represents ONE document's worth of chunks. Document boundaries
        are preserved for contextualized embeddings.

        Called by UpsertBuilder when inside batch_embeddings() context.
        If not inside context, embeddings should be generated immediately instead.

        Args:
            table: Table name
            column: Column name (vector field)
            ids: List of row IDs for this document
            texts: List of texts to embed for this document

        Raises:
            DatabaseError: If embedder is None or wrong task context
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
                f"Cannot enqueue embeddings for {table}.{column} without embedder. "
                "Set VOYAGE_API_KEY environment variable."
            )

        queue = emb_queue_var.get()
        if queue is None:
            # Not inside batch_embeddings() context
            # This is a programming error - caller should check and embed immediately
            raise RuntimeError(
                f"Cannot enqueue embeddings for {table}.{column} outside batch_embeddings() context. "
                f"Either use 'async with db.batch_embeddings():' or generate embeddings immediately."
            )

        # Append this document (preserves document boundaries)
        key = (table, column)
        if key not in queue:
            queue[key] = []

        queue[key].append({"ids": ids, "texts": texts})

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
        """Execute SQL with error handling and return rows as dicts.

        Includes optional slow-query logging controlled by INTELLIFIN_SLOW_SQL_MS (default 2000ms).
        """
        import time
        import os
        t0 = time.time()
        with self._lock:
            rows = self._exec_unsafe(sql, params)
            # Commit after successful execution (SQLite is NOT in autocommit mode by default)
            self.conn.commit()
        elapsed_ms = (time.time() - t0) * 1000.0
        try:
            threshold = int(os.environ.get("INTELLIFIN_SLOW_SQL_MS", "2000"))
        except Exception:
            threshold = 2000
        if elapsed_ms >= threshold:
            import logging
            logging.warning(f"Slow SQL ({elapsed_ms:.0f} ms): {sql[:200]} ... params={params[:5] if isinstance(params, (list, tuple)) else ''}")
        return rows

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
