import hashlib
import pickle
import lmdb
from pathlib import Path
from typing import List, Optional


class EmbeddingCache:
    """Simple LMDB-based cache for embeddings"""

    def __init__(self, cache_dir: str = "./cache", model: str = "voyage-3.5-lite", dimensions: int = 512):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        model_key = model.replace('.', '_').replace('-', '_')
        db_path = self.cache_dir / f"embeddings_{model_key}_dim{dimensions}.lmdb"

        self.env = lmdb.open(
            str(db_path),
            map_size=10*1024*1024*1024,
            writemap=True,
            max_readers=126,
            lock=True
        )

    def _hash_text(self, text: str) -> bytes:
        """Hash text to use as cache key"""
        return hashlib.sha256(text.encode('utf-8')).digest()

    def get(self, text: str) -> Optional[List[float]]:
        """Get cached embedding for text"""
        key = self._hash_text(text)
        with self.env.begin() as txn:
            value = txn.get(key)
            if value:
                return pickle.loads(value)
        return None

    def set(self, text: str, embedding: List[float]) -> None:
        """Cache embedding for text"""
        key = self._hash_text(text)
        value = pickle.dumps(embedding)
        with self.env.begin(write=True) as txn:
            txn.put(key, value)

    def get_many(self, texts: List[str]) -> List[Optional[List[float]]]:
        """Get cached embeddings for multiple texts (returns None for cache misses)"""
        keys = [self._hash_text(text) for text in texts]
        results = []
        with self.env.begin() as txn:
            for key in keys:
                value = txn.get(key)
                results.append(pickle.loads(value) if value else None)
        return results

    def set_many(self, texts: List[str], embeddings: List[List[float]]) -> None:
        """Cache multiple embeddings"""
        with self.env.begin(write=True) as txn:
            for text, embedding in zip(texts, embeddings):
                key = self._hash_text(text)
                value = pickle.dumps(embedding)
                txn.put(key, value)

    def close(self):
        """Close the LMDB environment"""
        self.env.close()
