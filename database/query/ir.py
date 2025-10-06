"""Intermediate Representation (IR/AST) for query operations."""
from dataclasses import dataclass, field
from typing import Any, List, Optional, Tuple, Union


@dataclass(frozen=True)
class Col:
    """Column reference."""
    name: str


@dataclass(frozen=True)
class Lit:
    """Literal value."""
    value: Any


class Pred:
    """Base class for predicates."""
    pass


@dataclass(frozen=True)
class Eq(Pred):
    col: Col
    val: Lit


@dataclass(frozen=True)
class Ne(Pred):
    col: Col
    val: Lit


@dataclass(frozen=True)
class Gt(Pred):
    col: Col
    val: Lit


@dataclass(frozen=True)
class Ge(Pred):
    col: Col
    val: Lit


@dataclass(frozen=True)
class Lt(Pred):
    col: Col
    val: Lit


@dataclass(frozen=True)
class Le(Pred):
    col: Col
    val: Lit


@dataclass(frozen=True)
class In_(Pred):
    col: Col
    vals: List[Lit]


@dataclass(frozen=True)
class Nin(Pred):
    col: Col
    vals: List[Lit]


@dataclass(frozen=True)
class Ilike(Pred):
    col: Col
    pattern: str


@dataclass(frozen=True)
class Regex(Pred):
    col: Col
    pattern: str


@dataclass(frozen=True)
class ContainsJSON(Pred):
    col: Col
    vals: Union[List[Lit], Lit]


@dataclass(frozen=True)
class KeywordFTS(Pred):
    col: Col
    query: str


@dataclass(frozen=True)
class And(Pred):
    parts: List[Pred]


@dataclass(frozen=True)
class Or(Pred):
    parts: List[Pred]


@dataclass(frozen=True)
class SelectIR:
    """SELECT statement IR."""
    table: str
    columns: Optional[List[str]] = None
    where: Optional[Pred] = None
    order: List[Tuple[str, bool]] = field(default_factory=list)  # (col, desc)
    limit: Optional[int] = None
    fts_rank_expr: Optional[str] = None
    pk: Optional[str] = None
    fts_table: Optional[str] = None
    fts_query: Optional[str] = None


@dataclass(frozen=True)
class InsertIR:
    """INSERT statement IR."""
    table: str
    rows: List[dict]


@dataclass(frozen=True)
class UpdateIR:
    """UPDATE statement IR."""
    table: str
    assign: dict
    where: Optional[Pred] = None


@dataclass(frozen=True)
class DeleteIR:
    """DELETE statement IR."""
    table: str
    where: Optional[Pred] = None


@dataclass(frozen=True)
class UpsertIR:
    """UPSERT (INSERT ... ON CONFLICT) statement IR."""
    table: str
    rows: List[dict]
    on_conflict: List[str]
    do_nothing: bool
    returning_all: bool
