"""Генератор гипотез: сигнал диагностики + подграф знаний + литература → LLM.

GraphRAG-подход: языковая модель формулирует гипотезы строго на основе
переданного контекста (квантифицированный сигнал, подграф причинных связей,
цитаты из учебников), а не собственных домыслов. Каждая гипотеза обязана
ссылаться на механизм из графа и источники.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field, asdict

from . import ontology
from .diagnostics import Signal
from .graph import KnowledgeGraph
from .llm import YandexLLM
from .tailings import TailingsReport
from .vectorstore import VectorStore

# Примеры формулировок с реальных мозговых штурмов (стиль-анкоры из кейса)
STYLE_EXAMPLES = """- «Магнитная сепарация надцелевого класса с последующим доизмельчением в отдельном цикле»
- «Замена песковых насадок на гидроциклонах с уменьшением диаметра с 12 на 8»
- «Перераспределение фронта флотации для увеличения времени первой контрольной операции»
- «Добавление промежуточных контактных чанов для увеличения времени агитации перед контрольной флотацией»
- «Переход на 100% загрузку мелящих шаров диаметром 120 мм»"""

PROMPT_TEMPLATE = """Ты — ведущий технолог-исследователь обогатительной фабрики (медно-никелевые руды, Норильский Никель).

## Задача
{goal}
Ограничения: {constraints}

## Диагностический сигнал (факты из отчёта института по хвостам)
{signal}

## Подграф корпоративного графа знаний (причинно-следственные связи)
{subgraph}

## Выдержки из литературы (цитируй их номерами [1], [2], ...)
{evidence}

## Кандидатные направления (рычаги) для этого сигнала
{levers}

## Требования к гипотезам
1. Каждая гипотеза — конкретное проверяемое утверждение вида: «изменение X приведёт к снижению потерь {metal} за счёт механизма Z».
2. Используй ТОЛЬКО факты из сигнала, графа и выдержек. Не выдумывай числа, которых нет в данных.
3. lever_id выбирай СТРОГО из списка кандидатных рычагов выше — другие рычаги для этого сигнала технически неприменимы (например, флотационные реагенты и доизмельчение НЕ извлекают силикатные формы и изоморфные примеси в решётке минерала). Гипотеза с посторонним lever_id будет отброшена.
4. Ожидаемый эффект оценивай долей от тоннажа сигнала ({tonnes:,.0f} т {metal}/период) с консервативной вилкой.
5. Стиль формулировок как на мозговых штурмах фабрик:
{style}

