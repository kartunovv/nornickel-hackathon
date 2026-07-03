"""Локальный векторный индекс: numpy-матрица + jsonl-метаданные.

Никаких внешних БД — полная локальность (требование безопасности кейса).
Эмбеддинги Yandex text-search-doc/query (256-мерные), косинусная близость.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from .documents import Chunk
from .llm import YandexLLM


class VectorStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.meta_path = self.path.with_suffix(".jsonl")
        self.vec_path = self.path.with_suffix(".npy")
        self.chunks: list[Chunk] = []
        self.matrix: np.ndarray | None = None
        if self.meta_path.exists() and self.vec_path.exists():
            self._load()

    def _load(self) -> None:
        self.chunks = []
        for line in self.meta_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                self.chunks.append(Chunk(**json.loads(line)))
        self.matrix = np.load(self.vec_path)

    def build(self, chunks: list[Chunk], llm: YandexLLM, progress_cb=None) -> None:
        vecs = llm.embed_many([c.text for c in chunks], kind="doc",
                              progress_cb=progress_cb)
        self.chunks = chunks
        self.matrix = _normalize(np.array(vecs, dtype=np.float32))
        self.save()

    def save(self) -> None:
        with self.meta_path.open("w", encoding="utf-8") as f:
            for c in self.chunks:
                f.write(json.dumps(c.to_dict(), ensure_ascii=False) + "\n")
        np.save(self.vec_path, self.matrix)

    def search(self, query: str, llm: YandexLLM, top_k: int = 8,
               kinds: set[str] | None = None) -> list[tuple[Chunk, float]]:
        if self.matrix is None or not len(self.chunks):
            return []
        q = np.array(llm.embed(query, kind="query"), dtype=np.float32)
        q = q / (np.linalg.norm(q) + 1e-9)
        sims = self.matrix @ q
        order = np.argsort(-sims)
        out: list[tuple[Chunk, float]] = []
        for idx in order:
            c = self.chunks[int(idx)]
            if kinds and c.kind not in kinds:
                continue
            out.append((c, float(sims[int(idx)])))
            if len(out) >= top_k:
                break
        return out

    def __len__(self) -> int:
        return len(self.chunks)


def _normalize(m: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(m, axis=1, keepdims=True)
    return m / np.maximum(norms, 1e-9)
