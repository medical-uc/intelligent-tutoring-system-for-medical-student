"""Semantic chunking: groups UnifiedItems (from unify.py) into topically
coherent chunks for downstream concept extraction, using embedding
similarity instead of heading structure or LLM-judged continuity.

Each item (a page's transcribed text, or a [FIGURE:...] caption block) is
embedded as one unit, in document order. A chunk boundary is placed
wherever the cosine distance between consecutive item embeddings exceeds
a percentile-based threshold over the whole document — sections that
drift topic sharply split apart; sections that stay on-topic (including a
figure immediately following the text it illustrates) stay together.

Percentile-based (not a fixed distance cutoff) so the threshold adapts to
each document's own similarity distribution instead of a magic number
tuned for one document's writing style.
"""

import gc

import numpy as np
import torch
from sentence_transformers import SentenceTransformer

from ingestion.unify import UnifiedItem

MODEL_PATH = "all-MiniLM-L6-v2"


def get_device() -> str:
    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


class SemanticChunker:
    def __init__(self, model_path: str = MODEL_PATH):
        self.model_path = model_path
        self.device = get_device()
        self.model = None

    def load(self):
        if self.model is not None:
            return
        self.model = SentenceTransformer(self.model_path, device=self.device)

    def unload(self):
        self.model = None
        gc.collect()
        if torch.backends.mps.is_available():
            torch.mps.empty_cache()
        elif torch.cuda.is_available():
            torch.cuda.empty_cache()

    def chunk(
        self,
        items: list[UnifiedItem],
        percentile: float = 95.0,
        max_chars: int = 6000,
    ) -> list[list[UnifiedItem]]:
        """percentile: how sharp a topic shift must be (relative to this
        document's own distance distribution) to trigger a new chunk —
        higher = fewer, larger chunks.

        max_chars: hard fallback split so one semantically-coherent but
        very long run of items still can't exceed the extraction model's
        context window."""
        if not items:
            return []
        if len(items) == 1:
            return [items]

        self.load()
        embeddings = self.model.encode([item.text for item in items], convert_to_numpy=True)

        distances = 1.0 - _cosine_similarities(embeddings)
        threshold = np.percentile(distances, percentile)

        chunks: list[list[UnifiedItem]] = [[items[0]]]
        chunk_chars = len(items[0].text)

        for item, distance in zip(items[1:], distances):
            starts_new_topic = distance > threshold
            exceeds_budget = chunk_chars + len(item.text) > max_chars
            if starts_new_topic or exceeds_budget:
                chunks.append([item])
                chunk_chars = len(item.text)
            else:
                chunks[-1].append(item)
                chunk_chars += len(item.text)

        return chunks


def _cosine_similarities(embeddings: np.ndarray) -> np.ndarray:
    """Cosine similarity between each consecutive pair of rows, i.e.
    embeddings[i] vs embeddings[i+1] for i in range(len(embeddings) - 1)."""
    a, b = embeddings[:-1], embeddings[1:]
    a_norm = a / np.linalg.norm(a, axis=1, keepdims=True)
    b_norm = b / np.linalg.norm(b, axis=1, keepdims=True)
    return np.sum(a_norm * b_norm, axis=1)