Сгенерируй {n} РАЗНЫХ гипотез (разные рычаги!). Верни строго JSON:
{{"hypotheses": [{{
  "statement": "формулировка гипотезы одним предложением",
  "mechanism": "механизм влияния (из графа знаний)",
  "rationale": "обоснование 2-4 предложения с опорой на данные сигнала и источники [N]",
  "lever_id": "id рычага из списка кандидатов",
  "expected_effect": {{"metal": "{metal}", "min_recovery_pct": 5, "max_recovery_pct": 20, "basis": "чего % — например 'от тоннажа сигнала'"}},
  "citations": [1, 2],
  "risks": ["технический риск", "экономический риск"],
  "test_plan": ["шаг 1 лабораторной проверки", "шаг 2", "критерий успеха: ..."],
  "required_resources": "какие пробы/оборудование/время нужны"
}}]}}"""


@dataclass
class Hypothesis:
    id: str
    statement: str
    mechanism: str
    rationale: str
    lever_id: str
    lever_label: str
    signal_id: str
    signal_title: str
    stream: str
    metal: str
    signal_tonnes: float
    expected_effect: dict
    citations: list[dict] = field(default_factory=list)   # {n, source, quote}
    risks: list[str] = field(default_factory=list)
    test_plan: list[str] = field(default_factory=list)
    required_resources: str = ""
    graph_nodes: list[str] = field(default_factory=list)
    capex_class: int = 1
    # заполняется ранжированием:
    scores: dict = field(default_factory=dict)
    total_score: float = 0.0
    rank: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


def generate_for_signal(signal: Signal, graph: KnowledgeGraph, store: VectorStore,
                        llm: YandexLLM, goal: str, constraints: str = "нет",
                        n_hypotheses: int = 3) -> list[Hypothesis]:
    # 1) выдержки из литературы под сигнал
    lever_labels = [ontology.LEVERS[lv]["label"] for lv in signal.levers
                    if lv in ontology.LEVERS]
    query = f"{signal.title}. {'; '.join(lever_labels[:4])}"
    # цитируем только литературу (книги/статьи); docx мозговых штурмов
    # уже присутствуют в промпте как стиль-анкоры и не должны подменять
    # собой обоснование
    found = store.search(query, llm, top_k=6,
                         kinds={"book", "article"}) if len(store) else []
    evidence_lines, citations_map = [], {}
    for n, (chunk, sim) in enumerate(found, start=1):
        snippet = chunk.text[:700].strip()
        evidence_lines.append(f"[{n}] ({chunk.cite()}) {snippet}")
        citations_map[n] = {"n": n, "source": chunk.cite(),
                            "chunk_id": chunk.chunk_id,
                            "quote": snippet[:300]}
    evidence = "\n\n".join(evidence_lines) or "(выдержки недоступны)"

    # 2) подграф знаний
    subgraph = graph.subgraph_text(signal.graph_nodes, depth=2)

    # 3) рычаги-кандидаты
    lever_lines = []
    for lv in signal.levers:
        if lv in ontology.LEVERS:
            info = ontology.LEVERS[lv]
            capex = {0: "настройка", 1: "модернизация", 2: "новый передел"}[info["capex"]]
            lever_lines.append(f"- {lv}: {info['label']} (класс затрат: {capex}; "
                               f"проверка: {info['test']})")

    n_hypotheses = min(n_hypotheses, max(1, len(signal.levers)))
    prompt = PROMPT_TEMPLATE.format(
        goal=goal, constraints=constraints,
        signal=f"{signal.title}\n{signal.explanation}",
        subgraph=subgraph, evidence=evidence,
        levers="\n".join(lever_lines), style=STYLE_EXAMPLES,
        metal=signal.metal, tonnes=signal.tonnes, n=n_hypotheses)

    data = llm.complete_json([{"role": "user", "text": prompt}],
                             temperature=0.4, max_tokens=4000)
    if isinstance(data, dict):
        raw = data.get("hypotheses", [])
    elif isinstance(data, list):
        raw = data
    else:
        raw = []

    out: list[Hypothesis] = []
    for h in raw:
        statement = str(h.get("statement", "")).strip()
        if not statement:
            continue
        lever_id = str(h.get("lever_id", ""))
        if lever_id not in signal.levers:
            # рычаг вне кандидатов сигнала = технически неприменимое
            # предложение (например, флотация для неизвлекаемых форм) — отбраковка
            continue
        lever = ontology.LEVERS.get(lever_id, {})
        cits = []
        for c in h.get("citations", []):
            try:
                num = int(c)
            except (TypeError, ValueError):
                continue
            if num in citations_map:
                cits.append(citations_map[num])
        hid = hashlib.sha256(f"{signal.id}|{statement}".encode()).hexdigest()[:10]
        out.append(Hypothesis(
            id=hid, statement=statement,
            mechanism=str(h.get("mechanism", ""))[:300],
            rationale=str(h.get("rationale", ""))[:1500],
            lever_id=lever_id,
            lever_label=lever.get("label", lever_id),
            signal_id=signal.id, signal_title=signal.title,
            stream=signal.stream, metal=signal.metal,
            signal_tonnes=signal.tonnes,
            expected_effect=_sanitize_effect(h.get("expected_effect"), signal),
            citations=cits,
            risks=[str(r)[:300] for r in h.get("risks", [])][:5],
            test_plan=[str(t)[:300] for t in h.get("test_plan", [])][:8],
            required_resources=str(h.get("required_resources", ""))[:500],
            graph_nodes=signal.graph_nodes + lever.get("nodes", []),
            capex_class=lever.get("capex", 1)))
    return out


def _sanitize_effect(effect, signal: Signal) -> dict:
    if not isinstance(effect, dict):
        effect = {}
    lo = _to_float(effect.get("min_recovery_pct"), 5.0)
    hi = _to_float(effect.get("max_recovery_pct"), 15.0)
    lo, hi = max(0.0, min(lo, 100.0)), max(0.0, min(hi, 100.0))
    if lo > hi:
        lo, hi = hi, lo
    return {
        "metal": signal.metal,
        "min_recovery_pct": lo,
        "max_recovery_pct": hi,
        "basis": str(effect.get("basis", "от тоннажа сигнала"))[:120],
        "tonnes_min": signal.tonnes * lo / 100,
        "tonnes_max": signal.tonnes * hi / 100,
    }


def _to_float(v, default: float) -> float:
    try:
        return float(re.sub(r"[^\d.\-]", "", str(v))) if v is not None else default
    except ValueError:
        return default


def generate_all(report: TailingsReport, signals: list[Signal],
                 graph: KnowledgeGraph, store: VectorStore, llm: YandexLLM,
                 goal: str, constraints: str = "нет",
                 max_signals: int = 6, per_signal: int = 3,
                 progress_cb=None) -> list[Hypothesis]:
    hypos: list[Hypothesis] = []
    top = signals[:max_signals]
    for i, sig in enumerate(top):
        try:
            hypos.extend(generate_for_signal(
                sig, graph, store, llm, goal=goal, constraints=constraints,
                n_hypotheses=per_signal))
        except Exception as e:
            print(f"  ! сигнал {sig.id}: {e}")
        if progress_cb:
            progress_cb(i + 1, len(top))
    # дедупликация одинаковых формулировок
    seen: set[str] = set()
    unique: list[Hypothesis] = []
    for h in hypos:
        key = re.sub(r"\W+", "", h.statement.lower())[:80]
        if key not in seen:
            seen.add(key)
            unique.append(h)
    return unique
