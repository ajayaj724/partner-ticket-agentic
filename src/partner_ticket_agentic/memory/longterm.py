"""Long-term memory tier — hybrid (BM25 + dense) retrieval over runbooks.

The runbook corpus from ``data/runbooks.json`` is indexed two ways:

* **Dense** — FAISS inner-product index over deterministic feature-hashed
  embeddings (``embed_text``). Captures rough semantic similarity.
* **BM25** — classical lexical scoring (Robertson/Spärck-Jones), pure-Python
  implementation. Captures exact-keyword match strength.

:meth:`LongTermMemory.search` blends the two with min-max normalisation
plus a configurable ``alpha`` (default 0.5), matching slide 14 of the
panel deck ("Hybrid retrieval: BM25 + dense; cross-encoder re-ranker").
The hashing embedder + hand-rolled BM25 keep the demo offline and
deterministic: a reviewer can predict retrieval behaviour without running
anything. Production deployments would swap the hashing embedder for a
real embedding model (Ollama ``nomic-embed-text``, Anthropic Voyage, etc.)
by replacing :func:`embed_text` only — the surrounding plumbing is
identical.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

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


class _BM25:
    """Pure-Python Okapi BM25 (Robertson/Spärck-Jones) over tokenised docs.

    Hand-rolled rather than imported so a reviewer can read the math:

        score(q, d) = sum_i IDF(qi) · (tf · (k1+1)) / (tf + k1 · (1 - b + b · |d|/avgdl))

    with the standard ``k1 = 1.5`` and ``b = 0.75`` defaults. Eight runbooks
    means tf/df lookups are dict-ops; no library needed.
    """

    def __init__(self, docs: list[list[str]], *, k1: float = 1.5, b: float = 0.75) -> None:
        self._docs = docs
        self._N = len(docs)
        self._k1 = k1
        self._b = b
        self._doclen = [len(d) for d in docs]
        self._avgdl = sum(self._doclen) / max(1, self._N)
        self._tf: list[Counter[str]] = [Counter(d) for d in docs]
        self._df: Counter[str] = Counter()
        for tf in self._tf:
            for term in tf:
                self._df[term] += 1
        # Precompute IDF: log((N - df + 0.5) / (df + 0.5) + 1) — the BM25+
        # variant that keeps IDF strictly positive.
        self._idf: dict[str, float] = {
            term: math.log((self._N - df + 0.5) / (df + 0.5) + 1.0) for term, df in self._df.items()
        }

    def score(self, query_tokens: list[str]) -> list[float]:
        """Return one BM25 score per document for ``query_tokens``."""

        scores = [0.0] * self._N
        for term in query_tokens:
            idf = self._idf.get(term)
            if idf is None:
                continue
            for i, tf in enumerate(self._tf):
                tfi = tf.get(term, 0)
                if tfi == 0:
                    continue
                norm = 1.0 - self._b + self._b * (self._doclen[i] / max(1.0, self._avgdl))
                scores[i] += idf * (tfi * (self._k1 + 1.0)) / (tfi + self._k1 * norm)
        return scores


def _minmax(values: list[float]) -> list[float]:
    """Min-max-normalise a list to [0, 1] — used to blend BM25 + dense."""

    if not values:
        return values
    lo, hi = min(values), max(values)
    if hi - lo < 1e-12:
        return [0.0 for _ in values]
    return [(v - lo) / (hi - lo) for v in values]


class LongTermMemory:
    """Hybrid (BM25 + dense) runbook index plus a stub for structured facts.

    Built from a list of runbook dicts (loaded by default from
    ``data/runbooks.json``). Both indexes are in-memory; rebuild on every
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
        texts = [_runbook_text(rb) for rb in self._runbooks]
        vectors = np.stack([embed_text(t, self._dim) for t in texts]).astype(np.float32)
        self._index = faiss.IndexFlatIP(self._dim)
        self._index.add(vectors)
        # BM25 index uses the same tokeniser as the dense embedder so a
        # term that lands in one is visible to the other.
        self._bm25 = _BM25([_tokenize(t) for t in texts])

    def search(
        self,
        query: str,
        k: int = 3,
        *,
        mode: Literal["hybrid", "dense", "bm25"] = "hybrid",
        alpha: float = 0.5,
    ) -> list[RunbookHit]:
        """Return the top-``k`` runbooks for ``query``.

        ``mode``:

        * ``"dense"`` — FAISS cosine over hashed embeddings only. Scores
          are in ``[-1, 1]``.
        * ``"bm25"`` — Okapi BM25 lexical scoring only. Scores are
          non-negative (unbounded above; typically 0 to 10 on small corpora).
        * ``"hybrid"`` *(default)* — both signals min-max-normalised to
          ``[0, 1]`` then blended as ``alpha · dense + (1 - alpha) · bm25``.
          With ``alpha = 0.5`` the two signals weigh equally.

        ``k`` is silently clamped to the corpus size. Results carry the
        blended score in ``hit.score`` so a caller can apply a threshold.
        """

        k = max(1, min(k, len(self._runbooks)))

        # ---- dense ----
        q_vec = embed_text(query, self._dim).reshape(1, -1).astype(np.float32)
        dense_scores_arr, _ = self._index.search(q_vec, len(self._runbooks))
        dense_scores = [float(s) for s in dense_scores_arr[0]]
        # FAISS returns in score-descending order with permuted indices;
        # re-align so position i in the list is doc i (not the rank).
        _, idx_arr = self._index.search(q_vec, len(self._runbooks))
        aligned_dense = [0.0] * len(self._runbooks)
        for rank, doc_i in enumerate(idx_arr[0]):
            if doc_i >= 0:
                aligned_dense[int(doc_i)] = dense_scores[rank]

        # ---- bm25 ----
        bm25_scores = self._bm25.score(_tokenize(query))

        # ---- blend ----
        if mode == "dense":
            blended = aligned_dense
        elif mode == "bm25":
            blended = bm25_scores
        else:
            d_norm = _minmax(aligned_dense)
            b_norm = _minmax(bm25_scores)
            blended = [alpha * d + (1.0 - alpha) * b for d, b in zip(d_norm, b_norm, strict=True)]

        ranked = sorted(range(len(self._runbooks)), key=lambda i: blended[i], reverse=True)
        hits: list[RunbookHit] = []
        for doc_i in ranked[:k]:
            rb = self._runbooks[doc_i]
            hits.append(
                RunbookHit(
                    runbook_id=str(rb.get("id", "")),
                    title=str(rb.get("title", "")),
                    category=str(rb.get("category", "")),
                    score=float(blended[doc_i]),
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
