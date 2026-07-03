"""Сквозной конвейер: отчёт по хвостам → диагностика → гипотезы → ранжирование."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import config
from .diagnostics import Signal, diagnose
from .generator import Hypothesis, generate_all
from .graph import KnowledgeGraph
from .llm import YandexLLM
from .ranker import rank
from .tailings import TailingsReport, parse_tailings_xlsx
from .vectorstore import VectorStore

GRAPH_PATH = config.ARTIFACTS_DIR / "knowledge_graph.json"
STORE_PATH = config.ARTIFACTS_DIR / "vectorstore"


@dataclass
class PipelineResult:
    report: TailingsReport
    signals: list[Signal]
    hypotheses: list[Hypothesis]
    goal: str
    constraints: str
    elapsed_sec: float = 0.0
    kb_stats: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "goal": self.goal,
            "constraints": self.constraints,
            "elapsed_sec": round(self.elapsed_sec, 1),
            "kb_stats": self.kb_stats,
            "report": self.report.to_dict(),
            "signals": [s.to_dict() for s in self.signals],
            "hypotheses": [h.to_dict() for h in self.hypotheses],
        }


def load_kb() -> tuple[KnowledgeGraph, VectorStore]:
    graph = (KnowledgeGraph.load(GRAPH_PATH) if GRAPH_PATH.exists()
             else KnowledgeGraph.from_seed())
    store = VectorStore(STORE_PATH)
    return graph, store


def run_pipeline(xlsx_path: str | Path, goal: str = "",
                 constraints: str = "нет",
                 weights: dict | None = None,
                 max_signals: int = 6, per_signal: int = 3,
                 progress_cb=None) -> PipelineResult:
    """progress_cb(stage: str, done: int, total: int)"""
    t0 = time.time()
    llm = YandexLLM()
    graph, store = load_kb()

    def notify(stage, done=0, total=0):
        if progress_cb:
            progress_cb(stage, done, total)

    notify("Парсинг отчёта по хвостам")
    report = parse_tailings_xlsx(xlsx_path)
    if not goal:
        goal = (f"Снизить потери цветных металлов с хвостами фабрики "
                f"{report.plant} (Ni: {report.tailings_ni_t or 0:,.0f} т, "
                f"Cu: {report.tailings_cu_t or 0:,.0f} т за период)")

    notify("Диагностика потерь")
    signals = diagnose(report)

    notify("Генерация гипотез", 0, min(max_signals, len(signals)))
    hypotheses = generate_all(
        report, signals, graph, store, llm, goal=goal, constraints=constraints,
        max_signals=max_signals, per_signal=per_signal,
        progress_cb=lambda d, t: notify("Генерация гипотез", d, t))

    notify("Ранжирование")
    hypotheses = rank(hypotheses, llm, weights=weights)

    return PipelineResult(
        report=report, signals=signals, hypotheses=hypotheses,
        goal=goal, constraints=constraints,
        elapsed_sec=time.time() - t0,
        kb_stats={**graph.stats(), "literature_chunks": len(store)})


def save_result(result: PipelineResult, path: str | Path) -> None:
    Path(path).write_text(
        json.dumps(result.to_dict(), ensure_ascii=False, indent=1),
        encoding="utf-8")
