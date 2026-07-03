"""Граф знаний: затравочная онтология + связи, извлечённые LLM из литературы.

Лёгкая собственная реализация (без Neo4j) — полная локальность и простота
развёртывания. Хранение в JSON, подграфы для GraphRAG-промптов и
визуализации.
"""
from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass, field, asdict
from pathlib import Path

from . import ontology
from .documents import Chunk
from .llm import YandexLLM


@dataclass
class Node:
    id: str
    label: str
    type: str
    desc: str = ""


@dataclass
class Edge:
    src: str
    rel: str
    dst: str
    desc: str = ""
    sources: list[str] = field(default_factory=list)  # chunk_id доказательств


class KnowledgeGraph:
    def __init__(self):
        self.nodes: dict[str, Node] = {}
        self.edges: list[Edge] = []
        self._adj: dict[str, list[int]] = {}

    # ------------------------------------------------------------- построение
    @classmethod
    def from_seed(cls) -> "KnowledgeGraph":
        g = cls()
        for n in ontology.SEED_NODES:
            g.add_node(Node(id=n["id"], label=n["label"], type=n["type"],
                            desc=n.get("desc", "")))
        for src, rel, dst, desc in ontology.SEED_EDGES:
            g.add_edge(Edge(src=src, rel=rel, dst=dst, desc=desc,
                            sources=["ontology"]))
        return g

    def add_node(self, node: Node) -> None:
        if node.id not in self.nodes:
            self.nodes[node.id] = node
            self._adj[node.id] = []

    def add_edge(self, edge: Edge) -> None:
        if edge.src not in self.nodes or edge.dst not in self.nodes:
            return
        # дедупликация: одинаковая тройка → слить источники
        for e in self.edges:
            if (e.src, e.rel, e.dst) == (edge.src, edge.rel, edge.dst):
                e.sources = list(dict.fromkeys(e.sources + edge.sources))
                if edge.desc and not e.desc:
                    e.desc = edge.desc
                return
        idx = len(self.edges)
        self.edges.append(edge)
        self._adj[edge.src].append(idx)
        self._adj[edge.dst].append(idx)

    # -------------------------------------------------- LLM-извлечение связей
    EXTRACT_PROMPT = """Ты — инженер по знаниям в области обогащения медно-никелевых руд.
Из фрагмента учебника извлеки причинно-следственные связи вида
(сущность А) -[отношение]-> (сущность Б), относящиеся к флотации, измельчению,
классификации, реагентам, потерям металлов.

Известные сущности (используй их id, если подходит):
{known}

Верни строго JSON: {{"triples": [{{"src": "...", "rel": "...", "dst": "...",
"src_label": "...", "dst_label": "...", "src_type": "mineral|operation|equipment|parameter|mechanism|reagent|kpi",
"dst_type": "...", "desc": "краткое пояснение из текста"}}]}}
Если id из списка не подходит — придумай новый короткий латинский id.
Не более 6 самых значимых связей. Если связей нет — {{"triples": []}}.

Фрагмент ({source}):
{text}"""

    def enrich_from_chunks(self, chunks: list[Chunk], llm: YandexLLM,
                           progress_cb=None) -> int:
        known = "\n".join(f"- {n.id}: {n.label} ({n.type})"
                          for n in self.nodes.values())
        added = 0
        for i, chunk in enumerate(chunks):
            try:
                data = llm.complete_json([{
                    "role": "user",
                    "text": self.EXTRACT_PROMPT.format(
                        known=known, source=chunk.cite(), text=chunk.text[:3000]),
                }], temperature=0.1)
            except Exception:
                continue
            triples = data.get("triples", []) if isinstance(data, dict) else []
            for t in triples:
                if not all(k in t for k in ("src", "rel", "dst")):
                    continue
                for end, label_key, type_key in (("src", "src_label", "src_type"),
                                                 ("dst", "dst_label", "dst_type")):
                    nid = str(t[end])[:40]
                    if nid not in self.nodes:
                        self.add_node(Node(
                            id=nid, label=str(t.get(label_key, nid))[:80],
                            type=str(t.get(type_key, "mechanism"))[:20]))
                self.add_edge(Edge(src=str(t["src"])[:40], rel=str(t["rel"])[:40],
                                   dst=str(t["dst"])[:40],
                                   desc=str(t.get("desc", ""))[:300],
                                   sources=[chunk.chunk_id]))
                added += 1
            if progress_cb:
                progress_cb(i + 1, len(chunks))
        return added

    # ------------------------------------------------------------- запросы
    def subgraph(self, seed_ids: list[str], depth: int = 2,
                 max_nodes: int = 25) -> tuple[list[Node], list[Edge]]:
        seen: set[str] = set()
        picked_edges: list[Edge] = []
        queue: deque[tuple[str, int]] = deque(
            (nid, 0) for nid in seed_ids if nid in self.nodes)
        seen.update(nid for nid, _ in queue)
        while queue and len(seen) < max_nodes:
            nid, d = queue.popleft()
            if d >= depth:
                continue
            for eidx in self._adj.get(nid, []):
                e = self.edges[eidx]
                if e not in picked_edges:
                    picked_edges.append(e)
                for other in (e.src, e.dst):
                    if other not in seen and len(seen) < max_nodes:
                        seen.add(other)
                        queue.append((other, d + 1))
        return [self.nodes[i] for i in seen if i in self.nodes], picked_edges

    def subgraph_text(self, seed_ids: list[str], depth: int = 2) -> str:
        """Подграф в текстовом виде для промпта LLM."""
        nodes, edges = self.subgraph(seed_ids, depth=depth)
        lines = ["Узлы:"]
        for n in nodes:
            d = f" — {n.desc}" if n.desc else ""
            lines.append(f"  [{n.id}] {n.label} ({n.type}){d}")
        lines.append("Связи:")
        for e in edges:
            d = f" ({e.desc})" if e.desc else ""
            lines.append(f"  {self.nodes[e.src].label} -[{e.rel}]-> "
                         f"{self.nodes[e.dst].label}{d}")
        return "\n".join(lines)

    # ------------------------------------------------------------- I/O
    def save(self, path: str | Path) -> None:
        data = {"nodes": [asdict(n) for n in self.nodes.values()],
                "edges": [asdict(e) for e in self.edges]}
        Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=1),
                              encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "KnowledgeGraph":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        g = cls()
        for n in data["nodes"]:
            g.add_node(Node(**n))
        for e in data["edges"]:
            g.add_edge(Edge(**e))
        return g

    def stats(self) -> dict:
        from collections import Counter
        return {"nodes": len(self.nodes), "edges": len(self.edges),
                "node_types": dict(Counter(n.type for n in self.nodes.values()))}
