from typing import Dict, Any, Optional, Literal, TYPE_CHECKING, Union, List
import re
from datetime import datetime


if TYPE_CHECKING:
    from .schema import Schema


# ISO 8601 timestamp pattern
_ISO_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+\-]\d{2}:\d{2})?$"
)


class FieldDescriptor:
    """Base for field types."""

    def __init__(self, sql_type: str, primary_key: bool = False, nullable: bool = True,
                 default: Optional[Any] = None, foreign_key: Optional[str] = None,
                 unique: bool = False, index: bool = False,
                 on_delete: str = "CASCADE", auto_update: bool = False):
        self.sql_type = sql_type
        self.primary_key = primary_key
        self.nullable = nullable
        self.default = default
        self.foreign_key = foreign_key  # e.g., "documents.id"
        self.unique = unique
        self.index = index
        self.on_delete = on_delete if foreign_key else None
        self.auto_update = auto_update

        self._table_name: Optional[str] = None
        self._field_name: Optional[str] = None
        self._schema: Optional['Schema'] = None

    def validate(self, value: Any) -> Any:
        """Validate and convert value for this field"""
        if value is None:
            if not self.nullable:
                raise ValueError(f"Field '{self._field_name or 'unknown'}' cannot be null")
            return None
        
        # Apply field-specific validation
        return self._validate_type(value)
    
    def _validate_type(self, value: Any) -> Any:
        """Override in subclasses for type-specific validation"""
        return value

    def to_sql(self, name: str) -> str:
        sql = f"`{name}` {self.sql_type}"
        if self.primary_key and self.sql_type == "INTEGER":
            sql += " PRIMARY KEY AUTOINCREMENT"
        elif self.primary_key:
            sql += " PRIMARY KEY"
        if not self.nullable:
            sql += " NOT NULL"
        if self.unique:
            sql += " UNIQUE"
        if self.default is not None:
            if isinstance(self.default, str) and self.default.startswith("CURRENT_"):
                # SQL keyword like CURRENT_TIMESTAMP
                sql += f" DEFAULT {self.default}"
            elif isinstance(self.default, (int, float, bool)):
                # Numeric/boolean literals
                sql += f" DEFAULT {self.default}"
            elif isinstance(self.default, str):
                # String literal - escape single quotes by doubling them
                escaped = self.default.replace("'", "''")
                sql += f" DEFAULT '{escaped}'"
            else:
                # Other types - convert to string and escape
                escaped = str(self.default).replace("'", "''")
                sql += f" DEFAULT '{escaped}'"
        return sql

    def set_context(self, table_name: str, field_name: str, schema: 'Schema') -> None:
        """Set context for better error messages"""
        self._table_name = table_name
        self._field_name = field_name
        self._schema = schema

    def to_dict(self) -> Dict[str, Any]:
        """Serialize field descriptor to dictionary"""
        return {
            'kind': self.__class__.__name__,
            'sql_type': self.sql_type,
            'primary_key': self.primary_key,
            'nullable': self.nullable,
            'default': self.default,
            'foreign_key': self.foreign_key,
            'unique': self.unique,
            'index': self.index,
            'on_delete': self.on_delete,
            'auto_update': self.auto_update
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'FieldDescriptor':
        """Deserialize field descriptor from dictionary"""
        kind = data.get('kind')
        if kind == 'JSONField':
            return JSONField.from_dict(data)
        elif kind == 'Date':
            return Date.from_dict(data)
        elif kind == 'Timestamp':
            return Timestamp.from_dict(data)
        elif kind == 'Boolean':
            return Boolean.from_dict(data)
        elif kind == 'Enum':
            return Enum.from_dict(data)
        elif kind == 'Serial':
            return Serial.from_dict(data)
        elif kind == 'Integer':
            return Integer.from_dict(data)
        elif kind == 'Text':
            return Text.from_dict(data)
        elif kind == 'Float':
            return Float.from_dict(data)
        else:
            raise ValueError(f"Unknown field kind: {kind}")


# Common Field Types
class Serial(FieldDescriptor):
    def __init__(self, **kwargs):
        if 'foreign_key' in kwargs:
            raise ValueError("Serial is for PKs only; use Integer for FKs.")
        super().__init__("INTEGER", primary_key=True, nullable=False, **kwargs)
    
    def _validate_type(self, value: Any) -> int:
        """Validate and convert value to integer for serial field"""
        if isinstance(value, (int, float)):
            return int(value)
        elif isinstance(value, str) and value.isdigit():
            return int(value)
        else:
            raise ValueError(f"Serial field '{self._field_name or 'unknown'}' expects integer, got {type(value).__name__}: {value}")
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Serial':
        """Deserialize field descriptor from dictionary"""
        return cls(
            default=data['default'],
            unique=data['unique'],
            index=data['index'],
            auto_update=data.get('auto_update', False)
        )


class Text(FieldDescriptor):
    def __init__(self, index: bool = False, fts: bool = False, vector: bool = False, **kwargs):
        super().__init__("TEXT", index=index, **kwargs)
        self.fts = fts
        self.vector = vector

    def _validate_type(self, value: Any) -> str:
        """Validate and convert value to string"""
        return str(value)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize field descriptor to dictionary"""
        data = super().to_dict()
        data['fts'] = self.fts
        data['vector'] = self.vector
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Text':
        """Deserialize field descriptor from dictionary"""
        return cls(
            index=data['index'],
            fts=data.get('fts', False),
            vector=data.get('vector', False),
            primary_key=data['primary_key'],
            nullable=data['nullable'],
            default=data['default'],
            foreign_key=data['foreign_key'],
            unique=data['unique'],
            on_delete=data['on_delete'],
            auto_update=data.get('auto_update', False)
        )


class Integer(FieldDescriptor):
    def __init__(self, foreign_key: Optional[str] = None, on_delete: str = "CASCADE", **kwargs):
        super().__init__("INTEGER", foreign_key=foreign_key, on_delete=on_delete, **kwargs)

    def _validate_type(self, value: Any) -> int:
        """Validate and convert value to integer"""
        if isinstance(value, bool):
            return int(value)
        elif isinstance(value, (int, float)):
            return int(value)
        elif isinstance(value, str) and value.isdigit():
            return int(value)
        else:
            raise ValueError(f"Field '{self._field_name or 'unknown'}' expects integer, got {type(value).__name__}: {value}")

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Integer':
        """Deserialize field descriptor from dictionary"""
        return cls(
            foreign_key=data['foreign_key'],
            on_delete=data['on_delete'],
            primary_key=data['primary_key'],
            nullable=data['nullable'],
            default=data['default'],
            unique=data['unique'],
            index=data['index'],
            auto_update=data.get('auto_update', False)
        )


class Float(FieldDescriptor):
    def __init__(self, **kwargs):
        super().__init__("REAL", **kwargs)
    
    def _validate_type(self, value: Any) -> float:
        """Validate and convert value to float"""
        if isinstance(value, (int, float)):
            return float(value)
        elif isinstance(value, str):
            try:
                return float(value)
            except ValueError:
                raise ValueError(f"Field '{self._field_name or 'unknown'}' expects float, got invalid string: {value}")
        else:
            raise ValueError(f"Field '{self._field_name or 'unknown'}' expects float, got {type(value).__name__}: {value}")
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Float':
        """Deserialize field descriptor from dictionary"""
        return cls(
            primary_key=data['primary_key'],
            nullable=data['nullable'],
            default=data['default'],
            foreign_key=data['foreign_key'],
            unique=data['unique'],
            index=data['index'],
            on_delete=data['on_delete'],
            auto_update=data.get('auto_update', False)
        )


class JSONField(FieldDescriptor):
    def __init__(self, **kwargs):
        # SQLite doesn't have native JSON type, store as TEXT
        super().__init__("TEXT", **kwargs)
        self.json = True

    def _validate_type(self, value: Any) -> str:
        """Validate and serialize JSON value to string"""
        import json
        try:
            # Serialize to JSON string for SQLite storage
            return json.dumps(value, separators=(',', ':'))  # Compact JSON
        except (TypeError, ValueError) as e:
            raise ValueError(f"Field '{self._field_name or 'unknown'}' expects JSON serializable value, got {type(value).__name__}: {e}")

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'JSONField':
        """Deserialize field descriptor from dictionary"""
        return cls(
            primary_key=data['primary_key'],
            nullable=data['nullable'],
            default=data['default'],
            foreign_key=data['foreign_key'],
            unique=data['unique'],
            index=data['index'],
            on_delete=data['on_delete'],
            auto_update=data.get('auto_update', False)
        )


class Date(FieldDescriptor):
    def __init__(self, **kwargs):
        """
        Date field that stores TEXT in ISO format (YYYY-MM-DD)

        **kwargs: Standard field options (nullable, default, etc.)
        """
        super().__init__("TEXT", **kwargs)

    def _validate_type(self, value: Any) -> str:
        """Validate value is a valid ISO date string (YYYY-MM-DD)"""
        from datetime import date, datetime

        # Accept datetime.date objects directly
        if isinstance(value, date):
            return value.strftime('%Y-%m-%d')

        str_value = str(value)

        # Try to parse as ISO date
        try:
            datetime.strptime(str_value, '%Y-%m-%d')
            return str_value
        except ValueError:
            raise ValueError(
                f"Field '{self._field_name or 'unknown'}' must be in ISO date format (YYYY-MM-DD), "
                f"got '{str_value}'"
            )

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Date':
        """Deserialize field descriptor from dictionary"""
        return cls(
            primary_key=data['primary_key'],
            nullable=data['nullable'],
            default=data['default'],
            foreign_key=data['foreign_key'],
            unique=data['unique'],
            index=data['index'],
            on_delete=data['on_delete'],
            auto_update=data.get('auto_update', False)
        )


class Enum(FieldDescriptor):
    def __init__(self, choices: List[str], **kwargs):
        """
        Enum field that stores TEXT with CHECK constraint

        Args:
            choices: List of valid string values
            **kwargs: Standard field options (nullable, default, etc.)
        """
        if not choices:
            raise ValueError("Enum field requires at least one choice")
        if not all(isinstance(c, str) for c in choices):
            raise ValueError("All enum choices must be strings")

        # Validate default if provided
        default = kwargs.get('default')
        if default is not None and default not in choices:
            raise ValueError(
                f"Enum default '{default}' is not in choices {choices}"
            )

        self.choices = choices
        super().__init__("TEXT", **kwargs)

    def _validate_type(self, value: Any) -> str:
        """Validate value is one of the allowed choices"""
        str_value = str(value)
        if str_value not in self.choices:
            raise ValueError(
                f"Field '{self._field_name or 'unknown'}' must be one of {self.choices}, "
                f"got '{str_value}'"
            )
        return str_value

    def to_sql(self, name: str) -> str:
        """Generate SQL with CHECK constraint"""
        sql = super().to_sql(name)
        # Add CHECK constraint for enum values
        choices_sql = ", ".join(f"'{c}'" for c in self.choices)
        sql += f" CHECK({name} IN ({choices_sql}))"
        return sql

    def to_dict(self) -> Dict[str, Any]:
        """Serialize field descriptor to dictionary"""
        data = super().to_dict()
        data['choices'] = self.choices
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Enum':
        """Deserialize field descriptor from dictionary"""
        return cls(
            choices=data['choices'],
            primary_key=data['primary_key'],
            nullable=data['nullable'],
            default=data['default'],
            foreign_key=data['foreign_key'],
            unique=data['unique'],
            index=data['index'],
            on_delete=data['on_delete'],
            auto_update=data.get('auto_update', False)
        )


class Timestamp(FieldDescriptor):
    """ISO 8601 timestamp field stored as TEXT."""

    def __init__(self, **kwargs):
        super().__init__("TEXT", **kwargs)

    def _validate_type(self, value: Any) -> str:
        """Validate value is ISO 8601 timestamp"""
        from datetime import datetime as dt, timezone

        # Accept datetime objects directly
        if isinstance(value, dt):
            # Ensure timezone-aware (assume UTC if naive)
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            return value.isoformat()

        s = str(value)

        # Fast pattern check
        if _ISO_PATTERN.match(s):
            return s

        # Try to parse common formats
        try:
            # Handle 'Z' suffix
            if s.endswith("Z"):
                datetime.fromisoformat(s[:-1] + "+00:00")
            else:
                datetime.fromisoformat(s)
            return s
        except Exception:
            raise ValueError(
                f"Field '{self._field_name or 'unknown'}' must be ISO 8601 "
                f"(e.g. 2024-03-01T12:34:56Z or with offset), got '{s}'"
            )

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Timestamp':
        """Deserialize field descriptor from dictionary"""
        return cls(
            primary_key=data['primary_key'],
            nullable=data['nullable'],
            default=data['default'],
            foreign_key=data['foreign_key'],
            unique=data['unique'],
            index=data['index'],
            on_delete=data['on_delete'],
            auto_update=data.get('auto_update', False)
        )


class Boolean(FieldDescriptor):
    """Boolean field stored as INTEGER (0/1) in SQLite."""

    def __init__(self, **kwargs):
        super().__init__("INTEGER", **kwargs)
        self.boolean = True

    def _validate_type(self, value: Any) -> int:
        """Validate and convert value to 0 or 1"""
        # Handle boolean types
        if isinstance(value, bool):
            return 1 if value else 0

        # Handle integers
        if isinstance(value, int):
            if value in (0, 1):
                return value
            # Treat any non-zero as True
            return 1 if value else 0

        # Handle strings
        if isinstance(value, str):
            lower = value.lower()
            if lower in ('true', 't', 'yes', 'y', '1'):
                return 1
            elif lower in ('false', 'f', 'no', 'n', '0'):
                return 0
            else:
                raise ValueError(
                    f"Field '{self._field_name or 'unknown'}' cannot parse '{value}' as boolean"
                )

        raise ValueError(
            f"Field '{self._field_name or 'unknown'}' expects boolean, got {type(value).__name__}"
        )

    def to_sql(self, name: str) -> str:
        """Generate SQL with proper default and CHECK constraint"""
        sql = f"{name} INTEGER"

        if not self.nullable:
            sql += " NOT NULL"

        # Normalize default to 0/1
        if self.default is not None:
            if isinstance(self.default, bool):
                dv = 1 if self.default else 0
            elif isinstance(self.default, str):
                dv = 1 if self.default.lower() in ("1", "true", "t", "yes", "y") else 0
            else:
                dv = 1 if self.default else 0
            sql += f" DEFAULT {dv}"

        # Enforce boolean domain (0 or 1 only)
        sql += f" CHECK({name} IN (0, 1))"

        if self.unique:
            sql += " UNIQUE"

        return sql

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Boolean':
        """Deserialize field descriptor from dictionary"""
        return cls(
            primary_key=data['primary_key'],
            nullable=data['nullable'],
            default=data['default'],
            foreign_key=data['foreign_key'],
            unique=data['unique'],
            index=data['index'],
            on_delete=data['on_delete'],
            auto_update=data.get('auto_update', False)
        )
