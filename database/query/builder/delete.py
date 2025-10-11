"""DELETE query builder."""
import asyncio
from typing import List, Dict, Any
from .mixins import PredMixin
from ..ir import DeleteIR
from ..binder import bind_delete
from ..sqlgen import generate_delete
from ..dialect.sqlite import SQLiteDialect
from ...results import Result


class DeleteBuilder(PredMixin):
    """Fluent builder for DELETE queries."""

    def __init__(self, database, schema, table: str):
        self.db = database
        self.schema = schema
        self.table = table
        self.dialect = SQLiteDialect()
        self._pred = None

        if self.table in schema.views:
            raise ValueError(f"Cannot DELETE from view '{table}'.")

    async def execute(self):
        """Execute the DELETE query."""
        ir = DeleteIR(table=self.table, where=self._pred)
        bound = bind_delete(ir, self.schema)
        sql, params = generate_delete(bound, self.dialect)

        def _exec_delete():
            with self.db._lock:
                rows = self.db._exec_unsafe(sql, params)
                self.db.conn.commit()
                return rows

        rows = await asyncio.to_thread(_exec_delete)

        # Tombstone vectors AFTER delete, BEFORE return
        self._tombstone_vectors(rows)

        return Result(rows)

    def _tombstone_vectors(self, rows: List[Dict[str, Any]]):
        """Tombstone vectors for deleted rows"""
        if not rows:
            return

        # Get table class to check for vector fields
        table_cls = self.schema.get_table(self.table)
        if not table_cls:
            return

        # Find all vector-enabled fields
        vector_fields = []
        for field_name, field in table_cls.get_fields().items():
            if getattr(field, 'vector', False):
                vector_fields.append(field_name)

        if not vector_fields:
            return

        # Collect all deleted IDs
        deleted_ids = [row['id'] for row in rows]

        # Tombstone in all vector stores for this table
        for field_name in vector_fields:
            if (self.table, field_name) in self.db.vector_stores:
                vector_store = self.db.vector_stores[(self.table, field_name)]
                vector_store.tombstone_batch(deleted_ids)
