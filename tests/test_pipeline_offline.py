"""Офлайн-тесты (без API): парсер отчётов, диагностика, граф, ранжирование.

    python -m pytest tests/ -q     (или python tests/test_pipeline_offline.py)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hypofactory.config import DATA_DIR
from hypofactory.diagnostics import diagnose
from hypofactory.graph import KnowledgeGraph
from hypofactory.tailings import parse_tailings_xlsx

EXAMPLES = sorted((DATA_DIR / "task1").rglob("Хвосты*.xlsx"))


def test_parser_all_examples():
    assert EXAMPLES, "нет примеров в data/task1"
    for path in EXAMPLES:
        r = parse_tailings_xlsx(path)
        assert r.streams, path.name
        for s in r.streams:
            assert s.classes, f"{path.name}: {s.name} без классов"
            # сумма потерь по классам ~ итог потока (допуск 5%)
            total = sum(c.ni_t or 0 for c in s.classes)
            if s.ni_t:
                assert abs(total - s.ni_t) / s.ni_t < 0.05, \
                    f"{path.name}/{s.name}: Ni по классам {total:.0f} != {s.ni_t:.0f}"


def test_diagnostics_produce_quantified_signals():
    for path in EXAMPLES:
        signals = diagnose(parse_tailings_xlsx(path))
        assert signals, path.name
        for s in signals:
            assert s.tonnes >= 0 and s.levers and s.explanation
            assert s.share_of_stream_pct <= 100.01


def test_seed_graph_connectivity():
    g = KnowledgeGraph.from_seed()
    st = g.stats()
    assert st["nodes"] > 30 and st["edges"] > 25
    nodes, edges = g.subgraph(["pnt_locked"], depth=2)
    assert any(n.id == "regrinding" for n in nodes), \
        "закрытые сростки должны связываться с доизмельчением за 2 шага"
    text = g.subgraph_text(["mech_overgrind"])
    assert "Переизмельчение" in text


if __name__ == "__main__":
    test_parser_all_examples()
    test_diagnostics_produce_quantified_signals()
    test_seed_graph_connectivity()
    print("OK: все офлайн-тесты пройдены")
