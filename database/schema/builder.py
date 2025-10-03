from typing import Dict, Type
from .table import Table
from .fields import FieldDescriptor


class TableBuilder:
    """
    Builder for creating Table classes iteratively.
    Supports the pattern: table = TableBuilder("name").add_field(...).add_field(...)
    """
    
    def __init__(self, name: str):
        self.name = name
        self.fields: Dict[str, FieldDescriptor] = {}

    def add_field(self, name: str, field: FieldDescriptor) -> 'TableBuilder':
        """Add a field to the table. Returns self for chaining."""
        Table._validate_column_name(name)
        
        if name in self.fields:
            raise ValueError(f"Field '{name}' already exists in table builder for '{self.name}'.")
        
        self.fields[name] = field
        return self

    def build(self) -> Type[Table]:
        """Build and return the Table subclass."""
        class_attrs = {
            '__tablename__': self.name,
        }
        
        # Add fields as class attributes - TableMeta will process them
        class_attrs.update(self.fields)

        # Dynamically create the Table subclass
        table_cls = type(f"Dynamic{self.name.capitalize()}", (Table,), class_attrs)
        return table_cls
