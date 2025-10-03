try:
    from typing import Self
except ImportError:
    from typing_extensions import Self


class OrderLimitMixin:
    """Mixin for ordering and limiting results"""

    def limit(self, n: int) -> Self:
        """Limit the number of results returned.

        Args:
            n: Maximum number of results to return
        """
        self._limit = n
        return self

    def order(self, field: str, desc: bool = False) -> Self:
        """Order results by a field.

        Args:
            field: Name of the field to sort by
            desc: Sort in descending order (default: False for ascending)
        """
        self._order_by = field
        self._order_desc = desc
        return self

