from typing import Any, List, Union, Dict

try:
    from typing import Self
except ImportError:
    from typing_extensions import Self


class FilterMixin:
    """Mixin for filter operations that can be shared across builders"""

    def eq(self, field: str, value: Any) -> Self:
        """Filter where field equals value.

        Args:
            field: Name of the field to filter on
            value: Value to match exactly
        """
        self.mongo_filters[field] = value
        return self

    def neq(self, field: str, value: Any) -> Self:
        """Filter where field does not equal value.

        Args:
            field: Name of the field to filter on
            value: Value to exclude
        """
        self.mongo_filters[field] = {"$ne": value}
        return self

    def gt(self, field: str, value: Union[int, float]) -> Self:
        """Filter where field is greater than value.

        Args:
            field: Name of the numeric field to filter on
            value: Minimum value (exclusive)
        """
        if field in self.mongo_filters and isinstance(self.mongo_filters[field], dict):
            self.mongo_filters[field]["$gt"] = value
        else:
            self.mongo_filters[field] = {"$gt": value}
        return self

    def gte(self, field: str, value: Union[int, float]) -> Self:
        """Filter where field is greater than or equal to value.

        Args:
            field: Name of the numeric field to filter on
            value: Minimum value (inclusive)
        """
        if field in self.mongo_filters and isinstance(self.mongo_filters[field], dict):
            self.mongo_filters[field]["$gte"] = value
        else:
            self.mongo_filters[field] = {"$gte": value}
        return self

    def lt(self, field: str, value: Union[int, float]) -> Self:
        """Filter where field is less than value.

        Args:
            field: Name of the numeric field to filter on
            value: Maximum value (exclusive)
        """
        if field in self.mongo_filters and isinstance(self.mongo_filters[field], dict):
            self.mongo_filters[field]["$lt"] = value
        else:
            self.mongo_filters[field] = {"$lt": value}
        return self

    def lte(self, field: str, value: Union[int, float]) -> Self:
        """Filter where field is less than or equal to value.

        Args:
            field: Name of the numeric field to filter on
            value: Maximum value (inclusive)
        """
        if field in self.mongo_filters and isinstance(self.mongo_filters[field], dict):
            self.mongo_filters[field]["$lte"] = value
        else:
            self.mongo_filters[field] = {"$lte": value}
        return self

    def in_(self, field: str, values: List[Any]) -> Self:
        """Filter where field value is in the provided list.

        Args:
            field: Name of the field to filter on
            values: List of acceptable values
        """
        self.mongo_filters[field] = {"$in": values}
        return self

    def not_in(self, field: str, values: List[Any]) -> Self:
        """Filter where field value is not in the provided list.

        Args:
            field: Name of the field to filter on
            values: List of values to exclude
        """
        self.mongo_filters[field] = {"$nin": values}
        return self

    def is_null(self, field: str) -> Self:
        """Filter where field is null or missing.

        Args:
            field: Name of the field to check
        """
        self.mongo_filters[field] = {"$eq": None}
        return self

    def is_not_null(self, field: str) -> Self:
        """Filter where field is not null and exists.

        Args:
            field: Name of the field to check
        """
        self.mongo_filters[field] = {"$ne": None}
        return self

    def between(self, field: str, min_val: Union[int, float], max_val: Union[int, float]) -> Self:
        """Filter where field is between min and max values (inclusive).

        Args:
            field: Name of the numeric field to filter on
            min_val: Minimum value (inclusive)
            max_val: Maximum value (inclusive)
        """
        self.mongo_filters[field] = {"$gte": min_val, "$lte": max_val}
        return self

    def contains(self, field: str, value: Any) -> Self:
        """Filter where JSON array field contains value(s).

        For scalar values, checks if the array contains that exact value.
        For lists/tuples, checks if the array contains ANY of the provided values (OR semantics).

        Args:
            field: Name of the JSON array field to filter on
            value: Scalar value or list of values to check for
        """
        if isinstance(value, (list, tuple)):
            self.mongo_filters[field] = {"$contains": list(value)}
        else:
            self.mongo_filters[field] = {"$contains": value}
        return self

    def ilike(self, field: str, pattern: str) -> Self:
        """Case-insensitive pattern matching using LIKE.

        If pattern contains no wildcards (% or _), automatically wraps as '%pattern%' for contains search.

        Args:
            field: Name of the field to filter on
            pattern: SQL LIKE pattern (% for wildcard, _ for single char)
        """
        # Auto-wrap if no wildcards specified (common case: contains search)
        if "%" not in pattern and "_" not in pattern:
            pattern = f"%{pattern}%"
        self.mongo_filters[field] = {"$ilike": pattern}
        return self

    def ilike_prefix(self, field: str, prefix: str) -> Self:
        """Case-insensitive prefix match (index-friendly).

        Args:
            field: Name of the field to filter on
            prefix: Prefix to match (automatically adds % suffix)
        """
        self.mongo_filters[field] = {"$ilike": f"{prefix}%"}
        return self



