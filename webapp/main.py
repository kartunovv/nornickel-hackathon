"""Веб-интерфейс «Фабрики гипотез» (FastAPI).

    uvicorn webapp.main:app --reload
"""
from __future__ import annotations

import json
import shutil
import threading
import uuid
from pathlib import Path

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse,
                               Response)
from fastapi.staticfiles import StaticFiles

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hypofactory import config
from hypofactory.export import (export_csv, export_docx, export_html,
                                export_tasks_json)
from hypofactory.pipeline import (GRAPH_PATH, PipelineResult, load_kb,
                                  run_pipeline)
from hypofactory.ranker import rank, save_feedback
from hypofactory.llm import YandexLLM

BASE = Path(__file__).resolve().parent
UPLOADS = config.ARTIFACTS_DIR / "uploads"
UPLOADS.mkdir(exist_ok=True)

app = FastAPI(title="Фабрика гипотез")
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")

# ------------------------------------------------------------------ jobs
JOBS: dict[str, dict] = {}
RESULTS: dict[str, PipelineResult] = {}


def _run_job(job_id: str, xlsx: Path, goal: str, constraints: str,
             weights: dict, max_signals: int, per_signal: int) -> None:
    job = JOBS[job_id]

    def progress(stage, done, total):
        job["stage"] = stage
        job["done"] = done
        job["total"] = total

    try:
        result = run_pipeline(xlsx, goal=goal, constraints=constraints,
                              weights=weights, max_signals=max_signals,
                              per_signal=per_signal, progress_cb=progress)
        RESULTS[job_id] = result
        job["status"] = "done"
    except Exception as e:
        job["status"] = "error"
        job["error"] = str(e)


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (BASE / "templates" / "index.html").read_text(encoding="utf-8")


@app.get("/api/examples")
def examples() -> JSONResponse:
    base = config.DATA_DIR / "task1"
    items = []
    if base.exists():
        for p in sorted(base.rglob("Хвосты*.xlsx")):
            items.append({"name": p.stem,
                          "path": p.relative_to(config.PROJECT_ROOT).as_posix()})
    return JSONResponse(items)


@app.post("/api/run")
async def api_run(
    file: UploadFile | None = File(None),
    example_path: str = Form(""),
    goal: str = Form(""),
    constraints: str = Form("нет"),
    weights: str = Form("{}"),
    max_signals: int = Form(6),
    per_signal: int = Form(3),
) -> JSONResponse:
    if file is not None and file.filename:
        dest = UPLOADS / f"{uuid.uuid4().hex[:8]}_{file.filename}"
        with dest.open("wb") as f:
            shutil.copyfileobj(file.file, f)
        xlsx = dest
    elif example_path:
        xlsx = config.PROJECT_ROOT / example_path
        if not xlsx.exists():
            return JSONResponse({"error": "пример не найден"}, status_code=400)
    else:
        return JSONResponse({"error": "не передан файл отчёта"}, status_code=400)

    job_id = uuid.uuid4().hex[:12]
    JOBS[job_id] = {"status": "running", "stage": "Старт", "done": 0, "total": 0}
    threading.Thread(
        target=_run_job,
        args=(job_id, xlsx, goal, constraints, json.loads(weights or "{}"),
              max_signals, per_signal),
        daemon=True).start()
    return JSONResponse({"job_id": job_id})


@app.get("/api/status/{job_id}")
def api_status(job_id: str) -> JSONResponse:
    job = JOBS.get(job_id)
    if not job:
        return JSONResponse({"error": "нет такой задачи"}, status_code=404)
    payload = dict(job)
    if job.get("status") == "done":
        payload["result"] = RESULTS[job_id].to_dict()
    return JSONResponse(payload)


@app.post("/api/rerank/{job_id}")
async def api_rerank(job_id: str, weights: dict) -> JSONResponse:
    """Пересчёт рейтинга с новыми весами (без повторной генерации)."""
    result = RESULTS.get(job_id)
    if not result:
        return JSONResponse({"error": "нет результата"}, status_code=404)
    llm = YandexLLM()
    result.hypotheses = rank(result.hypotheses, llm, weights=weights)
    return JSONResponse(result.to_dict())


@app.post("/api/feedback")
async def api_feedback(payload: dict) -> JSONResponse:
    save_feedback(payload.get("statement", ""),
                  payload.get("verdict", "rejected"),
                  payload.get("comment", ""))
    return JSONResponse({"ok": True})


@app.get("/api/graph")
def api_graph(nodes: str = "") -> JSONResponse:
    graph, _ = load_kb()
    if nodes:
        ns, es = graph.subgraph(nodes.split(","), depth=2, max_nodes=40)
    else:
        ns, es = list(graph.nodes.values()), graph.edges
        if len(ns) > 55:  # для обзора — только окрестность KPI, иначе каша
            ns, es = graph.subgraph(
                ["kpi_ni_loss", "kpi_cu_loss", "kpi_recovery"], depth=3,
                max_nodes=55)
    return JSONResponse({
        "nodes": [{"id": n.id, "label": n.label, "type": n.type, "desc": n.desc}
                  for n in ns],
        "edges": [{"src": e.src, "rel": e.rel, "dst": e.dst,
                   "sources": e.sources} for e in es],
        "stats": graph.stats()})


@app.get("/api/export/{job_id}/{fmt}")
def api_export(job_id: str, fmt: str):
    result = RESULTS.get(job_id)
    if not result:
        return JSONResponse({"error": "нет результата"}, status_code=404)
    if fmt == "docx":
        return Response(
            export_docx(result),
            media_type=("application/vnd.openxmlformats-officedocument"
                        ".wordprocessingml.document"),
            headers={"Content-Disposition":
                     'attachment; filename="hypotheses_report.docx"'})
    if fmt == "html":
        return Response(export_html(result).encode("utf-8"),
                        media_type="text/html; charset=utf-8",
                        headers={"Content-Disposition":
                                 'attachment; filename="hypotheses_report.html"'})
    if fmt == "csv":
        return Response(export_csv(result).encode("utf-8-sig"),
                        media_type="text/csv",
                        headers={"Content-Disposition":
                                 'attachment; filename="hypotheses.csv"'})
    if fmt == "tasks":
        return Response(export_tasks_json(result).encode("utf-8"),
                        media_type="application/json",
                        headers={"Content-Disposition":
                                 'attachment; filename="tasks.json"'})
    if fmt == "json":
        return JSONResponse(result.to_dict())
    return JSONResponse({"error": "неизвестный формат"}, status_code=400)
