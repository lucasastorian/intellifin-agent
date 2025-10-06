"""UPDATE query builder."""
from typing import Dict, Any
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
        rows = self.db._exec(sql, params)

        processed = []
        for row in rows:
            row = self.db._deserialize_json_fields(self.table, row)
            processed.append(row)

        self.db.conn.commit()
        return Result(processed)
