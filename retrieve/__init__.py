"""Robog retrieval engine: hybrid search over the PUDU manuals (Bonus 01)."""

from .engine import Hit, SearchEngine, SearchResult
from .profiles import ProfileHit, ProfileIndex
from .vector import ChromaRetriever, DenseHit, VectorStoreUnavailable

__all__ = [
    "SearchEngine",
    "SearchResult",
    "Hit",
    "ProfileIndex",
    "ProfileHit",
    "ChromaRetriever",
    "DenseHit",
    "VectorStoreUnavailable",
]
