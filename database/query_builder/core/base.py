"""
Type-Safe Query Builder Architecture for Simple SQLite Operations

This architecture provides:
1. Separation of each operation type into its own builder class
2. Mixins for shared functionality (filters, ordering, selection)
3. Clear type hints through specialized return types
4. Prevention of invalid query combinations at the type level

Architecture Benefits:
- Clean separation of concerns: builders focus on query construction, database handles execution
- Simplified builders: no provisioning logic, just pass data to database methods
- Type safety: each operation has its own builder with only relevant methods
- Extensibility: easy to add new operation types or modifiers
"""

from typing import Dict, Any, List, Union, Optional, TypeVar
from abc import ABC, abstractmethod

# Type variables for generic return types
T = TypeVar('T')


class BaseQueryBuilder(ABC):
    """Base class with common functionality for all query builders"""

    def __init__(self, database, schema, table: str):
        self.database = database
        self.schema = schema
        self.table = schema.table(table)

        # Common query state
        self.mongo_filters: Dict[str, Any] = {}
        self._limit: Optional[int] = None
        self._order_by: Optional[str] = None
        self._order_desc: bool = False

    @abstractmethod
    def execute(self) -> Any:
        """Execute must be implemented by each builder type"""
        pass
