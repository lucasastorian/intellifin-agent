import asyncio
import hashlib
import pickle
import lmdb
import json
from pathlib import Path
from typing import Optional, Type, Any
from pydantic import BaseModel

# ---- module-level singletons ----
_ENV = None
_ENV_PATH = None
_LOCK = asyncio.Lock()  # serialize LMDB access in asyncio


def _get_env(cache_dir: str) -> lmdb.Environment:
    """Get or create singleton LMDB environment per process"""
    global _ENV, _ENV_PATH
    cache_dir_path = Path(cache_dir)
    cache_dir_path.mkdir(parents=True, exist_ok=True)
    db_path = str(cache_dir_path / "llm_responses.lmdb")

    # Reuse one Environment per process
    if _ENV is None or _ENV_PATH != db_path:
        _ENV = lmdb.open(
            db_path,
            map_size=10 * 1024 * 1024 * 1024,
            max_readers=2048,
            lock=True
        )
        _ENV_PATH = db_path
    return _ENV


class LLMCache:
    """LMDB-based cache for LLM structured outputs (process-wide singleton env with lock)"""

    def __init__(self, cache_dir: str = "./cache"):
        self.cache_dir = cache_dir
        self.env = _get_env(cache_dir)

    def _generate_cache_key(
        self,
        model: str,
        system_prompt: str,
        user_message: str,
        response_model: Type[BaseModel],
        reasoning_effort: str
    ) -> bytes:
        """Generate a deterministic cache key from all inputs"""
        # Get schema as deterministic JSON string
        schema = response_model.model_json_schema()
        schema_str = json.dumps(schema, sort_keys=True)

        # Concatenate all inputs
        key_parts = [
            model,
            system_prompt,
            user_message,
            schema_str,
            reasoning_effort or "none"
        ]
        key_string = "\n---\n".join(key_parts)

        # Hash to fixed-size key
        return hashlib.sha256(key_string.encode('utf-8')).digest()

    async def get(
        self,
        model: str,
        system_prompt: str,
        user_message: str,
        response_model: Type[BaseModel],
        reasoning_effort: str
    ) -> Optional[Any]:
        """Get cached LLM response (serialized with asyncio.Lock)"""
        key = self._generate_cache_key(model, system_prompt, user_message, response_model, reasoning_effort)
        async with _LOCK:
            with self.env.begin() as txn:
                value = txn.get(key)
                if value:
                    return pickle.loads(value)
        return None

    async def set(
        self,
        model: str,
        system_prompt: str,
        user_message: str,
        response_model: Type[BaseModel],
        reasoning_effort: str,
        response: BaseModel
    ) -> None:
        """Cache LLM response (serialized with asyncio.Lock)"""
        key = self._generate_cache_key(model, system_prompt, user_message, response_model, reasoning_effort)
        value = pickle.dumps(response)
        async with _LOCK:
            with self.env.begin(write=True) as txn:
                txn.put(key, value)

    def close(self):
        """Close the LMDB environment"""
        self.env.close()
