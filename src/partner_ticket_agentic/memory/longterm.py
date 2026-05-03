"""Long-term memory tier — FAISS vector index over runbooks + SQLite facts.

The runbook corpus from ``data/runbooks.json`` is loaded into a FAISS
inner-product index over deterministic feature-hashed embeddings. Using a
hashing vectoriser (rather than a model-based embedder) keeps the demo
offline and reproducible: the same input yields the same vector on every
machine, on every run, without an API call.

A reviewer reading this module should be able to predict retrieval
behaviour without running anything — that's the same determinism contract
the mock LLM honours, applied to the retrieval layer. Production
deployments would swap the hashing embedder for a real embedding model
(Ollama ``nomic-embed-text``, Anthropic Voyage, etc.) by replacing
:func:`embed_text` only — the surrounding plumbing is identical.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

EMBED_DIM = 128
"""Dimensionality of the deterministic feature-hashed embedding."""


_TOKEN_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9]+|\d+")


def _tokenize(text: str) -> list[str]:
    return [tok.lower() for tok in _TOKEN_PATTERN.findall(text)]


def _stable_hash(token: str) -> int:
    """Deterministic 64-bit hash that doesn't depend on PYTHONHASHSEED.

    Python's ``hash()`` is randomised per process; using it would break
    determinism across runs. FNV-1a over UTF-8 is dependency-free and
    stable, which is what we need for reproducible retrieval.
    """

    h = 0xCBF29CE484222325
    for byte in token.encode("utf-8"):
        h ^= byte
        h = (h * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return h


def embed_text(text: str, dim: int = EMBED_DIM) -> np.ndarray:
    """Map text to a unit-norm ``dim``-dimensional vector deterministically.

    The hashing trick: each token contributes ±1 to one bucket chosen by a
    stable hash. The resulting vector is L2-normalised so FAISS's inner
    product equals cosine similarity. Empty strings return a zero vector,
    which never matches anything — a deliberate failure mode that's
    cheaper than raising and easier to surface in tests.
    """

    vec = np.zeros(dim, dtype=np.float32)
    tokens = _tokenize(text)
    if not tokens:
        return vec
    for tok in tokens:
        h = _stable_hash(tok)
        bucket = h % dim
        sign = 1.0 if (h >> 32) & 1 else -1.0
        vec[bucket] += sign
    norm = float(np.linalg.norm(vec))
    if norm > 0.0:
        vec /= norm
    return vec


@dataclass(frozen=True, slots=True)
class RunbookHit:
    """One match from the long-term runbook index."""

    runbook_id: str
    title: str
    category: str
    score: float
    summary: str
    owner_queue: str
    sla_minutes: int


class LongTermMemory:
    """FAISS-backed runbook index plus a stub for structured facts.

    Built from a list of runbook dicts (loaded by default from
    ``data/runbooks.json``). The index is in-memory; rebuild on every
    process start. With ~10 runbooks the build is microseconds, so
    persisting to disk would be premature.
    """

    def __init__(self, runbooks: Iterable[dict[str, Any]]) -> None:
        try:
            import faiss
        except ImportError as exc:
            raise RuntimeError("faiss-cpu must be installed for long-term memory") from exc
        self._runbooks = list(runbooks)
        if not self._runbooks:
            raise ValueError("LongTermMemory requires at least one runbook")
        self._dim = EMBED_DIM
        vectors = np.stack(
            [embed_text(_runbook_text(rb), self._dim) for rb in self._runbooks]
        ).astype(np.float32)
        self._index = faiss.IndexFlatIP(self._dim)
        self._index.add(vectors)

    def search(self, query: str, k: int = 3) -> list[RunbookHit]:
        """Return the top-``k`` runbooks ranked by cosine similarity to ``query``.

        ``k`` is silently clamped to the number of indexed runbooks. The
        score is in ``[-1, 1]`` (inner product of unit vectors); higher is
        better. Scores below zero are returned anyway — callers decide on
        a threshold.
        """

        q = embed_text(query, self._dim).reshape(1, -1).astype(np.float32)
        k = max(1, min(k, len(self._runbooks)))
        scores, idxs = self._index.search(q, k)
        hits: list[RunbookHit] = []
        for score, idx in zip(scores[0], idxs[0], strict=True):
            if idx < 0:
                continue
            rb = self._runbooks[int(idx)]
            hits.append(
                RunbookHit(
                    runbook_id=str(rb.get("id", "")),
                    title=str(rb.get("title", "")),
                    category=str(rb.get("category", "")),
                    score=float(score),
                    summary=str(rb.get("summary", "")),
                    owner_queue=str(rb.get("owner_queue", "")),
                    sla_minutes=int(rb.get("sla_minutes", 0)),
                )
            )
        return hits

    @classmethod
    def from_seed(cls, runbooks_path: str | Path | None = None) -> LongTermMemory:
        """Load from ``data/runbooks.json`` (or a custom path) and build the index."""

        if runbooks_path is None:
            runbooks_path = _default_runbooks_path()
        with Path(runbooks_path).open("r", encoding="utf-8") as fh:
            runbooks = json.load(fh)
        if not isinstance(runbooks, list):
            raise ValueError(f"{runbooks_path}: expected a JSON array of runbooks")
        return cls(runbooks)


def _runbook_text(rb: dict[str, Any]) -> str:
    """Concatenate title + category + summary as the indexable doc text."""

    parts = [str(rb.get("title", "")), str(rb.get("category", "")), str(rb.get("summary", ""))]
    return " \n".join(p for p in parts if p)


def _default_runbooks_path() -> Path:
    here = Path(__file__).resolve()
    for parent in (here, *here.parents):
        candidate = parent / "data" / "runbooks.json"
        if candidate.exists():
            return candidate
    raise FileNotFoundError("data/runbooks.json not found relative to this module")
