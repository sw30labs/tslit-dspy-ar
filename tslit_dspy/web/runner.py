"""Background job runner for the TSLIT-DSPy command deck.

One job at a time (single-writer guard). The HTTP layer polls job status;
the worker thread drives the actual DSPy evaluation / analysis / probe work.

The heavy DSPy stack is imported lazily inside the worker so the stdlib-only
server still boots and serves the SPA + docs even when dspy is absent.
"""

from __future__ import annotations

import json
import socket
import threading
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

__all__ = [
    "busy",
    "active_job_id",
    "get_job",
    "list_jobs",
    "live_events",
    "current_snapshot",
    "backend_reachable",
    "start_job",
]

from tslit_dspy.backends import DEFAULT_OMLX_URL, normalize_backend

_MAX_LIVE_EVENTS = 600
_MAX_JOBS = 25

_JOBS: dict[str, dict[str, Any]] = {}
_JOBS_LOCK = threading.Lock()
_BUSY = threading.Lock()

_LIVE: dict[str, Any] = {
    "snapshot": None,
    "events": deque(maxlen=_MAX_LIVE_EVENTS),
    "active_job_id": None,
    "last_params": None,
}
_LIVE_LOCK = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def busy() -> bool:
    return _BUSY.locked()


def active_job_id() -> str | None:
    with _LIVE_LOCK:
        return _LIVE.get("active_job_id")


def get_job(job_id: str) -> dict[str, Any] | None:
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        return dict(job) if job else None


def list_jobs(limit: int = 25) -> list[dict[str, Any]]:
    with _JOBS_LOCK:
        jobs = sorted(
            _JOBS.values(), key=lambda j: j.get("started_at") or "", reverse=True
        )
        return [dict(j) for j in jobs[:limit]]


def live_events(limit: int = 120) -> list[dict[str, Any]]:
    with _LIVE_LOCK:
        events = list(_LIVE["events"])
    return events[-limit:]


def current_snapshot() -> dict[str, Any] | None:
    with _LIVE_LOCK:
        snap = _LIVE.get("snapshot")
        return dict(snap) if isinstance(snap, dict) else None


