"""Context variables for transaction-scoped state.

Uses ContextVars to maintain per-task (not per-thread) state that propagates
across awaits but remains isolated between concurrent tasks.
"""
from contextvars import ContextVar
from typing import Dict, List, Optional, Tuple

# Transaction nesting depth (0 = no transaction, 1 = outermost, 2+ = nested)
txn_depth_var: ContextVar[int] = ContextVar("txn_depth", default=0)

# Embedding queue: maps (table, column) -> {ids: [...], texts: [...]}
# Only exists during outermost transaction
emb_queue_var: ContextVar[Optional[Dict[Tuple[str, str], Dict[str, List]]]] = ContextVar(
    "emb_queue", default=None
)

# Whether to rollback SQL transaction if embedding flush fails
atomic_vectors_var: ContextVar[bool] = ContextVar("atomic_vectors", default=False)
