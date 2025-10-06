from typing import Dict, Any, Optional, List, Union

from ..schema.schema import Schema
from ..schema.fields import Boolean


class MongoToSqlConverter:

    def __init__(self, schema: Schema):
        self.schema = schema
        self._fts_rank_exprs = []
        self._order_rank_forced = False

    def _quote_identifier(self, identifier: str) -> str:
        return f"`{identifier}`"

    def convert(self, mongo_obj: dict) -> str:
        if "insert" in mongo_obj:
            return self.convert_insert(mongo_obj)

        table = mongo_obj.get("collection")
        find_filter = mongo_obj.get("find", {})
        projection = mongo_obj.get("projection", {})
        sort_clause = mongo_obj.get("sort", [])
        limit_val = mongo_obj.get("limit")

        self._current_table = table
        table_alias = "t"

        main_columns = self._build_main_columns(projection, table_alias)
        from_clause = f"FROM {self._quote_identifier(table)} {table_alias}"
        sql = f"SELECT {', '.join(main_columns)} {from_clause}"

        where_sql, params = self.build_where_sql(find_filter, table_alias)
        self._last_select_params = params
        if where_sql:
            sql += f" WHERE {where_sql}"

        if sort_clause:
            order_sql = self.build_order_by_sql(sort_clause, table_alias)
            if order_sql:
                sql += f" ORDER BY {order_sql}"

        if limit_val is not None:
            limit_int = int(limit_val)
            if limit_int < 0:
                raise ValueError(f"LIMIT must be >= 0, got {limit_int}")
            sql += f" LIMIT {limit_int}"

        return sql + ";"

    def get_select_params(self, mongo_obj: dict) -> list:
        return getattr(self, "_last_select_params", [])

    def convert_select(self, *, table: str, find: Dict[str, Any], projection: Dict[str, int], sort: list, limit: Optional[int]) -> str:
        self._current_table = table
        self._fts_rank_exprs = []
        self._order_rank_forced = False
        table_alias = "t"

        if projection:
            main_columns = [f"{table_alias}.`{f}`" for f in projection.keys()]
        else:
            main_columns = [f"{table_alias}.*"]

        sql = f"SELECT {', '.join(main_columns)} FROM `{table}` {table_alias}"

        where_sql, params = self.build_where_sql(find, table_alias)
        self._last_select_params = params
        if where_sql:
            sql += f" WHERE {where_sql}"

        if self._fts_rank_exprs:
            _, rank_expr_sql, rank_param = self._fts_rank_exprs[-1]
            sql += f" ORDER BY {rank_expr_sql} ASC"
            self._order_rank_forced = True
            self._last_select_params.append(rank_param)
        elif sort:
            order_sql = self.build_order_by_sql(sort, table_alias)
            if order_sql:
                sql += f" ORDER BY {order_sql}"

        if limit is not None:
            limit_int = int(limit)
            if limit_int < 0:
                raise ValueError(f"LIMIT must be >= 0, got {limit_int}")
            sql += f" LIMIT {limit_int}"

        return sql + ";"

    def get_last_select_params(self) -> list:
        return getattr(self, "_last_select_params", [])

    def _get_primary_key_field(self, table_name: str) -> str:
        table_cls = self.schema.get_table(table_name)
        for field_name, field_desc in table_cls.get_fields().items():
            if field_desc.primary_key:
                return field_name
        raise ValueError(f"No primary key field found for table '{table_name}'")

    def _build_main_columns(self, projection: Dict[str, int], table_alias: str) -> List[str]:
        if projection:
            return [f"{table_alias}.`{field}`" for field in projection.keys()]
        else:
            return [f"{table_alias}.*"]

    def build_where_sql(self, find_filter: Dict[str, Any], table_alias: Optional[str] = None) -> tuple:
        if not find_filter:
            return "", []

        if isinstance(find_filter, dict):
            if "$and" in find_filter:
                parts_params = [self.build_where_sql(sub, table_alias) for sub in find_filter["$and"]]
                parts = [p[0] for p in parts_params if p[0]]
                params = [p for pp in parts_params for p in pp[1]]
                sql = "(" + ") AND (".join(parts) + ")" if parts else ""
                return sql, params
            elif "$or" in find_filter:
                parts_params = [self.build_where_sql(sub, table_alias) for sub in find_filter["$or"]]
                parts = [p[0] for p in parts_params if p[0]]
                params = [p for pp in parts_params for p in pp[1]]
                sql = "(" + ") OR (".join(parts) + ")" if parts else ""
                return sql, params
            else:
                return self.build_basic_conditions(find_filter, table_alias)

        if isinstance(find_filter, list):
            parts_params = [self.build_where_sql(sub, table_alias) for sub in find_filter]
            parts = [p[0] for p in parts_params if p[0]]
            params = [p for pp in parts_params for p in pp[1]]
            sql = "(" + ") AND (".join(parts) + ")" if parts else ""
            return sql, params

        return "", []

    def build_basic_conditions(self, condition_dict: Dict[str, Any], table_alias: Optional[str] = None) -> tuple:
        clauses = []
        params = []

        for field, expr in condition_dict.items():
            sql_field = self._convert_field_name(field, table_alias)
            expr = self._normalize_boolean_value(field, expr)

            if isinstance(expr, dict):
                for op, val in expr.items():
                    if op == "$in":
                        if not val:
                            clauses.append("1=0")
                        else:
                            placeholders = ", ".join("?" for _ in val)
                            clauses.append(f"{sql_field} IN ({placeholders})")
                            params.extend(val)

                    elif op == "$nin":
                        if not val:
                            clauses.append("1=1")
                        else:
                            placeholders = ", ".join("?" for _ in val)
                            clauses.append(f"{sql_field} NOT IN ({placeholders})")
                            params.extend(val)

                    elif op == "$contains":
                        if isinstance(val, (list, tuple)):
                            if not val:
                                clauses.append("1=0")
                            else:
                                placeholders = ", ".join("?" for _ in val)
                                array_clause = (
                                    f"(json_type({sql_field}) = 'array' AND "
                                    f"EXISTS (SELECT 1 FROM json_each({sql_field}) WHERE value IN ({placeholders})))"
                                )
                                object_clause = (
                                    f"(json_type({sql_field}) = 'object' AND "
                                    f"EXISTS (SELECT 1 FROM json_each({sql_field}) WHERE key IN ({placeholders})))"
                                )
                                clauses.append(f"({array_clause} OR {object_clause})")
                                params.extend(val)
                                params.extend(val)
                        else:
                            array_clause = (
                                f"(json_type({sql_field}) = 'array' AND "
                                f"EXISTS (SELECT 1 FROM json_each({sql_field}) WHERE value = ?))"
                            )
                            object_clause = (
                                f"(json_type({sql_field}) = 'object' AND "
                                f"EXISTS (SELECT 1 FROM json_each({sql_field}) WHERE key = ?))"
                            )
                            clauses.append(f"({array_clause} OR {object_clause})")
                            params.append(val)
                            params.append(val)

                    elif op == "$ilike":
                        clauses.append(f"{sql_field} LIKE ? COLLATE NOCASE")
                        params.append(val)

                    elif op == "$keyword":
                        if self._current_table in self.schema.views:
                            vcls = self.schema.views[self._current_table]
                            vcls._schema = self.schema

                            view_fields = vcls.get_fields()
                            view_field = view_fields.get(field)
                            if not view_field:
                                raise ValueError(f"Field '{field}' does not exist in view '{self._current_table}'")

                            underlying_table = getattr(view_field, "_view_src_table", None)
                            underlying_field = getattr(view_field, "_view_src_field", None)

                            if not (underlying_table and underlying_field):
                                raise ValueError(f"Cannot resolve view field '{field}' to underlying table")

                            underlying_tbl_cls = self.schema.get_table(underlying_table)
                            underlying_fields = underlying_tbl_cls.get_fields()
                            underlying_fd = underlying_fields.get(underlying_field)

                            if not getattr(underlying_fd, "fts", False):
                                raise ValueError(
                                    f"View field '{field}' maps to '{underlying_table}.{underlying_field}' "
                                    f"which is not FTS-enabled"
                                )

                            underlying_pk = self._get_primary_key_field(underlying_table)
                            view_pk_field = None
                            for alias, vf in view_fields.items():
                                src_t = getattr(vf, "_view_src_table", None)
                                src_f = getattr(vf, "_view_src_field", None)
                                if src_t == underlying_table and src_f == underlying_pk:
                                    view_pk_field = alias
                                    break

                            if not view_pk_field:
                                raise ValueError(
                                    f"View '{self._current_table}' must expose '{underlying_table}.{underlying_pk}' "
                                    f"to support FTS on '{field}'"
                                )

                            match = " ".join(str(val).split())
                            if not match:
                                clauses.append("1=0")
                                continue

                            fts_table_name = f"{underlying_table}__{underlying_field}__fts"
                            fts_table = f"`{fts_table_name}`"
                            main = table_alias or "t"

                            clauses.append(
                                f"EXISTS (SELECT 1 FROM {fts_table} "
                                f"WHERE rowid = {main}.`{view_pk_field}` AND {fts_table} MATCH ?)"
                            )
                            params.append(match)

                            rank_expr = (
                                f"(SELECT bm25({fts_table_name}) FROM {fts_table} "
                                f"WHERE rowid = {main}.`{view_pk_field}` AND {fts_table} MATCH ?)"
                            )
                            self._fts_rank_exprs.append((field, rank_expr, match))
                            continue

                        tbl = self.schema.get_table(self._current_table)
                        fields = tbl.get_fields()
                        fd = fields.get(field)
                        if not getattr(fd, "fts", False):
                            raise ValueError(f"Field '{field}' is not FTS-enabled (fts=True) on '{tbl.__tablename__}'")

                        match = " ".join(str(val).split())
                        if not match:
                            clauses.append("1=0")
                            continue

                        pk = self._get_primary_key_field(tbl.__tablename__)
                        fts_table_name = f"{tbl.__tablename__}__{field}__fts"
                        fts_table = f"`{fts_table_name}`"
                        main = table_alias or "t"

                        clauses.append(
                            f"EXISTS (SELECT 1 FROM {fts_table} "
                            f"WHERE rowid = {main}.`{pk}` AND {fts_table} MATCH ?)"
                        )
                        params.append(match)

                        rank_expr = (
                            f"(SELECT bm25({fts_table_name}) FROM {fts_table} "
                            f"WHERE rowid = {main}.`{pk}` AND {fts_table} MATCH ?)"
                        )
                        self._fts_rank_exprs.append((field, rank_expr, match))

                    elif op == "$regex":
                        pattern = str(val)
                        regex_meta = set('.^$()+{}|\\')
                        glob_meta = set('*?[]')
                        has_regex = any(ch in regex_meta for ch in pattern)
                        has_glob = any(ch in glob_meta for ch in pattern)

                        if has_regex:
                            clauses.append(f"{sql_field} REGEXP ?")
                            params.append(pattern)
                        elif has_glob:
                            clauses.append(f"{sql_field} GLOB ?")
                            params.append(pattern)
                        else:
                            escaped = self._escape_like(pattern)
                            clauses.append(f"{sql_field} LIKE ? ESCAPE '\\' COLLATE NOCASE")
                            params.append(f"%{escaped}%")

                    elif op in {"$gt", "$gte", "$lt", "$lte", "$eq", "$ne"}:
                        cmp_map = {"$gt": ">", "$gte": ">=", "$lt": "<", "$lte": "<=", "$eq": "=", "$ne": "<>"}
                        if val is None and op in {"$eq", "$ne"}:
                            clause = f"{sql_field} IS NULL" if op == "$eq" else f"{sql_field} IS NOT NULL"
                            clauses.append(clause)
                        else:
                            clauses.append(f"{sql_field} {cmp_map[op]} ?")
                            params.append(val)
            else:
                if expr is None:
                    clauses.append(f"{sql_field} IS NULL")
                else:
                    clauses.append(f"{sql_field} = ?")
                    params.append(expr)

        return " AND ".join(clauses), params

    def _normalize_boolean_value(self, field: str, expr: Any) -> Any:
        if not hasattr(self, '_current_table'):
            return expr

        table_cls = self.schema.tables.get(self._current_table, None)
        if not table_cls:
            return expr

        fields = table_cls.get_fields()
        field_desc = fields.get(field)

        if isinstance(field_desc, Boolean):
            if isinstance(expr, bool):
                return 1 if expr else 0
            elif isinstance(expr, dict):
                normalized = {}
                for op, val in expr.items():
                    if isinstance(val, bool):
                        normalized[op] = 1 if val else 0
                    elif isinstance(val, (list, tuple)) and op in ('$in', '$nin'):
                        normalized[op] = [1 if v is True else 0 if v is False else v for v in val]
                    else:
                        normalized[op] = val
                return normalized

        return expr

    def _convert_field_name(self, field: str, table_alias: Optional[str] = None) -> str:
        if '.' in field:
            table_context = getattr(self, '_current_table', 'unknown')
            raise ValueError(f"Dotted field '{field}' is not supported in table '{table_context}'")

        if table_alias:
            return f"{table_alias}.`{field}`"
        else:
            return f"`{field}`"

    def build_order_by_sql(self, sort_list: List[tuple], table_alias: Optional[str] = None) -> str:
        if not sort_list:
            return ""
        order_parts = []
        for field, direction in sort_list:
            dir_sql = "ASC" if direction == 1 else "DESC"
            sql_field = self._convert_field_name(field, table_alias)
            order_parts.append(f"{sql_field} {dir_sql}")
        return ", ".join(order_parts)

    @staticmethod
    def escape_quotes(s: str) -> str:
        return s.replace("'", "''")

    @staticmethod
    def _escape_like(s: str) -> str:
        return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

    def convert_insert(self, mongo_obj: dict) -> str:
        table = mongo_obj.get("collection")
        insert_data = mongo_obj.get("insert")

        if not table or insert_data is None:
            raise ValueError("Insert operation requires 'collection' and 'insert' keys")

        if isinstance(insert_data, list):
            return self._convert_bulk_insert(table, insert_data)
        else:
            return self._convert_single_insert(table, insert_data)

    def _convert_single_insert(self, table: str, data: Dict[str, Any]) -> str:
        if not data:
            raise ValueError("Insert data cannot be empty")

        columns = list(data.keys())
        placeholders = ['?' for _ in columns]

        columns_str = ', '.join(f'`{col}`' for col in columns)
        placeholders_str = ', '.join(placeholders)

        pk_field = self._get_primary_key_field(table)

        return f"INSERT INTO {self._quote_identifier(table)} ({columns_str}) VALUES ({placeholders_str}) RETURNING `{pk_field}`"

    def _convert_bulk_insert(self, table: str, data_list: List[Dict[str, Any]]) -> str:
        if not data_list:
            raise ValueError("Bulk insert data cannot be empty")

        first_item = data_list[0]
        columns = list(first_item.keys())

        for i, item in enumerate(data_list):
            if set(item.keys()) != set(columns):
                raise ValueError(f"Item {i} has different keys than first item")

        columns_str = ', '.join(f'`{col}`' for col in columns)

        single_row_placeholders = ', '.join(['?' for _ in columns])
        all_rows_placeholders = ', '.join([f'({single_row_placeholders})' for _ in data_list])

        pk_field = self._get_primary_key_field(table)

        return f"INSERT INTO {self._quote_identifier(table)} ({columns_str}) VALUES {all_rows_placeholders} RETURNING `{pk_field}`"

    def get_insert_values(self, mongo_obj: dict) -> Union[tuple, List[tuple]]:
        insert_data = mongo_obj.get("insert")

        if isinstance(insert_data, list):
            if not insert_data:
                return []

            columns = list(insert_data[0].keys())
            return [tuple(item[col] for col in columns) for item in insert_data]
        else:
            return tuple(insert_data.values())

    def convert_update(self, mongo_obj: dict) -> str:
        table = mongo_obj.get("collection")
        update_data = mongo_obj.get("update")
        find_filter = mongo_obj.get("find", {})

        if not table or not update_data:
            raise ValueError("Update operation requires 'collection' and 'update' keys")

        set_clauses = []
        for field in update_data.keys():
            set_clauses.append(f"`{field}` = ?")
        set_clause = ", ".join(set_clauses)

        where_sql, where_params = self.build_where_sql(find_filter)
        self._last_update_params = where_params

        sql = f"UPDATE {self._quote_identifier(table)} SET {set_clause}"
        if where_sql:
            sql += f" WHERE {where_sql}"

        return sql

    def get_update_params(self, mongo_obj: dict) -> list:
        update_data = mongo_obj.get("update", {})
        update_values = list(update_data.values())
        where_params = getattr(self, "_last_update_params", [])
        return update_values + where_params

    def convert_delete(self, mongo_obj: dict) -> str:
        table = mongo_obj.get("collection")
        find_filter = mongo_obj.get("find", {})

        if not table:
            raise ValueError("Delete operation requires 'collection' key")

        where_sql, where_params = self.build_where_sql(find_filter)
        self._last_delete_params = where_params

        sql = f"DELETE FROM {self._quote_identifier(table)}"
        if where_sql:
            sql += f" WHERE {where_sql}"

        return sql

    def get_delete_params(self, mongo_obj: dict) -> list:
        return getattr(self, "_last_delete_params", [])
