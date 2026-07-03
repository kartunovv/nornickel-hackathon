"""CLI: сквозной прогон конвейера на отчёте по хвостам (без веб-интерфейса).

    python scripts/run_pipeline.py "data/task1/Пример 1/Хвосты КГМК.xlsx"

Результат: консольная сводка + файлы рядом с --out:
    .html  — автономный отчёт (открыть в браузере, сервер не нужен)
    .docx  — бизнес-отчёт
    .csv   — таблица гипотез
    _tasks.json — задачи для импорта в Jira/YouTrack
    .json  — полные данные
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hypofactory.export import (export_csv, export_docx, export_html,
                                export_tasks_json)
from hypofactory.pipeline import run_pipeline, save_result


def main() -> None:
    ap = argparse.ArgumentParser(description="Фабрика гипотез (CLI)")
    ap.add_argument("xlsx", help="путь к отчёту по хвостам (.xlsx)")
    ap.add_argument("--goal", default="", help="целевой KPI / проблема")
    ap.add_argument("--constraints", default="нет", help="ограничения")
    ap.add_argument("--max-signals", type=int, default=6,
                    help="сколько главных сигналов диагностики прорабатывать")
    ap.add_argument("--per-signal", type=int, default=3,
                    help="гипотез на сигнал")
    ap.add_argument("--out", default="artifacts/result",
                    help="базовое имя выходных файлов")
    args = ap.parse_args()

    def progress(stage, done, total):
        suffix = f" {done}/{total}" if total else ""
        print(f"  [{stage}]{suffix}")

    result = run_pipeline(args.xlsx, goal=args.goal,
                          constraints=args.constraints,
                          max_signals=args.max_signals,
                          per_signal=args.per_signal,
                          progress_cb=progress)

    rep = result.report
    print("\n" + "=" * 72)
    print(f"Фабрика: {rep.plant} | отвальные хвосты: {rep.tailings_smt or 0:,.0f} СМТ | "
          f"потери Ni {rep.tailings_ni_t or 0:,.0f} т, Cu {rep.tailings_cu_t or 0:,.0f} т")
    print(f"Время: {result.elapsed_sec:.0f} c | сигналов: {len(result.signals)} | "
          f"гипотез: {len(result.hypotheses)}")

    print("\n--- Диагностика (главные сигналы потерь) ---")
    for s in result.signals[:args.max_signals]:
        print(f"  * {s.title}")
        print(f"    {s.tonnes:,.0f} т {s.metal} = {s.share_of_stream_pct:.0f}% "
              f"потерь потока «хвосты {s.stream}»")

    print("\n--- Гипотезы (по убыванию рейтинга) ---")
    for h in result.hypotheses:
        crits = " ".join(f"{k}={v['score']:.2f}" for k, v in h.scores.items())
        print(f"  №{h.rank} [{h.total_score:.2f}] {h.statement}")
        print(f"      критерии: {crits}")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    save_result(result, out.with_suffix(".json"))
    out.with_suffix(".html").write_text(export_html(result), encoding="utf-8")
    out.with_suffix(".docx").write_bytes(export_docx(result))
    out.with_suffix(".csv").write_text(export_csv(result), encoding="utf-8-sig")
    out.with_name(out.name + "_tasks.json").write_text(
        export_tasks_json(result), encoding="utf-8")
    print(f"\nФайлы: {out}.html (открыть в браузере) / .docx / .csv / "
          f"_tasks.json / .json")


if __name__ == "__main__":
    main()
