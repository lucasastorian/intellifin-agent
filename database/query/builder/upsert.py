"""UPSERT query builder."""
import numpy as np
from typing import Dict, List, Union, Any, Optional
from ..ir import UpsertIR
from ..sqlgen import generate_upsert
from ..dialect.sqlite import SQLiteDialect
from ...results import Result


class UpsertBuilder:
    """Fluent builder for UPSERT (INSERT ... ON CONFLICT) queries."""

    def __init__(
        self,
        database,
        schema,
        table: str,
        values: Union[Dict[str, Any], List[Dict[str, Any]]],
        on_conflict: Union[str, List[str]],
        ignore_duplicates: bool = False,
        returning: str = "representation",
        count: Optional[str] = None,
        default_to_null: bool = False,
    ):
        self.db = database
        self.schema = schema
        self.table = table
        self.dialect = SQLiteDialect()
        self.rows = [values] if isinstance(values, dict) else values
        self.do_nothing = ignore_duplicates
        self.returning_all = returning == "representation"

        if self.table in schema.views:
            raise ValueError(f"Cannot UPSERT into view '{table}'.")

        if isinstance(on_conflict, str):
            self.on_conflict_cols = [c.strip() for c in on_conflict.split(",") if c.strip()]
        else:
            self.on_conflict_cols = list(on_conflict)

        tbl = self.schema.get_table(table)
        fields = tbl.get_fields()
        for c in self.on_conflict_cols:
            if c not in fields:
                raise ValueError(f"on_conflict column '{c}' does not exist in table '{table}'")

        if len(self.on_conflict_cols) == 1:
            fd = fields[self.on_conflict_cols[0]]
            if not (fd.primary_key or fd.unique):
                raise ValueError(
                    f"on_conflict column '{self.on_conflict_cols[0]}' must have UNIQUE constraint or be PRIMARY KEY."
                )
        else:
            declared_uniques = [tuple(u) for u in getattr(tbl, '__uniques__', ())]
            if tuple(self.on_conflict_cols) not in declared_uniques:
                raise ValueError(
                    f"on_conflict {tuple(self.on_conflict_cols)} does not match any declared composite UNIQUE."
                )

    def execute(self):
        """Execute the UPSERT query."""
        validated_rows = [self.db._validate_insert_data(self.table, row) for row in self.rows]
        with_defaults = [self.db._apply_runtime_defaults(self.table, row) for row in validated_rows]
        serialized_rows = [self.db._serialize_json_fields(self.table, row) for row in with_defaults]

        if not serialized_rows:
            return Result([])

        cols = list(serialized_rows[0].keys())
        num_cols = len(cols)
        max_vars = self.db._get_max_vars()

        total_params = num_cols * len(serialized_rows)
        use_executemany = total_params > max_vars

        all_results = []
        with self.db.transaction():
            if use_executemany:
                conflict_cols = ", ".join(self.dialect.q(c) for c in self.on_conflict_cols)
                if self.do_nothing:
                    action = "DO NOTHING"
                else:
                    update_clauses = [
                        f"{self.dialect.q(c)} = excluded.{self.dialect.q(c)}"
                        for c in cols if c not in self.on_conflict_cols
                    ]
                    action = f"DO UPDATE SET {', '.join(update_clauses)}" if update_clauses else "DO NOTHING"

                sql = (
                    f"INSERT INTO {self.dialect.q(self.table)} ({', '.join(self.dialect.q(c) for c in cols)}) "
                    f"VALUES ({', '.join(['?'] * num_cols)}) "
                    f"ON CONFLICT ({conflict_cols}) {action}"
                )
                param_rows = [[row[c] for c in cols] for row in serialized_rows]
                self.db.conn.executemany(sql, param_rows)
                return Result([], count=len(serialized_rows))
            else:
                batch_size = max(1, max_vars // num_cols)
                for i in range(0, len(serialized_rows), batch_size):
                    batch = serialized_rows[i:i + batch_size]
                    ir = UpsertIR(
                        table=self.table,
                        rows=batch,
                        on_conflict=self.on_conflict_cols,
                        do_nothing=self.do_nothing,
                        returning_all=self.returning_all,
                    )
                    sql, params = generate_upsert(ir, self.dialect)
                    rows = self.db._exec(sql, params)

                    for row in rows:
                        row = self.db._deserialize_json_fields(self.table, row)
                        all_results.append(row)

        # Embed and store vectors AFTER SQLite upsert, BEFORE return
        self._embed_vectors(all_results)

        return Result(all_results)

    def _embed_vectors(self, rows: List[Dict[str, Any]]):
        """Embed and store vectors for vector-enabled fields"""
        if not rows or not self.db.embedder:
            return

        # Get table class to check for vector fields
        table_cls = self.schema.get_table(self.table)
        if not table_cls:
            return

        # Find vector-enabled text fields
        vector_fields = []
        for field_name, field in table_cls.get_fields().items():
            if getattr(field, 'vector', False):
                vector_fields.append(field_name)

        if not vector_fields:
            return

        # Embed each vector field
        for field_name in vector_fields:
            # Collect texts and IDs
            texts = []
            ids = []
            for row in rows:
                if field_name in row and row[field_name]:
                    texts.append(row[field_name])
                    ids.append(row['id'])

            if not texts:
                continue

            # Batch embed
            embeddings = self.db.embedder.embed(texts)
            vectors = [np.array(emb, dtype=np.float32) for emb in embeddings]

            # Store in vector store
            vector_store = self.db.get_or_create_vector_store(self.table, field_name)
            vector_store.add_batch(ids, vectors)
