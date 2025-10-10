import hashlib
import pickle
import lmdb
import json
from pathlib import Path
from typing import Optional, Type, Any
from pydantic import BaseModel


class LLMCache:
    """LMDB-based cache for LLM structured outputs"""

    def __init__(self, cache_dir: str = "./cache"):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        db_path = self.cache_dir / "llm_responses.lmdb"

        # 10GB max size, writemap=True for thread safety, max_readers for concurrent reads
        self.env = lmdb.open(
            str(db_path),
            map_size=10*1024*1024*1024,
            writemap=True,
            max_readers=126,
            lock=True
        )

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
            reasoning_effort
        ]
        key_string = "\n---\n".join(key_parts)

        # Hash to fixed-size key
        return hashlib.sha256(key_string.encode('utf-8')).digest()

    def get(
        self,
        model: str,
        system_prompt: str,
        user_message: str,
        response_model: Type[BaseModel],
        reasoning_effort: str
    ) -> Optional[Any]:
        """Get cached LLM response"""
        key = self._generate_cache_key(model, system_prompt, user_message, response_model, reasoning_effort)
        with self.env.begin() as txn:
            value = txn.get(key)
            if value:
                # Return the pickled Pydantic model instance
                return pickle.loads(value)
        return None

    def set(
        self,
        model: str,
        system_prompt: str,
        user_message: str,
        response_model: Type[BaseModel],
        reasoning_effort: str,
        response: BaseModel
    ) -> None:
        """Cache LLM response (Pydantic model instance)"""
        key = self._generate_cache_key(model, system_prompt, user_message, response_model, reasoning_effort)
        value = pickle.dumps(response)
        with self.env.begin(write=True) as txn:
            txn.put(key, value)

    def close(self):
        """Close the LMDB environment"""
        self.env.close()
