"""Диагностика отчёта по хвостам: интерпретируемые правила → сигналы потерь.

Каждый сигнал:
- квантифицирован (тонны металла, % от потерь потока);
- привязан к узлам графа знаний и рычагам воздействия;
- имеет человекочитаемое объяснение «почему система так считает».

Это правило-based слой: он полностью прозрачен и валидируется экспертом.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict

from .tailings import TailingsReport, TailingsStream, SizeClass


@dataclass
class Signal:
    id: str
    title: str
    explanation: str          # почему сигнал сработал (с числами)
    stream: str               # породные | пирротиновые
    metal: str                # Ni | Cu | Ni+Cu
    tonnes: float             # потери металла, охваченные сигналом, т/период
    share_of_stream_pct: float
    graph_nodes: list[str] = field(default_factory=list)  # узлы графа знаний
    levers: list[str] = field(default_factory=list)       # id рычагов из онтологии
    size_classes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------- helpers
def _mineral_t(sc: SizeClass, pattern: str, metal: str) -> float:
    total = 0.0
    for m in sc.minerals:
        if re.search(pattern, m.name, re.I):
            total += (m.ni_t if metal == "Ni" else m.cu_t) or 0
    return total


def _coarse(sc: SizeClass) -> bool:
    return sc.label.startswith("+") or sc.label.startswith("-125")


def _fine(sc: SizeClass) -> bool:
    return sc.label in ("-10", "-20 +10")


# --------------------------------------------------------------- rules
def diagnose(report: TailingsReport) -> list[Signal]:
    signals: list[Signal] = []
    for stream in report.streams:
        if "сводно" in stream.name:
            continue  # работаем с конкретными потоками, не с суммой
        signals.extend(_diagnose_stream(stream))
    signals.sort(key=lambda s: -s.tonnes)
    return signals


def _diagnose_stream(s: TailingsStream) -> list[Signal]:
    out: list[Signal] = []
    total_ni = s.ni_t or sum(c.ni_t or 0 for c in s.classes) or 1e-9
    total_cu = s.cu_t or sum(c.cu_t or 0 for c in s.classes) or 1e-9
    sid = "por" if "пород" in s.name else "pyr"

    # 1) Закрытые сростки в крупных классах → недораскрытие
    for metal, total in (("Ni", total_ni), ("Cu", total_cu)):
        t = sum(_mineral_t(c, r"закрыт", metal) for c in s.classes if _coarse(c))
        classes = [c.label for c in s.classes
                   if _coarse(c) and _mineral_t(c, r"закрыт", metal) > 0.02 * total]
        if t > 0.08 * total:
            out.append(Signal(
                id=f"{sid}_locked_coarse_{metal.lower()}",
                title=f"Закрытые сростки {metal} в крупных классах (недораскрытие)",
                explanation=(
                    f"В потоке «хвосты {s.name}» {t:,.0f} т {metal} "
                    f"({t / total * 100:.0f}% потерь потока) находится в закрытых "
                    f"сростках Pnt/Cp крупных классов ({', '.join(classes)} мкм). "
                    f"Сростки не раскрыты измельчением: сульфидное зерно скрыто в "
                    f"породе и не контактирует с реагентами. Потенциально извлекаемы "
                    f"после доизмельчения."),
                stream=s.name, metal=metal, tonnes=t,
                share_of_stream_pct=t / total * 100,
                graph_nodes=["pnt_locked", "mech_liberation", "grinding",
                             "classification"],
                levers=["regrind_cycle", "fine_screening", "cyclone_tuning",
                        "liner_geometry", "ball_charge", "classifier_replace"],
                size_classes=classes))

    # 2) Раскрытые зёрна в тонких классах → ошламование / кинетика флотации
    for metal, total in (("Ni", total_ni), ("Cu", total_cu)):
        t = sum(_mineral_t(c, r"раскрыт", metal) for c in s.classes if _fine(c))
        classes = [c.label for c in s.classes
                   if _fine(c) and _mineral_t(c, r"раскрыт", metal) > 0.02 * total]
        if t > 0.08 * total:
            out.append(Signal(
                id=f"{sid}_open_fines_{metal.lower()}",
                title=f"Раскрытый {metal} в шламах (потери флотации тонких классов)",
                explanation=(
                    f"В потоке «хвосты {s.name}» {t:,.0f} т {metal} "
                    f"({t / total * 100:.0f}% потерь потока) — уже раскрытые зёрна "
                    f"Pnt/Cp в тонких классах ({', '.join(classes)} мкм). Металл "
                    f"раскрыт, но флотация его не извлекла: тонкие частицы имеют "
                    f"малую вероятность закрепления на пузырьке, поверхность легко "
                    f"окисляется. Признак переизмельчения и/или недостаточной "
                    f"кинетики флотации."),
                stream=s.name, metal=metal, tonnes=t,
                share_of_stream_pct=t / total * 100,
                graph_nodes=["pnt_open", "mech_overgrind", "mech_kinetics",
                             "flotation_scav", "reagent_regime"],
                levers=["reagent_optimization", "flot_time_redistribution",
                        "scavenger_boost", "contact_tanks", "pulp_density_tuning",
                        "cyclone_tuning"],
                size_classes=classes))

    # 3) Раскрытые зёрна в крупных/средних классах → недоизвлечение флотацией
    for metal, total in (("Ni", total_ni), ("Cu", total_cu)):
        t = sum(_mineral_t(c, r"раскрыт", metal) for c in s.classes
                if not _fine(c))
        classes = [c.label for c in s.classes
                   if not _fine(c) and _mineral_t(c, r"раскрыт", metal) > 0.02 * total]
        if t > 0.08 * total:
            out.append(Signal(
                id=f"{sid}_open_coarse_{metal.lower()}",
                title=f"Раскрытый {metal} в крупных/средних классах не извлечён флотацией",
                explanation=(
                    f"{t:,.0f} т {metal} ({t / total * 100:.0f}% потерь потока "
                    f"«хвосты {s.name}») — раскрытые зёрна в классах "
                    f"{', '.join(classes)} мкм. Крупные раскрытые частицы флотируются "
                    f"плохо из-за большой массы (отрыв от пузырька) — кандидат на "
                    f"гравитационное доизвлечение или усиление собирателя."),
                stream=s.name, metal=metal, tonnes=t,
                share_of_stream_pct=t / total * 100,
                graph_nodes=["pnt_open", "gravity_sep", "mech_surface"],
                levers=["gravity_circuit", "reagent_optimization",
                        "scavenger_boost", "tailings_reflotation"],
                size_classes=classes))

    # 4) Примесь Ni в пирротине
    t = sum(_mineral_t(c, r"пирротин", "Ni") for c in s.classes)
    if t > 0.08 * total_ni:
        out.append(Signal(
            id=f"{sid}_po_admixture",
            title="Изоморфная примесь Ni в пирротине",
            explanation=(
                f"{t:,.0f} т Ni ({t / total_ni * 100:.0f}% потерь потока «хвосты "
                f"{s.name}») связано в решётке пирротина. Флотацией эта форма не "
                f"отделяется от пирротина; вариант — магнитная сепарация пирротина "
                f"в отдельный продукт с последующей переработкой (или "
                f"гидрометаллургия)."),
            stream=s.name, metal="Ni", tonnes=t,
            share_of_stream_pct=t / total_ni * 100,
            graph_nodes=["po_admix", "pyrrhotite", "magnetic_sep", "mech_magnetic"],
            levers=["magnetic_separation", "hydromet_leach"],
            size_classes=[c.label for c in s.classes
                          if _mineral_t(c, r"пирротин", "Ni") > 0.02 * total_ni]))

    # 5) Миллерит
    t = sum(_mineral_t(c, r"миллерит", "Ni") for c in s.classes)
    if t > 0.03 * total_ni:
        out.append(Signal(
            id=f"{sid}_millerite",
            title="Потери Ni с миллеритом",
            explanation=(
                f"{t:,.0f} т Ni ({t / total_ni * 100:.0f}% потерь потока) — "
                f"миллерит (NiS). Потенциально извлекаемый минерал: медленная "
                f"кинетика флотации, требует адаптации реагентного режима "
                f"(усиление собирателя, активация)."),
            stream=s.name, metal="Ni", tonnes=t,
            share_of_stream_pct=t / total_ni * 100,
            graph_nodes=["millerite", "reagent_regime", "mech_surface"],
            levers=["reagent_optimization", "flot_time_redistribution"],
            size_classes=[c.label for c in s.classes
                          if _mineral_t(c, r"миллерит", "Ni") > 0.01 * total_ni]))

    # 6) Переизмельчение: высокая доля класса -10 мкм
    fines_share = sum(c.share_pct or 0 for c in s.classes if c.label == "-10")
    fines_ni = sum(c.ni_t or 0 for c in s.classes if _fine(c))
    if fines_share > 22:
        out.append(Signal(
            id=f"{sid}_overgrinding",
            title="Высокая доля шламового класса -10 мкм (переизмельчение)",
            explanation=(
                f"Класс -10 мкм составляет {fines_share:.0f}% массы потока «хвосты "
                f"{s.name}» (типично 15–20%). В тонких классах сосредоточено "
                f"{fines_ni:,.0f} т Ni. Вероятная причина — переизмельчение: "
                f"неэффективная классификация возвращает готовый по крупности "
                f"материал в мельницы."),
            stream=s.name, metal="Ni+Cu", tonnes=fines_ni,
            share_of_stream_pct=fines_share,
            graph_nodes=["mech_overgrind", "classification", "hydrocyclone",
                         "circulating_load"],
            levers=["cyclone_tuning", "classifier_replace", "fine_screening",
                    "feed_granulometry", "ball_charge"],
            size_classes=["-10"]))

    # 7) Недоизмельчение: крупные классы с высокой долей массы и потерь
    coarse_share = sum(c.share_pct or 0 for c in s.classes if _coarse(c))
    coarse_ni = sum(c.ni_t or 0 for c in s.classes if _coarse(c))
    if coarse_share > 35 and coarse_ni > 0.3 * total_ni:
        out.append(Signal(
            id=f"{sid}_undergrinding",
            title="Высокая доля крупных классов в хвостах (недоизмельчение)",
            explanation=(
                f"Крупные классы (+45 мкм и выше) составляют {coarse_share:.0f}% "
                f"массы потока «хвосты {s.name}» и несут {coarse_ni:,.0f} т Ni "
                f"({coarse_ni / total_ni * 100:.0f}% потерь). Тонина помола "
                f"недостаточна для полного раскрытия."),
            stream=s.name, metal="Ni", tonnes=coarse_ni,
            share_of_stream_pct=coarse_ni / total_ni * 100,
            graph_nodes=["grind_fineness", "grinding", "mech_bypass",
                         "classification"],
            levers=["feed_granulometry", "liner_geometry", "ball_charge",
                    "regrind_cycle", "fine_screening"],
            size_classes=[c.label for c in s.classes if _coarse(c)]))

    # 8) Неизвлекаемые формы (силикаты) — потолок текущей технологии
    t = sum(_mineral_t(c, r"силикат|валлериит", "Ni") for c in s.classes)
    if t > 0.15 * total_ni:
        out.append(Signal(
            id=f"{sid}_silicate",
            title="Существенные потери Ni в неизвлекаемых формах (силикаты/валлериит)",
            explanation=(
                f"{t:,.0f} т Ni ({t / total_ni * 100:.0f}% потерь потока) — "
                f"силикатная форма и валлериит, текущей флотационной технологией "
                f"не извлекаются. Это ограничивает достижимый эффект настройки "
                f"флотации; долгосрочный вариант — гидрометаллургия."),
            stream=s.name, metal="Ni", tonnes=t,
            share_of_stream_pct=t / total_ni * 100,
            graph_nodes=["silicate", "kpi_ni_loss"],
            levers=["hydromet_leach"],
            size_classes=[]))

    return out


def diagnostics_summary(signals: list[Signal]) -> str:
    lines = []
    for s in signals:
        lines.append(f"- [{s.stream}] {s.title}: {s.tonnes:,.0f} т {s.metal} "
                     f"({s.share_of_stream_pct:.0f}%)")
    return "\n".join(lines)
