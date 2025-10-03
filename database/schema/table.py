from typing import Dict, Any, Optional, Type, TYPE_CHECKING

if TYPE_CHECKING:
    from .schema import Schema

from .fields import FieldDescriptor


class TableMeta(type):
    def __new__(cls, name, bases, dct):
        fields = {}
        for k, v in list(dct.items()):
            if isinstance(v, FieldDescriptor):
                fields[k] = v
                del dct[k]
        dct['_schema'] = {'fields': fields}
        return super().__new__(cls, name, bases, dct)


class Table(metaclass=TableMeta):
    __tablename__: str = ""
    __uniques__: tuple = ()  # Immutable default; list of tuples for composite UNIQUE constraints

    def __init__(self):
        if not self.__tablename__:
            self.__tablename__ = self.__class__.__name__.lower() + "s"
        self._schema: Optional['Schema'] = None  # Will be set by Schema.table()

        # Initialize dynamic fields tracking for this table class
        if not hasattr(self.__class__, '_dynamic_fields'):
            self.__class__._dynamic_fields = set()

    @classmethod
    def get_fields(cls) -> Dict[str, FieldDescriptor]:
        """Get all fields in this table"""
        return cls._schema['fields']

    @classmethod
    def get_original_fields(cls) -> Dict[str, FieldDescriptor]:
        """Get only original schema fields (not dynamically added)"""
        dynamic_fields = getattr(cls, '_dynamic_fields', set())
        return {name: field for name, field in cls._schema['fields'].items()
                if name not in dynamic_fields}

    @classmethod
    def has_field(cls, name: str) -> bool:
        """Check if table has a field"""
        return name in cls._schema['fields']

    @classmethod
    def add_field(cls, name: str, field: FieldDescriptor) -> None:
        """Add a field to this table's schema (mutates the schema) or update existing field"""
        if not cls.has_field(name):
            cls._validate_column_name(name)

        cls._schema['fields'][name] = field

    @classmethod
    def _validate_column_name(cls, name: str) -> None:
        """Validate column name follows SQL identifier rules"""
        # SQLite reserved words (reuse from schema.py)
        reserved_words = {
            'abort', 'action', 'add', 'after', 'all', 'alter', 'analyze', 'and', 'as', 'asc',
            'attach', 'autoincrement', 'before', 'begin', 'between', 'by', 'cascade', 'case',
            'cast', 'check', 'collate', 'column', 'commit', 'conflict', 'constraint', 'create',
            'cross', 'current_date', 'current_time', 'current_timestamp', 'database', 'default',
            'deferrable', 'deferred', 'delete', 'desc', 'detach', 'distinct', 'drop', 'each',
            'else', 'end', 'escape', 'except', 'exclusive', 'exists', 'explain', 'fail', 'for',
            'foreign', 'from', 'full', 'glob', 'group', 'having', 'if', 'ignore', 'immediate',
            'in', 'index', 'indexed', 'initially', 'inner', 'insert', 'instead', 'intersect',
            'into', 'is', 'isnull', 'join', 'key', 'left', 'like', 'limit', 'match', 'natural',
            'no', 'not', 'notnull', 'null', 'of', 'offset', 'on', 'or', 'order', 'outer', 'plan',
            'pragma', 'primary', 'query', 'raise', 'recursive', 'references', 'regexp', 'reindex',
            'release', 'rename', 'replace', 'restrict', 'right', 'rollback', 'row', 'savepoint',
            'select', 'set', 'table', 'temp', 'temporary', 'then', 'to', 'transaction', 'trigger',
            'union', 'unique', 'update', 'using', 'vacuum', 'values', 'view', 'virtual', 'when',
            'where', 'with', 'without'
        }

        if not name or not isinstance(name, str):
            raise ValueError(f"Column name must be a non-empty string, got: {name}")

        if name.lower() in reserved_words:
            raise ValueError(f"Column name '{name}' is a SQLite reserved word. "
                             f"Please choose a different name.")

        if not name.replace('_', '').isalnum():
            raise ValueError(f"Column name '{name}' contains invalid characters. "
                             f"Use only letters, numbers, and underscores.")

        if name[0].isdigit():
            raise ValueError(f"Column name '{name}' cannot start with a digit.")

        if len(name) > 64:
            raise ValueError(f"Column name '{name}' is too long (max 64 characters).")

    @classmethod
    def remove_field(cls, name: str) -> Type['Table']:
        if name not in cls._schema['fields']:
            raise ValueError(f"Field '{name}' not found.")

        new_fields = {k: v for k, v in cls._schema['fields'].items() if k != name}
        new_schema = {'fields': new_fields}

        # Create new table class
        new_table_cls = type(cls.__name__, (Table,), {'_schema': new_schema, '__tablename__': cls.__tablename__})

        return new_table_cls

    @classmethod
    def to_dict(cls) -> Dict[str, Any]:
        """Serialize table definition to dictionary with separate original/dynamic fields"""
        return {
            'tablename': cls.__tablename__,
            'fields': {
                name: field.to_dict()
                for name, field in cls.get_original_fields().items()
            },
            'uniques': list(getattr(cls, '__uniques__', [])),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Type['Table']:
        """Recreate table class from dictionary"""
        # Get original fields and dynamic fields
        original_fields = data['fields']

        # Convert all field data to FieldDescriptor objects
        original_field_objects = {
            name: FieldDescriptor.from_dict(field_data)
            for name, field_data in original_fields.items()
        }

        # Create class attributes dict with field descriptors and table metadata
        class_attrs = {
            '__tablename__': data['tablename'],
            '__uniques__': data.get('uniques', []),
        }

        class_attrs.update(original_field_objects)

        # Create new table class - the metaclass will handle moving fields to _schema
        table_cls = type(
            f"Dynamic{data['tablename'].title()}",
            (Table,),
            class_attrs
        )

        return table_cls

    @classmethod
    def generate_create_sql(cls) -> str:
        if not cls.__tablename__:
            cls.__tablename__ = cls.__name__.lower() + "s"

        columns = []
        fks = []
        indices = []
        unique_clauses = []

        for name, field in cls.get_fields().items():
            column_sql = field.to_sql(name)
            if column_sql:
                columns.append(column_sql)

            if field.foreign_key:
                # Parse foreign key reference properly
                if '.' in field.foreign_key:
                    ref_table, ref_column = field.foreign_key.split('.', 1)
                    fk = f"FOREIGN KEY (`{name}`) REFERENCES `{ref_table}` (`{ref_column}`)"
                else:
                    fk = f"FOREIGN KEY (`{name}`) REFERENCES `{field.foreign_key}` (`id`)"
                if field.on_delete:
                    fk += f" ON DELETE {field.on_delete}"
                fks.append(fk)
            if field.index and not field.primary_key:
                indices.append(
                    f"CREATE INDEX IF NOT EXISTS idx_{cls.__tablename__}_{name} ON {cls.__tablename__}({name});")

        # Add composite UNIQUE constraints
        for cols in getattr(cls, '__uniques__', ()):
            # Validate columns exist
            for c in cols:
                if c not in cls.get_fields():
                    raise ValueError(
                        f"UNIQUE({', '.join(cols)}) references unknown column '{c}' in table '{cls.__tablename__}'"
                    )
            cols_sql = ", ".join(f"`{c}`" for c in cols)
            unique_clauses.append(f"UNIQUE ({cols_sql})")

        # Ensure we have at least one column for a valid SQL table
        if not columns:
            raise ValueError(
                f"Table '{cls.__tablename__}' has no columns.")

        base_sql = f"CREATE TABLE IF NOT EXISTS {cls.__tablename__} (\n"
        base_sql += ",\n".join(f"    {col}" for col in columns)
        if fks:
            base_sql += ",\n" + ",\n".join(f"    {fk}" for fk in fks)
        if unique_clauses:
            base_sql += ",\n" + ",\n".join(f"    {u}" for u in unique_clauses)
        base_sql += "\n);"

        full_sql = base_sql + "\n" + "\n".join(indices)

        # Append per-column FTS DDL
        fts_sql = cls._generate_fts_sql()
        if fts_sql:
            full_sql += "\n" + fts_sql

        return full_sql

    @classmethod
    def _generate_fts_sql(cls) -> str:
        """Generate FTS5 virtual tables and triggers for Text fields with fts=True"""
        from .fields import Text

        fts_chunks = []

        # Find primary key
        pk_field = None
        for name, field in cls.get_fields().items():
            if not field.primary_key:
                continue
            pk_field = name
            break

        # Generate FTS for each Text field with fts=True
        for col, field in cls.get_fields().items():
            if not isinstance(field, Text):
                continue
            if not getattr(field, "fts", False):
                continue

            # Validate PK exists for this FTS field
            if not pk_field:
                raise ValueError(f"Table '{cls.__tablename__}' requires a primary key for FTS content_rowid.")

            base = cls.__tablename__
            fts = f"`{base}__{col}__fts`"

            # VIRTUAL TABLE
            fts_chunks.append(
                f"CREATE VIRTUAL TABLE IF NOT EXISTS {fts}\n"
                f"USING fts5(\n"
                f"  `{col}`,\n"
                f"  content='{base}',\n"
                f"  content_rowid='{pk_field}',\n"
                f"  tokenize='unicode61 remove_diacritics 2 tokenchars \"-._%\"'\n"
                f");"
            )

            # Triggers
            fts_chunks.append(
                f"CREATE TRIGGER IF NOT EXISTS `{base}__{col}__fts_ai` AFTER INSERT ON `{base}` BEGIN\n"
                f"  INSERT INTO {fts}(rowid, `{col}`) VALUES (new.`{pk_field}`, new.`{col}`);\n"
                f"END;"
            )
            fts_chunks.append(
                f"CREATE TRIGGER IF NOT EXISTS `{base}__{col}__fts_ad` AFTER DELETE ON `{base}` BEGIN\n"
                f"  INSERT INTO {fts}({fts}, rowid, `{col}`) VALUES('delete', old.`{pk_field}`, old.`{col}`);\n"
                f"END;"
            )
            fts_chunks.append(
                f"CREATE TRIGGER IF NOT EXISTS `{base}__{col}__fts_au` AFTER UPDATE OF `{col}` ON `{base}` BEGIN\n"
                f"  INSERT INTO {fts}({fts}, rowid, `{col}`) VALUES('delete', old.`{pk_field}`, old.`{col}`);\n"
                f"  INSERT INTO {fts}(rowid, `{col}`) VALUES (new.`{pk_field}`, new.`{col}`);\n"
                f"END;"
            )

        return "\n".join(fts_chunks) if fts_chunks else ""

    def validate_insert_data(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Validate insert data against table schema"""
        if not isinstance(data, dict):
            raise ValueError(f"Insert data for table '{self.__tablename__}' must be a dictionary")

        validated_data = {}
        table_fields = self.get_fields()

        for field_name, value in data.items():
            if field_name in table_fields:
                field_desc = table_fields[field_name]
                field_desc.set_context(self.__tablename__, field_name, self._schema)
                validated_data[field_name] = field_desc.validate(value)
            else:
                raise ValueError(
                    f"Field '{field_name}' not found in table '{self.__tablename__}'")

        self._validate_required_fields(validated_data, table_fields)

        return validated_data

    def validate_update_data(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Validate update data against table schema"""
        if not isinstance(data, dict):
            raise ValueError(f"Update data for table '{self.__tablename__}' must be a dictionary")

        validated_data = {}
        table_fields = self.get_fields()

        for field_name, value in data.items():
            if field_name in table_fields:
                field_desc = table_fields[field_name]
                field_desc.set_context(self.__tablename__, field_name, self._schema)
                validated_data[field_name] = field_desc.validate(value)
            else:
                raise ValueError(
                    f"Field '{field_name}' not found in table '{self.__tablename__}'")

        return validated_data

    def _validate_required_fields(self, data: Dict[str, Any], table_fields: Dict[str, 'FieldDescriptor']) -> None:
        """Validate that all required fields are present (non-nullable without default)"""
        for field_name, field_desc in table_fields.items():
            # Field must be provided if it's non-nullable and has no default
            must_provide = (not field_desc.nullable) and (field_desc.default is None)
            if must_provide and field_name not in data:
                raise ValueError(
                    f"Required field '{field_name}' is missing from insert data for table '{self.__tablename__}'")

    def __str__(self):
        return self.__tablename__
