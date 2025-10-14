"""Append-only vector store for brute-force similarity search."""
import os
import json
import threading
import numpy as np
from pathlib import Path
from typing import Optional, List, Tuple, Iterable, Set

# fcntl is Unix-only; on Windows, use only threading lock
try:
    import fcntl
    HAS_FCNTL = True
except ImportError:
    HAS_FCNTL = False


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
        self._lock = threading.RLock()

        # Memmap cache to prevent file descriptor leaks
        self._vectors_mmap = None
        self._ids_mmap = None
        self._vec_size = None  # Track file size when memmap was created
        self._id_size = None

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

    def _invalidate_cache(self):
        """Invalidate memmap cache - call after writes"""
        if self._vectors_mmap is not None:
            del self._vectors_mmap
            self._vectors_mmap = None
        if self._ids_mmap is not None:
            del self._ids_mmap
            self._ids_mmap = None
        self._vec_size = None
        self._id_size = None

    def _get_memmaps(self):
        """Get cached memmaps or create new ones if files changed.

        Returns:
            (vectors_mmap, ids_mmap, total_count) or (None, None, 0) if empty
        """
        vec_size = self.vec_file.stat().st_size
        id_size = self.id_file.stat().st_size

        # Return empty if no data
        if vec_size == 0 or id_size == 0:
            return None, None, 0

        # Refresh cache if file sizes changed
        if vec_size != self._vec_size or id_size != self._id_size:
            self._invalidate_cache()
            self._vectors_mmap = np.memmap(self.vec_file, dtype=np.float32, mode='r')
            self._ids_mmap = np.memmap(self.id_file, dtype=np.int64, mode='r')
            self._vec_size = vec_size
            self._id_size = id_size

        total_count = len(self._vectors_mmap) // self.dim
        return self._vectors_mmap, self._ids_mmap, total_count

    def close(self):
        """Close and cleanup memmap resources"""
        with self._lock:
            self._invalidate_cache()

    def __enter__(self):
        """Context manager entry"""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit"""
        self.close()
        return False

    def count(self) -> int:
        """Return number of vectors (excluding tombstones)"""
        total = self.id_file.stat().st_size // 8
        return total - len(self.tombstones)

    def has_id(self, id_: int) -> bool:
        """Check if an ID exists in the store (ignoring tombstones)"""
        with self._lock:
            _, ids, count = self._get_memmaps()
            if count == 0:
                return False
            return int(id_) in ids

    def add(self, id_: int, vec: np.ndarray):
        """Append a single vector"""
        self.add_batch([id_], [vec])

    def add_batch(self, ids: List[int], vecs: List[np.ndarray]):
        """Append multiple vectors at once (bulk insert)"""
        if not ids:
            return

        with self._lock:
            assert len(ids) == len(vecs), f"ID count {len(ids)} != vector count {len(vecs)}"

            # Normalize all vectors to unit length
            vecs_array = np.array(vecs, dtype=np.float32)
            assert vecs_array.shape == (len(vecs), self.dim), f"Expected shape ({len(vecs)}, {self.dim}), got {vecs_array.shape}"

            norms = np.linalg.norm(vecs_array, axis=1, keepdims=True)
            norms[norms == 0] = 1  # Avoid division by zero
            vecs_array = vecs_array / norms

            # Append vectors and IDs with file locks (Unix only)
            with open(self.vec_file, 'ab') as vf, open(self.id_file, 'ab') as idf:
                if HAS_FCNTL:
                    fcntl.flock(vf.fileno(), fcntl.LOCK_EX)
                    fcntl.flock(idf.fileno(), fcntl.LOCK_EX)

                try:
                    vecs_array.tofile(vf)
                    vf.flush()
                    os.fsync(vf.fileno())

                    ids_array = np.array(ids, dtype=np.int64)
                    ids_array.tofile(idf)
                    idf.flush()
                    os.fsync(idf.fileno())
                finally:
                    if HAS_FCNTL:
                        fcntl.flock(vf.fileno(), fcntl.LOCK_UN)
                        fcntl.flock(idf.fileno(), fcntl.LOCK_UN)

            # Invalidate cache after write
            self._invalidate_cache()

    def tombstone(self, id_: int):
        """Mark an ID as deleted"""
        with self._lock:
            self.tombstones.add(int(id_))
            self._save_tombstones()

    def tombstone_batch(self, ids: List[int]):
        """Mark multiple IDs as deleted"""
        with self._lock:
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

        with self._lock:
            # Get cached memmaps
            vectors, ids, total_count = self._get_memmaps()
            if total_count == 0:
                return np.array([], dtype=np.int64), np.array([], dtype=np.float32)

            # Reshape vectors
            vectors = vectors.reshape(total_count, self.dim)

            # Ensure lengths match (safety check)
            id_count = len(ids)
            if total_count != id_count:
                # Use the smaller count to avoid index errors
                count = min(total_count, id_count)
                vectors = vectors[:count]
                ids = ids[:count]

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

            # Copy results (don't return views into memmap)
            return ids[idx].copy(), scores[idx].copy()

