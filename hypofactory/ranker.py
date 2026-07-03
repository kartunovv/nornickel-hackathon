"""Прозрачное ранжирование гипотез по пяти объяснимым критериям.

Каждый критерий — число 0..1 с текстовым объяснением, итог — взвешенная
сумма с настраиваемыми весами. В карточке гипотезы виден вклад каждого
критерия — никаких «чёрных ящиков».

Критерии:
- value        — потенциальная ценность: тонны возвращаемого металла × цена;
- feasibility  — реализуемость: класс затрат рычага (настройка/модернизация/передел);
- novelty      — новизна: непохожесть на известные решения фабрик (эмбеддинги)
                 и на «текущую схему» из регламентов;
- evidence     — обоснованность: цитаты из литературы + связность с графом;
- testability  — проверяемость: полнота плана эксперимента и критериев успеха.

Фидбэк экспертов (принято/отклонено) смещает оценку похожих гипотез —
механизм «обучения на фидбэке».
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from . import config
from .generator import Hypothesis
from .llm import YandexLLM

# Известные решения (уже предлагались на мозговых штурмах фабрик — из кейса).
# Совпадение с ними снижает новизну, но не обнуляет её: для другой фабрики
# решение может быть новым.
KNOWN_SOLUTIONS = [
    "Магнитная сепарация надцелевого класса с последующим доизмельчением в отдельном цикле",
    "Изменение геометрии футеровки шаровых мельниц",
    "Замена песковых насадок на гидроциклонах с уменьшением диаметра с 12 на 8",
    "Полная замена классификаторов на гидроциклоны",
    "Грохота тонкого грохочения после 2 стадии измельчения",
    "Контроль гранулометрии руды после конусных дробилок",
    "Автоматическое регулирование зазора щели конусных дробилок",
    "Автоматизация подачи воды в шаровые мельницы второго этапа измельчения",
    "Перераспределение фронта флотации для увеличения времени первой контрольной операции",
    "Повышение целевой плотности пульпы на входе в основную флотацию",
    "Добавление промежуточных контактных чанов для увеличения времени агитации",
    "Добавление реагента Finfix 300 в контактные чаны перед основной флотацией",
    "Переход на 100% загрузку мелящих шаров диаметром 120мм",
    "Донастройка скорости вращения классификаторов",
    "Классификация хвостов и возврат в голову процесса",
]

FEEDBACK_PATH = config.ARTIFACTS_DIR / "feedback.json"


def rank(hypotheses: list[Hypothesis], llm: YandexLLM,
         weights: dict[str, float] | None = None,
         feedback_path: Path = FEEDBACK_PATH) -> list[Hypothesis]:
    if not hypotheses:
        return []
    w = dict(config.DEFAULT_WEIGHTS)
    if weights:
        w.update({k: float(v) for k, v in weights.items() if k in w})
    total_w = sum(w.values()) or 1.0
    w = {k: v / total_w for k, v in w.items()}

    h_vecs = _embed_all([h.statement for h in hypotheses], llm)
    known_vecs = _embed_all(KNOWN_SOLUTIONS, llm)
    feedback = _load_feedback(feedback_path)
    fb_vecs, fb_signs = _feedback_vectors(feedback, llm)

    max_value = max(_expected_value_usd(h) for h in hypotheses) or 1.0

    for i, h in enumerate(hypotheses):
        scores: dict[str, dict] = {}

        # --- ценность
        value_usd = _expected_value_usd(h)
        s = min(1.0, value_usd / max_value)
        scores["value"] = {
            "score": round(s, 3),
            "explanation": (
                f"Ожидаемый возврат {h.expected_effect['tonnes_min']:,.0f}–"
                f"{h.expected_effect['tonnes_max']:,.0f} т {h.metal}/период ≈ "
                f"${value_usd / 1e6:,.1f} млн/период (оценка по цене "
                f"{_main_metal(h)} ${config.METAL_PRICE_USD.get(_main_metal(h), 0):,.0f}/т)")}

        # --- реализуемость
        s = {0: 1.0, 1: 0.6, 2: 0.25}.get(h.capex_class, 0.6)
        label = {0: "настройка существующего оборудования (недели)",
                 1: "модернизация/замена узла (месяцы)",
                 2: "новый передел (год и более)"}.get(h.capex_class, "")
        scores["feasibility"] = {"score": s, "explanation": f"Класс затрат: {label}"}

        # --- новизна
        sim_known = float(np.max(known_vecs @ h_vecs[i])) if len(known_vecs) else 0.0
        s = float(np.clip(1.0 - sim_known, 0.0, 1.0))
        nearest = KNOWN_SOLUTIONS[int(np.argmax(known_vecs @ h_vecs[i]))] \
            if len(known_vecs) else ""
        scores["novelty"] = {
            "score": round(s, 3),
            "explanation": (f"Максимальная близость к известным решениям фабрик: "
                            f"{sim_known:.2f} («{nearest[:80]}»)")}

        # --- обоснованность
        n_cit = len(h.citations)
        n_nodes = len(set(h.graph_nodes))
        s = min(1.0, 0.15 * n_cit + 0.06 * n_nodes + (0.2 if h.mechanism else 0))
        scores["evidence"] = {
            "score": round(s, 3),
            "explanation": (f"Источники: {n_cit} цитат из литературы; опора на "
                            f"{n_nodes} узлов графа знаний; механизм: "
                            f"{h.mechanism[:80] or '—'}")}

        # --- проверяемость
        has_criterion = any("критерий" in t.lower() for t in h.test_plan)
        s = min(1.0, 0.2 * len(h.test_plan) + (0.3 if has_criterion else 0))
        scores["testability"] = {
            "score": round(s, 3),
            "explanation": (f"План проверки: {len(h.test_plan)} шагов"
                            + ("; критерий успеха задан" if has_criterion
                               else "; критерий успеха не задан"))}

        # --- фидбэк экспертов
        # Порог 0.85 откалиброван по данным кейса: медиана близости ЛЮБЫХ двух
        # гипотез одного домена ~0.74, поэтому более низкий порог размазывал бы
        # одну оценку на весь список; >0.85 — действительно похожие формулировки.
        fb_adj = 0.0
        if len(fb_vecs):
            sims = fb_vecs @ h_vecs[i]
            fb_adj = float(np.sum(sims * fb_signs * (np.abs(sims) > 0.85))) * 0.1
            fb_adj = float(np.clip(fb_adj, -0.15, 0.15))
        if abs(fb_adj) > 1e-6:
            scores["feedback"] = {
                "score": round(fb_adj, 3),
                "explanation": "Поправка по оценкам экспертов на похожих гипотезах"}

        total = sum(w[k] * scores[k]["score"] for k in w) + fb_adj
        h.scores = {k: {**scores[k], "weight": w.get(k)} for k in scores}
        h.total_score = round(float(total), 4)

    hypotheses.sort(key=lambda h: -h.total_score)
    for r, h in enumerate(hypotheses, start=1):
        h.rank = r
    return hypotheses


def _main_metal(h: Hypothesis) -> str:
    return "Ni" if "Ni" in h.metal else "Cu"


def _expected_value_usd(h: Hypothesis) -> float:
    mid_t = (h.expected_effect["tonnes_min"] + h.expected_effect["tonnes_max"]) / 2
    price = config.METAL_PRICE_USD.get(_main_metal(h), 10000.0)
    return mid_t * price


def _embed_all(texts: list[str], llm: YandexLLM) -> np.ndarray:
    if not texts:
        return np.zeros((0, 256), dtype=np.float32)
    vecs = np.array(llm.embed_many(texts, kind="doc"), dtype=np.float32)
    return vecs / np.maximum(np.linalg.norm(vecs, axis=1, keepdims=True), 1e-9)


# ------------------------------------------------------------------ feedback
def _load_feedback(path: Path) -> list[dict]:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return []


def save_feedback(statement: str, verdict: str, comment: str = "",
                  path: Path = FEEDBACK_PATH) -> None:
    """verdict: 'accepted' | 'rejected'.

    На одну формулировку хранится одна актуальная оценка: повторная оценка
    той же гипотезы заменяет прежнюю (эксперт может передумать), поэтому
    противоречивых пар «принято+отклонено» в файле не бывает.
    """
    items = [f for f in _load_feedback(path) if f.get("statement") != statement]
    items.append({"statement": statement, "verdict": verdict, "comment": comment})
    path.write_text(json.dumps(items, ensure_ascii=False, indent=1),
                    encoding="utf-8")


def _feedback_vectors(feedback: list[dict], llm: YandexLLM):
    if not feedback:
        return np.zeros((0, 256), dtype=np.float32), np.zeros(0, dtype=np.float32)
    vecs = _embed_all([f["statement"] for f in feedback], llm)
    signs = np.array([1.0 if f["verdict"] == "accepted" else -1.0
                      for f in feedback], dtype=np.float32)
    return vecs, signs
