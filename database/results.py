from dataclasses import dataclass
from typing import Generic, TypeVar, Optional, List, Union, Dict, Any

T = TypeVar("T")


@dataclass
class DBResult(Generic[T]):
    data: T
    score: Optional[Union[float, List[float]]] = None


class Result:
    """Simple query result wrapper."""

    def __init__(self, data: List[Dict[str, Any]], count: Optional[int] = None):
        self.data = data
        self.count = count if count is not None else len(data)
