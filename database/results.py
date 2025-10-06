from dataclasses import dataclass
from typing import Generic, TypeVar, Optional, List, Union

T = TypeVar("T")


@dataclass
class DBResult(Generic[T]):
    data: T
    score: Optional[Union[float, List[float]]] = None
