"""UPDATE query builder."""
import numpy as np
from typing import Dict, Any, List
from .mixins import PredMixin
from ..ir import UpdateIR
from ..binder import bind_update
from ..sqlgen import generate_update
from ..dialect.sqlite import SQLiteDialect
from ...results import Result


class UpdateBuilder(PredMixin):
    """Fluent builder for UPDATE queries."""

    def __init__(self, database, schema, table: str, data: Dict[str, Any]):
        self.db = database
        self.schema = schema
        self.table = table
        self.dialect = SQLiteDialect()
        self.data = data
        self._pred = None

        if self.table in schema.views:
            raise ValueError(f"Cannot UPDATE view '{table}'.")

    def execute(self):
        """Execute the UPDATE query."""
        data_with_auto = self.db._apply_auto_update(self.table, self.data)
        validated_data = self.db._validate_update_data(self.table, data_with_auto)
        serialized_data = self.db._serialize_json_fields(self.table, validated_data)

        ir = UpdateIR(table=self.table, assign=serialized_data, where=self._pred)
        bound = bind_update(ir, self.schema)
        sql, params = generate_update(bound, self.dialect)

        with self.db._lock:
            rows = self.db._exec_unsafe(sql, params)
            self.db.conn.commit()

        processed = []
        for row in rows:
            row = self.db._deserialize_json_fields(self.table, row)
            processed.append(row)

        # Update vectors for changed fields AFTER commit, BEFORE return
        self._update_vectors(processed)

        return Result(processed)

    def _update_vectors(self, rows: List[Dict[str, Any]]):
        """Update vectors for any vector-enabled fields that were modified"""
        if not rows or not self.db.embedder:
            return

        # Get table class to check for vector fields
        table_cls = self.schema.get_table(self.table)
        if not table_cls:
            return

        # Find vector-enabled fields that were actually updated
        vector_fields = []
        for field_name, field in table_cls.get_fields().items():
            if getattr(field, 'vector', False) and field_name in self.data:
                vector_fields.append(field_name)

        if not vector_fields:
            return

        # For each vector field that was updated
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

            # Get vector store
            vector_store = self.db.get_or_create_vector_store(self.table, field_name)

            # Only tombstone IDs that actually exist in the store (for updates, not initial inserts)
            existing_ids = [id_ for id_ in ids if vector_store.has_id(id_)]
            if existing_ids:
                vector_store.tombstone_batch(existing_ids)

            # Batch embed new content
            embeddings = self.db.embedder.embed(texts)
            vectors = [np.array(emb, dtype=np.float32) for emb in embeddings]

            # Append new vectors
            vector_store.add_batch(ids, vectors)

            # Remove any tombstones for these IDs (in case they were tombstoned above)
            if existing_ids:
                vector_store.tombstones.difference_update(existing_ids)
                vector_store._save_tombstones()
