"""Phase 2: embed the chunks and store them in a vector database (Chroma).

Needs the extra dependencies (phase 1 needs none):

    pip install -r requirements-rag.txt

Both embedding providers are free -- pick one with --provider:

* ``local``  (default) -- intfloat/multilingual-e5-small via sentence-transformers.
  Runs offline on any laptop, no API key, no rate limits. Best for demos.
* ``gemini`` -- gemini-embedding-2 via the Google AI Studio free tier.
  Higher quality, needs the GEMINI_API_KEY environment variable, and has a
  free-tier request limit (~1,500 requests/day). Vectors are stored at 1536
  dimensions (the model supports 128-3072; Google recommends 768/1536/3072).

Usage:

    python -m ingest.embed_index --chunks build/chunks.jsonl
    python -m ingest.embed_index --chunks build/chunks.jsonl --provider gemini

NOTE for queries later: with the local e5 model, queries must be prefixed
"query: " and passages "passage: " (this script handles the passages; the
search side must add "query: " to the question).
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

# E5 models were trained with these prefixes -- they matter for quality.
_E5_QUERY_PREFIX = "query: "
_E5_PASSAGE_PREFIX = "passage: "

GEMINI_MODEL = "gemini-embedding-2"
LOCAL_MODEL = "intfloat/multilingual-e5-small"


# --------------------------------------------------------------------------
# Embedding providers (both free)
# --------------------------------------------------------------------------
class LocalEmbedder:
    """multilingual-e5-small: free, offline, great for EN/DE/IT."""

    def __init__(self, model_name: str = LOCAL_MODEL):
        from sentence_transformers import SentenceTransformer  # heavy import

        self.model = SentenceTransformer(model_name)

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors = self.model.encode(
            [_E5_PASSAGE_PREFIX + t for t in texts],
            normalize_embeddings=True,
            show_progress_bar=True,
        )
        return vectors.tolist()


class GeminiEmbedder:
    """gemini-embedding-2 via the free Google AI Studio tier."""

    def __init__(self, dims: int = 1536, model_name: str = GEMINI_MODEL):
        from google import genai  # light import

        if not os.environ.get("GEMINI_API_KEY") and not os.environ.get("GOOGLE_API_KEY"):
            raise SystemExit("Set the GEMINI_API_KEY environment variable (free key from Google AI Studio).")
        self.client = genai.Client()
        self.model_name = model_name
        self.dims = dims

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), 100):  # batch to stay inside free limits
            batch = texts[start:start + 100]
            response = self.client.models.embed_content(
                model=self.model_name,
                contents=batch,
                output_dimensionality=self.dims,
            )
            vectors.extend(e.values for e in response.embeddings)
            print(f"  embedded {min(start + 100, len(texts))}/{len(texts)}")
        return vectors


# --------------------------------------------------------------------------
# Indexing
# --------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="Embed chunks into a Chroma vector DB.")
    parser.add_argument("--chunks", default=Path("build/chunks.jsonl"), type=Path)
    parser.add_argument("--persist", default=Path("build/chroma"), type=Path,
                        help="where the vector DB lives (default: build/chroma)")
    parser.add_argument("--provider", choices=["local", "gemini"], default="local")
    parser.add_argument("--dims", type=int, default=1536,
                        help="gemini only: output dimensions 128-3072 (default: 1536)")
    args = parser.parse_args()

    rows = [json.loads(line) for line in args.chunks.read_text(encoding="utf-8").splitlines() if line]
    if not rows:
        raise SystemExit(f"No chunks in {args.chunks} -- run 'python -m ingest.run' first.")
    print(f"Loaded {len(rows)} chunks from {args.chunks}")

    embedder = GeminiEmbedder(dims=args.dims) if args.provider == "gemini" else LocalEmbedder()
    vectors = embedder.embed([row["text"] for row in rows])

    import chromadb  # light import, only needed now

    client = chromadb.PersistentClient(path=str(args.persist))
    collection = client.get_or_create_collection(
        name="robog", metadata={"hnsw:space": "cosine"}
    )
    # Chroma metadata values must be str / int / float / bool -- drop the rest
    # (e.g. null section_number for unnumbered headings).
    metadatas = [
        {k: v for k, v in row["metadata"].items() if isinstance(v, (str, int, float, bool))}
        for row in rows
    ]
    collection.upsert(
        ids=[row["id"] for row in rows],
        documents=[row["text"] for row in rows],
        metadatas=metadatas,
        embeddings=vectors,
    )
    print(f"Indexed {collection.count()} chunks into {args.persist} (collection 'robog').")


if __name__ == "__main__":
    main()
