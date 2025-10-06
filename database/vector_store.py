"""Append-only vector store for brute-force similarity search."""
import os
import json
import numpy as np
from pathlib import Path
from typing import Optional, List, Tuple, Iterable, Set


class VectorStore:
    """
    Append-only vector storage with tombstone deletes.

    Files:
        {base}.vec: raw float32 vectors, tightly packed
        {base}.id: parallel int64 IDs
        {base}.tomb.json: tombstoned (deleted) IDs
    """

    def __init__(self, base_path: str, dim: int = 512):
        self.base_path = Path(base_path)
        self.dim = dim
        self.vec_file = self.base_path.with_suffix('.vec')
        self.id_file = self.base_path.with_suffix('.id')
        self.tomb_file = self.base_path.with_suffix('.tomb.json')

        # Create directory and files if missing
        self.vec_file.parent.mkdir(parents=True, exist_ok=True)
        self.vec_file.touch(exist_ok=True)
        self.id_file.touch(exist_ok=True)

        self._load_tombstones()
        self._reconcile_lengths()

    def _load_tombstones(self):
        """Load tombstoned IDs from JSON"""
        if self.tomb_file.exists():
            with open(self.tomb_file) as f:
                self.tombstones = set(json.load(f).get('ids', []))
        else:
            self.tombstones = set()

    def _save_tombstones(self):
        """Save tombstones to JSON"""
        tmp = str(self.tomb_file) + '.tmp'
        with open(tmp, 'w') as f:
            json.dump({'ids': sorted(self.tombstones)}, f, separators=(',', ':'))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.tomb_file)

    def _reconcile_lengths(self):
        """Truncate files if they disagree (crash recovery)"""
        vec_count = self.vec_file.stat().st_size // (self.dim * 4)  # float32 = 4 bytes
        id_count = self.id_file.stat().st_size // 8  # int64 = 8 bytes

        count = min(vec_count, id_count)

        # Truncate to agreed length
        with open(self.vec_file, 'r+b') as f:
            f.truncate(count * self.dim * 4)
            f.flush()
            os.fsync(f.fileno())

        with open(self.id_file, 'r+b') as f:
            f.truncate(count * 8)
            f.flush()
            os.fsync(f.fileno())

    def count(self) -> int:
        """Return number of vectors (excluding tombstones)"""
        total = self.id_file.stat().st_size // 8
        return total - len(self.tombstones)

    def add(self, id_: int, vec: np.ndarray):
        """Append a single vector"""
        self.add_batch([id_], [vec])

    def add_batch(self, ids: List[int], vecs: List[np.ndarray]):
        """Append multiple vectors at once (bulk insert)"""
        if not ids:
            return

        assert len(ids) == len(vecs), f"ID count {len(ids)} != vector count {len(vecs)}"

        # Normalize all vectors to unit length
        vecs_array = np.array(vecs, dtype=np.float32)
        assert vecs_array.shape == (len(vecs), self.dim), f"Expected shape ({len(vecs)}, {self.dim}), got {vecs_array.shape}"

        norms = np.linalg.norm(vecs_array, axis=1, keepdims=True)
        norms[norms == 0] = 1  # Avoid division by zero
        vecs_array = vecs_array / norms

        # Append vectors
        with open(self.vec_file, 'ab') as f:
            vecs_array.tofile(f)
            f.flush()
            os.fsync(f.fileno())

        # Append IDs
        ids_array = np.array(ids, dtype=np.int64)
        with open(self.id_file, 'ab') as f:
            ids_array.tofile(f)
            f.flush()
            os.fsync(f.fileno())

    def tombstone(self, id_: int):
        """Mark an ID as deleted"""
        self.tombstones.add(int(id_))
        self._save_tombstones()

    def tombstone_batch(self, ids: List[int]):
        """Mark multiple IDs as deleted"""
        self.tombstones.update(int(x) for x in ids)
        self._save_tombstones()

    def search(
        self,
        query_vec: np.ndarray,
        topk: int = 50,
        filter_ids: Optional[Iterable[int]] = None
    ) -> Tuple[np.ndarray, np.ndarray]:
        """
        Brute-force cosine similarity search.

        Returns:
            (ids, scores) - both sorted descending by score
        """
        # Normalize query vector
        query_vec = np.array(query_vec, dtype=np.float32)
        assert query_vec.shape == (self.dim,), f"Expected shape ({self.dim},), got {query_vec.shape}"
        norm = np.linalg.norm(query_vec)
        if norm > 0:
            query_vec = query_vec / norm

        # Load vectors and IDs via memmap
        vectors = np.memmap(self.vec_file, dtype=np.float32, mode='r')
        total_count = len(vectors) // self.dim
        if total_count == 0:
            return np.array([], dtype=np.int64), np.array([], dtype=np.float32)

        vectors = vectors.reshape(total_count, self.dim)
        ids = np.memmap(self.id_file, dtype=np.int64, mode='r')

        # Apply tombstones
        if self.tombstones:
            mask = ~np.isin(ids, list(self.tombstones))
            ids = ids[mask]
            vectors = vectors[mask]

        # Apply filter
        if filter_ids is not None:
            filter_set = set(int(x) for x in filter_ids)
            mask = np.isin(ids, list(filter_set))
            ids = ids[mask]
            vectors = vectors[mask]

        if len(ids) == 0:
            return np.array([], dtype=np.int64), np.array([], dtype=np.float32)

        # Compute cosine similarity (dot product of normalized vectors)
        scores = vectors @ query_vec

        # Get top-k
        if len(scores) <= topk:
            idx = np.argsort(scores)[::-1]
        else:
            idx = np.argpartition(scores, -topk)[-topk:]
            idx = idx[np.argsort(scores[idx])[::-1]]

        return ids[idx], scores[idx]
