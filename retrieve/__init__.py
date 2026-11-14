"""Robog retrieval engine: hybrid search over the PUDU manuals (Bonus 01)."""

from .engine import Hit, SearchEngine, SearchResult
from .vector import ChromaRetriever, DenseHit, VectorStoreUnavailable

__all__ = [
    "SearchEngine",
    "SearchResult",
    "Hit",
    "ChromaRetriever",
    "DenseHit",
    "VectorStoreUnavailable",
]
