"""Построение базы знаний: индексация литературы + обогащение графа знаний.

Запускается один раз (или при добавлении новых документов):
    python scripts/build_kb.py [--max-chunks N] [--enrich-chunks N]

Результат в artifacts/: vectorstore.{npy,jsonl}, knowledge_graph.json.
Все вызовы API кэшируются — повторный запуск почти бесплатен.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hypofactory import config
from hypofactory.documents import load_directory
from hypofactory.graph import KnowledgeGraph
from hypofactory.llm import YandexLLM
from hypofactory.pipeline import GRAPH_PATH, STORE_PATH
from hypofactory.vectorstore import VectorStore


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default=str(config.DATA_DIR / "task1"))
    ap.add_argument("--max-chunks", type=int, default=1200,
                    help="максимум чанков для векторного индекса")
    ap.add_argument("--enrich-chunks", type=int, default=80,
                    help="сколько чанков прогнать через LLM-извлечение связей")
    args = ap.parse_args()

    llm = YandexLLM()

    print("1/3 Загрузка и фильтрация литературы...")
    chunks = load_directory(args.source, filter_domain=True)
    print(f"    релевантных чанков: {len(chunks)}")
    if len(chunks) > args.max_chunks:
        # приоритет: статьи/отчёты целиком, книги — равномерная выборка
        books = [c for c in chunks if c.kind == "book"]
        rest = [c for c in chunks if c.kind != "book"]
        step = max(1, len(books) // (args.max_chunks - len(rest)))
        chunks = rest + books[::step]
        print(f"    после выборки: {len(chunks)}")

    print("2/3 Векторный индекс (эмбеддинги Yandex)...")
    store = VectorStore(STORE_PATH)
    done_ids = {c.chunk_id for c in store.chunks}
    if done_ids and len(done_ids) >= len(chunks):
        print(f"    индекс уже построен: {len(store)} чанков (пропуск)")
    else:
        store.build(chunks, llm, progress_cb=lambda d, t: print(
            f"    {d}/{t}", end="\r") if d % 25 == 0 or d == t else None)
        print(f"\n    готово: {len(store)} чанков")

    print("3/3 Обогащение графа знаний из литературы (LLM)...")
    graph = (KnowledgeGraph.load(GRAPH_PATH) if GRAPH_PATH.exists()
             else KnowledgeGraph.from_seed())
    print(f"    старт: {graph.stats()}")
    # самые содержательные чанки: приоритет флотационной книги
    def score(c):
        low = c.text.lower()
        return sum(low.count(k) for k in
                   ("флотац", "извлечен", "реагент", "измельчен", "крупност"))
    ranked = sorted(chunks, key=score, reverse=True)[:args.enrich_chunks]
    added = graph.enrich_from_chunks(
        ranked, llm, progress_cb=lambda d, t: print(f"    {d}/{t}", end="\r")
        if d % 5 == 0 or d == t else None)
    graph.save(GRAPH_PATH)
    print(f"\n    добавлено связей: {added}; итог: {graph.stats()}")


if __name__ == "__main__":
    main()
