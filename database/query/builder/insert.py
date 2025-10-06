"""INSERT query builder."""
from typing import Dict, List, Union, Any
from ..ir import InsertIR
from ..sqlgen import generate_insert
from ..dialect.sqlite import SQLiteDialect
from ...results import Result


class InsertBuilder:
    """Fluent builder for INSERT queries."""

    def __init__(self, database, schema, table: str, data: Union[Dict[str, Any], List[Dict[str, Any]]]):
        self.db = database
        self.schema = schema
        self.table = table
        self.dialect = SQLiteDialect()
        self.rows = [data] if isinstance(data, dict) else data

        if self.table in schema.views:
            raise ValueError(f"Cannot INSERT into view '{table}'.")

    def execute(self):
        """Execute the INSERT query."""
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
                sql = f"INSERT INTO {self.dialect.q(self.table)} ({', '.join(self.dialect.q(c) for c in cols)}) VALUES ({', '.join(['?'] * num_cols)})"
                param_rows = [[row[c] for c in cols] for row in serialized_rows]
                self.db.conn.executemany(sql, param_rows)
                return Result([], count=len(serialized_rows))
            else:
                batch_size = max(1, max_vars // num_cols)
                for i in range(0, len(serialized_rows), batch_size):
                    batch = serialized_rows[i:i + batch_size]
                    ir = InsertIR(table=self.table, rows=batch)
                    sql, params = generate_insert(ir, self.dialect)
                    rows = self.db._exec(sql, params)

                    for row in rows:
                        row = self.db._deserialize_json_fields(self.table, row)
                        all_results.append(row)

        return Result(all_results)
