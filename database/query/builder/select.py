"""SELECT query builder."""
from .mixins import PredMixin, SelectMixin
from ..ir import SelectIR, KeywordFTS, Col
from ..binder import bind_select
from ..planner import plan_select
from ..sqlgen import generate_select
from ..dialect.sqlite import SQLiteDialect
from ...results import Result


class SelectBuilder(PredMixin, SelectMixin):
    """Fluent builder for SELECT queries."""

    def __init__(self, database, schema, table: str):
        self.db = database
        self.schema = schema
        self.table = table
        self.dialect = SQLiteDialect()
        self._pred = None
        self.selected = None
        self.order_by = []
        self.limit_n = None

    def keyword_search(self, query: str, column: str):
        """Full-text search on FTS-enabled column."""
        self._and(KeywordFTS(Col(column), query))
        self.order_by = []
        return self

    def execute(self):
        """Execute the SELECT query."""
        ir = SelectIR(
            table=self.table,
            columns=self.selected,
            where=self._pred,
            order=self.order_by,
            limit=self.limit_n,
        )
        bound = bind_select(ir, self.schema)
        planned, _ = plan_select(bound, self.schema, self.dialect)
        sql, params = generate_select(planned, self.dialect)
        rows = self.db._exec(sql, params)

        processed = []
        for row in rows:
            row = self.db._deserialize_json_fields(self.table, row)
            processed.append(row)

        return Result(processed)
