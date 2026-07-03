"""
clasp/cache/semantic_cache.py
=============================
Semantic Cache implementation using sentence-transformers and FAISS.
Requires `pip install clasp[semantic]`.
"""
from __future__ import annotations

import asyncio
from typing import Any
from loguru import logger

try:
    from sentence_transformers import SentenceTransformer
    import faiss
    _HAS_SEMANTIC_DEPS = True
except ImportError:
    _HAS_SEMANTIC_DEPS = False


class SemanticCache:
    def __init__(self, threshold: float = 0.95):
        self.threshold = threshold
        self._lock = asyncio.Lock()
        
        if not _HAS_SEMANTIC_DEPS:
            logger.warning("Semantic Cache requested but dependencies are missing. Install with: pip install .[semantic]")
            self.model = None
            self.index = None
            self.responses = []
            return
            
        logger.info("Semantic Cache: Loading all-MiniLM-L6-v2 embedding model...")
        self.model = SentenceTransformer("all-MiniLM-L6-v2")
        self.index = faiss.IndexFlatIP(384)  # Inner product = cosine on normalized vecs
        self.responses: list[list[bytes]] = []
        logger.info("Semantic Cache: Ready.")

    def _extract_query(self, request_body: dict[str, Any]) -> str:
        """Extract the raw text of the final user message to serve as the semantic query."""
        messages = request_body.get("messages", [])
        last_human = [m for m in messages if m.get("role") == "user"]
        if not last_human:
            return ""
            
        content = last_human[-1].get("content", "")
        if isinstance(content, str):
            return content[:2000]
            
        if isinstance(content, list):
            return " ".join(
                b.get("text", "") 
                for b in content 
                if isinstance(b, dict) and b.get("type") == "text"
            )[:2000]
            
        return ""

    def _sync_lookup(self, query: str) -> list[bytes] | None:
        if not self.model or not self.index or self.index.ntotal == 0 or not query:
            return None
            
        vec = self.model.encode([query], normalize_embeddings=True)
        distances, indices = self.index.search(vec, k=1)
        
        if distances[0][0] >= self.threshold:
            return self.responses[indices[0][0]]
        return None

    def _sync_store(self, query: str, response_chunks: list[bytes]) -> None:
        if not self.model or not self.index or not query:
            return
            
        vec = self.model.encode([query], normalize_embeddings=True)
        self.index.add(vec)
        self.responses.append(response_chunks)

    async def lookup(self, request_body: dict[str, Any]) -> list[bytes] | None:
        """Asynchronous lookup of semantic match."""
        if not _HAS_SEMANTIC_DEPS:
            return None
            
        query = self._extract_query(request_body)
        if not query:
            return None
            
        async with self._lock:
            # Run the blocking FAISS and sentence_transformers operations in a thread
            result = await asyncio.to_thread(self._sync_lookup, query)
            
        if result:
            logger.info("semantic cache hit!")
        return result

    async def store(self, request_body: dict[str, Any], response_chunks: list[bytes]) -> None:
        """Asynchronously store a query-response pair in the semantic cache."""
        if not _HAS_SEMANTIC_DEPS:
            return
            
        query = self._extract_query(request_body)
        if not query:
            return
            
        async with self._lock:
            await asyncio.to_thread(self._sync_store, query, response_chunks)
            logger.debug(f"semantic cache: stored new entry (total={self.index.ntotal})")


# Singleton Pattern
_semantic_cache: SemanticCache | None = None

def get_semantic_cache(threshold: float = 0.95) -> SemanticCache:
    """Return the global SemanticCache instance, instantiating it if necessary."""
    global _semantic_cache
    if _semantic_cache is None:
        _semantic_cache = SemanticCache(threshold)
    else:
        _semantic_cache.threshold = threshold
    return _semantic_cache
