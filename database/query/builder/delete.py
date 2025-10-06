"""DELETE query builder."""
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

    def execute(self):
        """Execute the DELETE query."""
        ir = DeleteIR(table=self.table, where=self._pred)
        bound = bind_delete(ir, self.schema)
        sql, params = generate_delete(bound, self.dialect)
        rows = self.db._exec(sql, params)

        self.db.conn.commit()
        return Result(rows)
