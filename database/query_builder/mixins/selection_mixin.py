from typing import Dict

try:
    from typing import Self
except ImportError:
    from typing_extensions import Self


class SelectionMixin:
    """Mixin for field selection in queries that support it"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.selected_fields: str = '*'

    def select(self, fields: str) -> Self:
        """Select specific fields to return"""
        self.selected_fields = fields
        return self

    def _build_projection(self) -> Dict[str, int]:
        """Build MongoDB projection from selected fields"""
        if self.selected_fields == '*':
            return {}

        fields = [f.strip() for f in self.selected_fields.split(',')]
        return {field: 1 for field in fields}


