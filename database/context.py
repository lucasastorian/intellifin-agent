"""Context variables for embedding batch state.

ContextVars provide context-aware state that:
1. Inherits to awaited coroutines in the same task
2. Propagates to child tasks created with asyncio.create_task() (context is copied)
3. All tasks share the same queue object reference (efficient batching)

This means batch_embeddings() is safe with:
- Using 'await' on coroutines
- Using asyncio.as_completed() / asyncio.gather() with coroutines or tasks
- Creating child tasks with asyncio.create_task()
- Any async operation that runs within the same context tree

NOT safe with:
- Thread pools or process pools (different execution context)

Example:
    async with db.batch_embeddings():
        # All of these work correctly:
        tasks = [upsert_filing(f) for f in filings]
        for coro in asyncio.as_completed(tasks):
            await coro  # ✓ Shares embedding queue

        # Also works:
        await asyncio.gather(*[upsert_filing(f) for f in filings])

        # Also works:
        tasks = [asyncio.create_task(upsert(f)) for f in filings]
        await asyncio.gather(*tasks)  # ✓ Child tasks share queue
"""
from contextvars import ContextVar
from typing import Dict, List, Optional, Tuple

# Embedding queue: maps (table, column) -> List[{"ids": [...], "texts": [...]}]
# Each list element represents ONE document (preserves document boundaries for contextualized embeddings)
# Only exists inside batch_embeddings() context
emb_queue_var: ContextVar[Optional[Dict[Tuple[str, str], List[Dict[str, List]]]]] = ContextVar(
    "emb_queue", default=None
)