def backend_reachable(
    backend: str = "omlx",
    base_url: str | None = None,
    timeout: float = 1.5,
) -> dict[str, Any]:
    """TCP-level reachability for the configured LLM endpoint."""
    try:
        name = normalize_backend(backend)
    except Exception as e:  # noqa: BLE001
        return {
            "reachable": False,
            "backend": backend,
            "base_url": base_url,
            "detail": str(e),
        }
    url = (base_url or DEFAULT_OMLX_URL) if name == "omlx" else (base_url or "")
    if not url:
        return {
            "reachable": False,
            "backend": name,
            "base_url": base_url,
            "detail": "base_url required for vllm",
        }
    parsed = urlparse(url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return {
                "reachable": True,
                "backend": name,
                "base_url": url,
                "host": host,
                "port": port,
            }
    except OSError as e:
        return {
            "reachable": False,
            "backend": name,
            "base_url": url,
            "host": host,
            "port": port,
            "detail": str(e),
        }


def start_job(params: dict[str, Any]) -> tuple[str | None, str | None]:
    """Launch a job. Returns ``(job_id, None)`` or ``(None, error)``."""
    kind = str(params.get("kind") or "").strip()
    if kind not in ("evaluate", "analyze", "probe"):
        return None, "kind must be one of: evaluate, analyze, probe"

    if not _BUSY.acquire(blocking=False):
        return None, "another job is still running — wait for it to finish"

    job_id = f"tslit-{uuid.uuid4().hex[:8]}"
    try:
        with _JOBS_LOCK:
            _JOBS[job_id] = {
                "id": job_id,
                "kind": kind,
                "status": "running",
                "started_at": _now(),
                "finished_at": None,
                "result": None,
                "error": None,
                "params": dict(params),
                "progress": {"stage": "starting", "note": "", "events": 0},
            }
            if len(_JOBS) > _MAX_JOBS:
                for old in sorted(_JOBS, key=lambda k: _JOBS[k]["started_at"])[
                    : len(_JOBS) - _MAX_JOBS
                ]:
                    if _JOBS[old]["status"] != "running":
                        del _JOBS[old]
        with _LIVE_LOCK:
            _LIVE["active_job_id"] = job_id
            _LIVE["last_params"] = dict(params)
            _LIVE["events"].clear()
        threading.Thread(
            target=_run_job, args=(job_id, dict(params)), daemon=True
        ).start()
    except BaseException:
        with _JOBS_LOCK:
            if job_id in _JOBS:
                _JOBS[job_id].update(
                    status="error", error="failed to start job thread",
                    finished_at=_now(),
                )
        _BUSY.release()
        raise
    return job_id, None


def _publish(job_id: str, entry: dict[str, Any]) -> None:
    with _LIVE_LOCK:
        _LIVE["events"].append(entry)
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if job is None:
            return
        progress = job.setdefault("progress", {})
        progress["events"] = int(progress.get("events") or 0) + 1
        if entry.get("kind") in ("stage.started", "stage.finished"):
            if entry.get("note"):
                progress["stage"] = entry["note"]
        if entry.get("kind") == "error":
            progress["note"] = f"ERROR: {entry.get('error', '?')}"
        elif entry.get("note"):
            progress["note"] = entry["note"]


def _event(kind: str, **kw: Any) -> dict[str, Any]:
    return {"kind": kind, "at": _now(), **kw}


def _run_job(job_id: str, params: dict[str, Any]) -> None:
    kind = params.get("kind")
    try:
        if kind == "evaluate":
            result = _run_evaluate(job_id, params)
        elif kind == "analyze":
            result = _run_analyze(job_id, params)
        elif kind == "probe":
            result = _run_probe(job_id, params)
        else:
            raise ValueError(f"unknown job kind: {kind}")
        status, error = "done", None
    except Exception as exc:  # noqa: BLE001
        result, status, error = None, "error", str(exc)
        _publish(job_id, _event("error", error=str(exc)))

    with _JOBS_LOCK:
        if job_id in _JOBS:
            _JOBS[job_id].update(
                status=status, result=result, error=error, finished_at=_now()
            )
    with _LIVE_LOCK:
        if _LIVE.get("active_job_id") == job_id:
            _LIVE["active_job_id"] = None
    _BUSY.release()


# --------------------------------------------------------------------------
# Job implementations
# --------------------------------------------------------------------------

def _load_records(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def _build_lm(model: str, base_url: str | None, backend: str) -> Any:
    from tslit_dspy.backends import api_key_for, omlx_lm

    if backend == "vllm":
        import dspy

        return dspy.LM(
            f"openai/{model}",
            api_base=base_url or "",
            api_key=api_key_for("vllm"),
            cache=False,
        )
    return omlx_lm(model, base_url)


def _run_evaluate(job_id: str, params: dict[str, Any]) -> dict[str, Any]:
    """Run zero-shot or compiled evaluation against a JSONL set."""
    import dspy

    from tslit_dspy.modules import TSLITAnalyzer
    from tslit_dspy.metrics import (
        evidence_quality_metrics,
        overall_accuracy,
        per_class_metrics,
    )
    from tslit_dspy.schemas import AnalysisResult

    data_path = Path(str(params["data_path"])).expanduser().resolve()
    compiled_path = params.get("compiled_path")
    compiled: Path | None = None
    if compiled_path:
        compiled = Path(str(compiled_path)).expanduser().resolve()
        if not compiled.exists():
            compiled = None

    model = str(params.get("model") or "DeepSeek-V4-Flash-0731-MLX")
    base_url = str(params.get("base_url") or "") or None
    backend = str(params.get("backend") or "omlx")

    _publish(job_id, _event("stage.started", note="configuring LM"))
    lm = _build_lm(model, base_url, backend)

    _publish(job_id, _event("stage.started", note=f"loading records from {data_path.name}"))
    records = _load_records(data_path)

    # Build dspy.Example ground-truth pairs.
    import dspy as _dspy

    examples = []
    for i, rec in enumerate(records):
        ex = _dspy.Example(
            response_text=rec.get("response_text", ""),
            probe_date=rec.get("probe_date", rec.get("virtual_time", "")),
            affiliation=rec.get("affiliation", ""),
            scenario_type=rec.get("scenario_type", rec.get("scenario", "")),
            detector_flags=rec.get("detector_flags", []),
            baseline_response=rec.get("baseline_response", ""),
            threat_category=rec.get("threat_category", "none"),
            risk_score_range=rec.get("risk_score_range", [0, 100]),
            example_id=rec.get("example_id", f"ex-{i}"),
        )
        examples.append(ex)

    predictions: list[AnalysisResult] = []
    per_example = []
    failures = 0
    n = len(examples)
    _publish(job_id, _event("stage.started", note=f"running {n} examples"))

    with dspy.context(lm=lm):
        analyzer = TSLITAnalyzer()
        is_optimized = False
        if compiled and compiled.exists():
            analyzer.load(str(compiled))
            is_optimized = True

        for i, ex in enumerate(examples):
            record = {
                "response_text": ex.response_text,
                "probe_date": ex.probe_date,
                "affiliation": ex.affiliation,
                "scenario_type": ex.scenario_type,
                "detector_flags": ex.detector_flags,
                "baseline_response": ex.baseline_response,
                "scenario": ex.scenario_type,
            }
            try:
                result = analyzer(record=record)
                predictions.append(result)
                from tslit_dspy.metrics import tslit_metric

                score = tslit_metric(ex, result)
                per_example.append({
                    "example_id": ex.example_id,
                    "gt_category": ex.threat_category,
                    "pred_category": result.final_category,
                    "score": round(score, 4),
                    "risk_score": result.risk_score,
                    "evidence_count": len(result.evidence_spans),
                    "evidence_spans": result.evidence_spans,
                    "qa_valid": result.qa_valid,
                    "reasoning": (result.reasoning or "")[:240],
                })
                _publish(job_id, _event(
                    "progress",
                    note=f"[{i+1}/{n}] {ex.example_id}: "
                         f"gt={ex.threat_category} pred={result.final_category}",
                ))
            except Exception as exc:  # noqa: BLE001
                failures += 1
                predictions.append(AnalysisResult(
                    scenario="error", probe_date=ex.probe_date,
                    affiliation=ex.affiliation, threat_category="none",
                    reasoning=f"error: {exc}", final_category="none",
                ))
                per_example.append({
                    "example_id": ex.example_id, "gt_category": ex.threat_category,
                    "pred_category": "none", "score": 0.0, "error": str(exc),
                })
                _publish(job_id, _event("error", error=str(exc)))

    class_metrics = per_class_metrics(examples, predictions)
    ev_metrics = evidence_quality_metrics(examples, predictions)
    acc = overall_accuracy(examples, predictions)
    qa_passes = sum(1 for p in predictions if isinstance(p, AnalysisResult) and p.qa_valid)
    composite_scores = [s.get("score", 0.0) for s in per_example]
    mean_composite = sum(composite_scores) / max(1, len(composite_scores))

    snapshot = {
        "kind": "evaluate",
        "mode": "MIPROv2 Optimized" if is_optimized else "Zero-Shot Baseline",
        "model": model,
        "backend": backend,
        "dataset": data_path.name,
        "n_examples": n,
        "failures": failures,
        "accuracy": round(acc, 4),
        "mean_composite": round(mean_composite, 4),
        "qa_pass_rate": round(qa_passes / max(1, n), 4),
        "class_metrics": class_metrics,
        "evidence_metrics": ev_metrics,
        "per_example": per_example,
        "compiled": str(compiled) if is_optimized else None,
    }
    with _LIVE_LOCK:
        _LIVE["snapshot"] = snapshot
    _publish(job_id, _event("run.finished",
                            note=f"accuracy={acc:.3f} composite={mean_composite:.3f}"))
    return snapshot


def _run_analyze(job_id: str, params: dict[str, Any]) -> dict[str, Any]:
    """Analyze a batch of NDJSON records via the TSLITAnalyzer (probe campaign)."""
    import dspy

    from tslit_dspy.modules import TSLITAnalyzer
    from tslit_dspy.schemas import ThreatReport

    compiled_path = params.get("compiled_path")
    compiled: Path | None = None
    if compiled_path:
        compiled = Path(str(compiled_path)).expanduser().resolve()
        if not compiled.exists():
            compiled = None

    model = str(params.get("model") or "DeepSeek-V4-Flash-0731-MLX")
    base_url = str(params.get("base_url") or "") or None
    backend = str(params.get("backend") or "omlx")

    records = params.get("records")
    if isinstance(records, str):
        records = json.loads(records)
    if not isinstance(records, list):
        raise ValueError("records must be a JSON array")
    if not records:
        raise ValueError("no records provided")

    _publish(job_id, _event("stage.started", note="configuring LM"))
    lm = _build_lm(model, base_url, backend)

    results = []
    n = len(records)
    with dspy.context(lm=lm):
        analyzer = TSLITAnalyzer()
        if compiled and compiled.exists():
            analyzer.load(str(compiled))

        for i, rec in enumerate(records):
            try:
                result = analyzer(record=rec)
                results.append(result)
                _publish(job_id, _event(
                    "progress",
                    note=f"[{i+1}/{n}] {rec.get('example_id', rec.get('probe_id', '?'))}: "
                         f"{result.final_category} risk={result.risk_score}",
                ))
            except Exception as exc:  # noqa: BLE001
                results.append(None)
                _publish(job_id, _event("error", error=str(exc)))

    report = ThreatReport(model_names=[model], results=[r for r in results if r])
    snapshot = {
        "kind": "analyze",
        "model": model,
        "backend": backend,
        "n_records": n,
        "n_success": len([r for r in results if r]),
        "n_threats": report.total_threats_found,
        "results": [r.to_dict() if r else None for r in results],
        "summary": report.to_analyst_findings()["summary"],
        "recommendations": report.to_analyst_findings()["recommendations"],
        "text_report": report.generate_text_report(),
    }
    with _LIVE_LOCK:
        _LIVE["snapshot"] = snapshot
    _publish(job_id, _event(
        "run.finished",
        note=f"{report.total_threats_found} confirmed threats from {n} records",
    ))
    return snapshot


def _run_probe(job_id: str, params: dict[str, Any]) -> dict[str, Any]:
    """Analyze a single response snippet (interactive probe)."""
    import dspy

    from tslit_dspy.modules import TSLITAnalyzer

    compiled_path = params.get("compiled_path")
    compiled: Path | None = None
    if compiled_path:
        compiled = Path(str(compiled_path)).expanduser().resolve()
        if not compiled.exists():
            compiled = None

    model = str(params.get("model") or "DeepSeek-V4-Flash-0731-MLX")
    base_url = str(params.get("base_url") or "") or None
    backend = str(params.get("backend") or "omlx")

    response_text = str(params.get("response_text") or "").strip()
    if not response_text:
        raise ValueError("response_text is required")
    record = {
        "response_text": response_text,
        "probe_date": str(params.get("probe_date") or "2026-08-07"),
        "affiliation": str(params.get("affiliation") or "unknown"),
        "scenario_type": str(params.get("scenario_type") or "probe"),
        "detector_flags": params.get("detector_flags") or "none",
        "baseline_response": str(params.get("baseline_response") or ""),
        "scenario": str(params.get("scenario") or "probe"),
        "example_id": "interactive-probe",
    }

    _publish(job_id, _event("stage.started", note="configuring LM"))
    lm = _build_lm(model, base_url, backend)

    with dspy.context(lm=lm):
        analyzer = TSLITAnalyzer()
        if compiled and compiled.exists():
            analyzer.load(str(compiled))
        result = analyzer(record=record)
    snapshot = {
        "kind": "probe",
        "model": model,
        "backend": backend,
        "result": result.to_dict(),
        "severity": result.severity,
    }
    with _LIVE_LOCK:
        _LIVE["snapshot"] = snapshot
    _publish(job_id, _event(
        "run.finished",
        note=f"{result.final_category} · risk {result.risk_score} · "
             f"qa_valid={result.qa_valid}",
    ))
    return snapshot