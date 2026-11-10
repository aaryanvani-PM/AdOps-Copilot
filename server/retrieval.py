"""Runbook retrieval (the "R" in RAG).

Each runbook is split into sections at its '##' headings. A query is scored against
every section with BM25, a standard keyword-ranking method, and the top sections are
returned with their source file and heading so the answer can cite them.

Why BM25 and not embeddings: it runs offline, needs no API key, and is easy to
inspect. With a small, clearly written runbook set, keyword match works well.
Embeddings are the natural upgrade once the document set grows.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from rank_bm25 import BM25Okapi

RUNBOOK_DIR = Path(__file__).resolve().parent.parent / "data" / "runbooks"
_TOKEN = re.compile(r"[a-z0-9%]+")
_STOP = {"the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "is", "are", "be",
         "what", "how", "our", "we", "it", "this", "that", "with", "by", "at", "as", "do", "does"}


def _tokens(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower()) if t not in _STOP]


@lru_cache(maxsize=1)
def _index() -> tuple[list[dict], BM25Okapi]:
    sections = []
    for path in sorted(RUNBOOK_DIR.glob("*.md")):
        text = path.read_text()
        title = text.splitlines()[0].lstrip("# ").strip()
        parts = re.split(r"\n(?=## )", text)
        for part in parts[1:] if len(parts) > 1 else parts:
            heading = part.splitlines()[0].lstrip("# ").strip()
            body = "\n".join(part.splitlines()[1:]).strip()
            sections.append({"source": path.name, "title": title, "section": heading, "text": body})
    corpus = [_tokens(f'{s["title"]} {s["section"]} {s["text"]}') for s in sections]
    return sections, BM25Okapi(corpus)


def search_runbooks(query: str, k: int = 3) -> dict:
    """Return the top-k runbook sections for a query, with sources."""
    sections, bm25 = _index()
    q = _tokens(query)
    if not q:
        return {"query": query, "results": []}
    scores = bm25.get_scores(q)
    ranked = sorted(range(len(sections)), key=lambda i: scores[i], reverse=True)
    k = max(1, min(int(k), 5))
    results = [{**sections[i], "score": round(float(scores[i]), 3)}
               for i in ranked[:k] if scores[i] > 0]
    return {"query": query, "results": results}
